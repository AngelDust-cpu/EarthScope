"""Regional climate trend map for EarthScope 2.5 (docs/REGIONAL-MAP-API.md).

This module owns the request-level behaviour of ``GET /api/maps/regions`` and
``GET /api/maps/trends``.  It never talks to NASA: one regional layer comes
from the Data-owned helper, and everything else is arithmetic over that one
in-memory payload.

Data-owned boundary
-------------------
``backend/app/regional_data.py`` (Data role) must expose::

    load_regional_cells(
        region_id: str,
        parameter: str,
        start_year: int = 1990,
        end_year: int = 2025,
    ) -> dict

returning already deduplicated cells for the requested region, parameter and
inclusive year span, plus provenance.  The canonical return value is::

    {
        "region_id": "kg-surroundings",
        "parameter": "T2M",                 # or "PRECTOTCORR"
        "start_year": 1990,
        "end_year": 2025,
        "resolution": {                      # optional, validated if present
            "latitude_degrees": 0.5,
            "longitude_degrees": 0.625,
        },
        "cells": [
            {
                "latitude": 41.0,            # returned grid centre
                "longitude": 73.75,
                "elevation_m": 1200.0,       # may be None
                # NASA annual key per year: YYYY13 (temperature uses this)
                "annual": {1990: 6.8, ...},
                # calendar-month values: rate for T2M (deg C) and
                # PRECTOTCORR (mm/day), months 1..12
                "monthly": {1990: {1: 6.1, ..., 12: 7.4}, ...},
            },
            ...
        ],
        "source": {
            "name": "NASA POWER",
            "dataset": "MERRA-2",
            "api_version": "v2.10.0",
            "time_standard": "LST",
            "community": "AG",
            "request_urls": ["https://power.larc.nasa.gov/..."],
            "retrieved_at": "2026-11-14T12:34:56Z",
            "cached": False,
            "analysis_cached": False,
        },
    }

Tolerated variants, so a slightly different Data shape still works: cell keys
``lat``/``lon``/``elevation``; a flat ``values`` map keyed ``"YYYYMM"`` with
month ``13`` for the annual key (the raw NASA layout); numeric-string year
and month keys; a single ``request_url`` instead of ``request_urls``; and
``analysis_cached`` at the payload root.  Anything that could silently mix
products (wrong parameter, wrong region, non-LST source, conflicting duplicate
cell centres) is rejected as a data-contract error instead.

Product rules (docs/REGIONAL-MAP-API.md)
----------------------------------------
* Temperature: use NASA's ``YYYY13`` annual mean, and accept the year only
  when the annual key and all 12 monthly keys are finite and not ``-999``.
* Precipitation: ignore the annual daily-rate key; sum
  ``monthly_PRECTOTCORR (mm/day) * calendar_days_in_month`` over all 12 valid
  months.  February follows the Gregorian leap-year calendar.
* Missing stays missing: no interpolation, zero-fill, or cross-product
  substitution.
* OLS on calendar year gives a descriptive per-decade slope from five complete
  years.  A pointwise HAC(3) interval, raw p, BY q and HAC(1/3/5) sensitivity
  require at least 27 complete consecutive years covering the whole requested
  period; otherwise those fields stay null.  A gap anywhere in the requested
  period suppresses inference rather than silently dropping years.
* Benjamini-Yekutieli at q = 0.10 is applied once per response, i.e. per
  variable, period and regional cell family.

Cell footprints are display rectangles built from the observed native spacing
around the returned centres and clipped to the declared region rectangle.  They
are not separately supplied NASA measurements, and the response is capped at
209 features so one layer is always a bounded synchronous calculation.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import statistics
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from scipy import stats as scipy_stats

from . import analysis
from .power import PowerError
from .regional_schemas import (
    FDR_METHOD,
    FDR_Q,
    HAC_SENSITIVITY_LAGS,
    INFERENCE_METHOD,
    MIN_INFERENCE_YEARS,
    PRIMARY_HAC_LAGS,
    REGIONAL_MAX_CELLS,
    REGIONAL_YEAR_MAX,
    REGIONAL_YEAR_MIN,
    RegionalAnnualValue,
    RegionalBounds,
    RegionalCellFeature,
    RegionalCellProperties,
    RegionalGeometry,
    RegionalHacSensitivity,
    RegionalInference,
    RegionalMapResponse,
    RegionalMetadata,
    RegionalRegionInfo,
    RegionalRegionsResponse,
    RegionalResolution,
    RegionalSourceInfo,
    RegionalSummary,
    RegionalTrend,
    RegionalVariableSpec,
)
from .schemas import MIN_TREND_YEARS

__all__ = [
    "DEFAULT_REGION_ID",
    "MAX_CELLS",
    "MIN_INFERENCE_YEARS",
    "REGIONAL_YEAR_MAX",
    "REGIONAL_YEAR_MIN",
    "REGIONS",
    "REGIONS_BY_ID",
    "RegionalDataUnavailable",
    "benjamini_yekutieli",
    "build_regional_map",
    "list_regions",
    "load_cells",
]

MAX_CELLS = REGIONAL_MAX_CELLS
DEFAULT_REGION_ID = "kg-surroundings"
DEFAULT_VARIABLE = "temperature"
FILL_VALUE = -999.0
_ANALYSIS_BUILD_LOCKS = tuple(threading.Lock() for _ in range(32))


class RegionalDataUnavailable(RuntimeError):
    """The Data-owned ``app.regional_data`` module is not installed."""


@dataclass(frozen=True, slots=True)
class VariableSpec:
    variable: str
    parameter: str
    unit: str
    aggregation: str


@dataclass(frozen=True, slots=True)
class RegionSpec:
    """One demonstration region; the rectangle is a display extent."""

    region_id: str
    label: str
    south: float
    north: float
    west: float
    east: float

    @property
    def bounds(self) -> dict[str, float]:
        return {
            "south": self.south,
            "north": self.north,
            "west": self.west,
            "east": self.east,
        }


VARIABLE_SPECS: dict[str, VariableSpec] = {
    "temperature": VariableSpec("temperature", "T2M", "°C", "annual_mean"),
    "precipitation": VariableSpec(
        "precipitation",
        "PRECTOTCORR",
        "mm",
        "sum_monthly_daily_rate_times_days",
    ),
}

KG_SURROUNDINGS = RegionSpec(
    region_id="kg-surroundings",
    label="Kyrgyzstan and neighboring areas",
    south=39.0,
    north=44.0,
    west=69.0,
    east=81.0,
)
REGIONS: tuple[RegionSpec, ...] = (KG_SURROUNDINGS,)
REGIONS_BY_ID: dict[str, RegionSpec] = {spec.region_id: spec for spec in REGIONS}


def _regional_data() -> Any:
    """Return the Data-owned ``regional_data`` module.

    Imported lazily so this module, the routes and the rest of the app stay
    importable while ``backend/app/regional_data.py`` is being delivered.
    """

    try:
        from . import regional_data
    except ModuleNotFoundError as exc:
        if exc.name not in ("app.regional_data", "regional_data"):
            raise
        raise RegionalDataUnavailable(
            "backend/app/regional_data.py is not installed; the regional map "
            "layer is unavailable."
        ) from exc
    return regional_data


def load_cells(
    region_id: str,
    parameter: str,
    start_year: int,
    end_year: int,
) -> Any:
    """Call the Data-owned loader; keep the call signature explicit.

    Tests replace :func:`_regional_data` (the established pattern in
    :mod:`app.climate`) or this function, so no test reaches NASA.
    """

    module = _regional_data()
    loader = getattr(module, "load_regional_cells", None)
    if not callable(loader):
        raise RegionalDataUnavailable(
            "app.regional_data.load_regional_cells is missing; the regional "
            "map layer is unavailable."
        )
    try:
        loaded = loader(
            region_id, parameter, start_year=start_year, end_year=end_year
        )
        # The Data implementation returns (RegionalCell[], provenance),
        # whereas synthetic contract tests and older adapters may return the
        # equivalent mapping directly. Normalize both at this boundary.
        if isinstance(loaded, tuple) and len(loaded) == 2:
            raw_cells, source = loaded
            normalized_cells = []
            for cell in raw_cells:
                if isinstance(cell, Mapping):
                    normalized_cells.append(dict(cell))
                else:
                    normalized_cells.append({
                        "latitude": getattr(cell, "latitude", None),
                        "longitude": getattr(cell, "longitude", None),
                        "elevation_m": getattr(cell, "elevation_m", None),
                        "values": getattr(cell, "values", None),
                    })
            return {
                "region_id": region_id,
                "parameter": parameter,
                "cells": normalized_cells,
                "source": source,
            }
        return loaded
    except (analysis.DataContractError, RuntimeError):
        raise
    except Exception as exc:
        # Do not swallow programming errors from unrelated modules; the
        # regional loader's own domain error exposes its stable error code.
        if type(exc).__name__ == "RegionalError":
            failure = PowerError(str(exc), getattr(exc, "code", "POWER_UNAVAILABLE"))
            failure.retryable = bool(getattr(exc, "retryable", False))
            raise failure from exc
        raise


def list_regions() -> RegionalRegionsResponse:
    """Static capability document; never loads NASA data."""

    return RegionalRegionsResponse(
        regions=[
            RegionalRegionInfo(
                region_id=spec.region_id,
                region_label=spec.label,
                bounds=RegionalBounds(**spec.bounds),
                start_year=REGIONAL_YEAR_MIN,
                end_year=REGIONAL_YEAR_MAX,
                max_cells=MAX_CELLS,
                variables=[
                    RegionalVariableSpec(
                        variable=item.variable,
                        parameter=item.parameter,
                        unit=item.unit,
                        aggregation=item.aggregation,
                    )
                    for item in VARIABLE_SPECS.values()
                ],
            )
            for spec in REGIONS
        ]
    )


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CellInput:
    latitude: float
    longitude: float
    elevation_m: float | None
    annual: Mapping[int, Any]
    monthly: Mapping[int, Mapping[int, Any]]


@dataclass(frozen=True, slots=True)
class _CellYear:
    year: int
    value: float | None
    valid_months: int


def _number(value: Any) -> float | None:
    """A finite float, or None for anything that is not a real number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _measurement(value: Any, *, non_negative: bool = False) -> float | None:
    """A usable NASA measurement: finite, not the -999 fill, non-negative."""

    number = _number(value)
    if number is None:
        return None
    if math.isclose(number, FILL_VALUE, rel_tol=0.0, abs_tol=1e-9):
        return None
    if non_negative and number < 0.0:
        return None
    return number


def _integer_key(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _month_key(value: Any) -> int | None:
    month = _integer_key(value)
    return month if month is not None and 1 <= month <= 12 else None


def _indexed_by_year(raw: Any, label: str) -> dict[int, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError(f"{label} must be a year-keyed object")
    result: dict[int, Any] = {}
    for key, value in raw.items():
        year = _integer_key(key)
        if year is None:
            continue
        result[year] = value
    return result


def _monthly_map(raw: Any, year: int) -> dict[int, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError(
            f"monthly[{year}] must be a month-keyed object"
        )
    result: dict[int, Any] = {}
    for key, value in raw.items():
        month = _month_key(key)
        if month is not None:
            result[month] = value
    return result


def _flat_values(raw: Any) -> tuple[dict[int, Any], dict[int, dict[int, Any]]]:
    """Split a raw NASA ``YYYYMM`` map into annual (13) and monthly maps."""

    if not isinstance(raw, Mapping):
        raise analysis.DataContractError("values must be a YYYYMM-keyed object")
    annual: dict[int, Any] = {}
    monthly: dict[int, dict[int, Any]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        text = key.strip()
        if not text.isdigit() or len(text) != 6:
            continue
        year = int(text[:4])
        month = int(text[4:])
        if month == 13:
            annual[year] = value
        elif 1 <= month <= 12:
            monthly.setdefault(year, {})[month] = value
    return annual, monthly


def _cell_key(latitude: float, longitude: float) -> tuple[float, float]:
    # Grid centres sit at 0.5 deg (lat) and 0.625 deg (lon) in the verified
    # probe, so six decimals is far finer than the native spacing while still
    # collapsing float round-off between the two requested longitude tiles.
    return (round(latitude, 6), round(longitude, 6))


def _parse_cell(raw: Any) -> _CellInput:
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError(
            f"load_regional_cells returned a {type(raw).__name__} cell, "
            "expected an object"
        )
    latitude = _number(raw.get("latitude", raw.get("lat")))
    longitude = _number(raw.get("longitude", raw.get("lon", raw.get("lng"))))
    if latitude is None or not -90.0 <= latitude <= 90.0:
        raise analysis.DataContractError(
            "regional cell latitude is missing or invalid"
        )
    if longitude is None or not -180.0 <= longitude <= 180.0:
        raise analysis.DataContractError(
            "regional cell longitude is missing or invalid"
        )
    elevation = raw.get("elevation_m", raw.get("elevation"))
    elevation_m = _number(elevation) if elevation is not None else None

    annual = _indexed_by_year(raw.get("annual"), "annual")
    monthly_raw = _indexed_by_year(raw.get("monthly"), "monthly")
    monthly = {
        year: _monthly_map(values, year) for year, values in monthly_raw.items()
    }
    if not annual and not monthly and "values" in raw:
        annual, monthly = _flat_values(raw.get("values"))
    if not annual and not monthly and not any(
        key in raw for key in ("annual", "monthly", "values")
    ):
        raise analysis.DataContractError(
            "regional cell has no annual or monthly parameter values"
        )
    return _CellInput(
        latitude=latitude,
        longitude=longitude,
        elevation_m=elevation_m,
        annual=annual,
        monthly=monthly,
    )


def _declared_resolution(payload: Mapping[str, Any]) -> tuple[float | None, float | None]:
    raw = payload.get("resolution")
    if raw is None:
        return (None, None)
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError("resolution must be an object")
    latitude = _number(raw.get("latitude_degrees", raw.get("latitude")))
    longitude = _number(raw.get("longitude_degrees", raw.get("longitude")))
    for name, value in (
        ("resolution.latitude_degrees", latitude),
        ("resolution.longitude_degrees", longitude),
    ):
        if value is not None and value <= 0.0:
            raise analysis.DataContractError(f"{name} must be positive")
    return (latitude, longitude)


def _axis_spacing(
    values: Iterable[float],
    declared: float | None,
    axis: str,
) -> float:
    """Infer native spacing from returned centres; validate any declaration."""

    unique = sorted({round(value, 9) for value in values})
    differences = [
        later - earlier
        for earlier, later in zip(unique, unique[1:])
        if later - earlier > 1e-9
    ]
    inferred = statistics.median(differences) if differences else None
    if inferred is None:
        if declared is None:
            raise analysis.DataContractError(
                f"cannot infer {axis} grid spacing from the returned cell centres"
            )
        inferred = declared
    if not math.isfinite(inferred) or inferred <= 0.0:
        raise analysis.DataContractError(f"invalid {axis} grid spacing")
    if declared is not None and not math.isclose(
        inferred, declared, rel_tol=1e-3, abs_tol=1e-9
    ):
        raise analysis.DataContractError(
            f"declared {axis} spacing {declared:g} does not match the "
            f"spacing inferred from returned centres ({inferred:g})"
        )
    return float(inferred)


# ---------------------------------------------------------------------------
# Annual product rules
# ---------------------------------------------------------------------------


def _annual_series(
    cell: _CellInput,
    variable: str,
    start_year: int,
    end_year: int,
) -> list[_CellYear]:
    """Reduce one cell's monthly/annual maps to the requested annual series.

    No value is ever synthesised.  A year that fails the product rule keeps a
    null value plus the count of months that did pass, so the response can show
    why it was excluded.
    """

    precipitation = variable == "precipitation"
    series: list[_CellYear] = []
    for year in range(start_year, end_year + 1):
        months = cell.monthly.get(year, {})
        valid: dict[int, float] = {}
        for month in range(1, 13):
            value = _measurement(months.get(month), non_negative=precipitation)
            if value is not None:
                valid[month] = value

        value: float | None = None
        if len(valid) == 12:
            if precipitation:
                value = math.fsum(
                    valid[month] * calendar.monthrange(year, month)[1]
                    for month in range(1, 13)
                )
            else:
                annual = _measurement(cell.annual.get(year))
                if annual is not None:
                    value = annual
        series.append(
            _CellYear(year=year, value=value, valid_months=len(valid))
        )
    return series


def _max_gap_years(series: Sequence[_CellYear]) -> int:
    """Longest run of consecutive years without a value inside the span."""

    longest = current = 0
    for item in series:
        if item.value is None:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _quality(valid_years: int, expected_years: int) -> str:
    if valid_years == expected_years:
        return "complete"
    if valid_years < MIN_TREND_YEARS:
        return "insufficient_years"
    return "incomplete"


# ---------------------------------------------------------------------------
# Trend and multiple-comparison inference
# ---------------------------------------------------------------------------


def _fit_cell_trend(
    series: Sequence[_CellYear],
) -> dict[str, Any] | None:
    """Descriptive OLS slope plus guarded HAC inference for one cell.

    The OLS fit itself is the existing :func:`app.analysis.fit_trend`, so the
    per-decade slope is identical to every other EarthScope trend endpoint.
    HAC inference is added only when the requested span is fully complete and
    at least :data:`MIN_INFERENCE_YEARS` long; otherwise the returned trend
    carries the slope and null inferential fields.
    """

    aggregates = [
        analysis.AnnualAggregate(
            year=item.year,
            value=item.value,
            valid_days=item.valid_months,
            expected_days=12,
        )
        for item in series
    ]
    stats, fitted, _ = analysis.fit_trend(aggregates)
    if stats is None:
        return None

    trend: dict[str, Any] = {
        "per_decade": float(stats.per_decade),
        "ci95_low": None,
        "ci95_high": None,
        "p_value": None,
        "q_value_by": None,
        "significant_by": None,
        "hac_sensitivity": [],
    }

    complete = all(item.value is not None for item in series)
    if not complete or len(series) < MIN_INFERENCE_YEARS:
        return trend

    years = [float(item.year) for item in series]
    values = [float(item.value) for item in series]  # type: ignore[arg-type]
    fitted_values = [float(fitted[item.year]) for item in series]
    if not all(math.isfinite(value) for value in fitted_values):
        return trend

    standard_errors: dict[int, float] = {}
    for lags in HAC_SENSITIVITY_LAGS:
        error = analysis.hac_slope_standard_error(
            years, values, fitted_values, lags
        )
        if error is None:
            # A degenerate residual variance means no honest interval exists;
            # keep the descriptive slope only rather than inventing one.
            return trend
        standard_errors[lags] = error

    degrees_of_freedom = len(series) - 2
    t_critical = float(scipy_stats.t.ppf(0.975, degrees_of_freedom))
    slope_per_year = float(stats.per_decade) / 10.0

    def _p_value(error: float) -> float:
        statistic = abs(slope_per_year) / error
        if not math.isfinite(statistic):
            return 1.0
        p_value = float(
            2.0 * scipy_stats.t.sf(statistic, degrees_of_freedom)
        )
        return min(max(p_value, 0.0), 1.0)

    primary = standard_errors[PRIMARY_HAC_LAGS]
    trend.update(
        ci95_low=float((slope_per_year - t_critical * primary) * 10.0),
        ci95_high=float((slope_per_year + t_critical * primary) * 10.0),
        p_value=_p_value(primary),
        hac_sensitivity=[
            {"lags": lags, "p_value": _p_value(standard_errors[lags])}
            for lags in HAC_SENSITIVITY_LAGS
        ],
    )
    return trend


def benjamini_yekutieli(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Yekutieli adjusted q-values, returned in input order.

    ``q_i = min_{j >= i} ( p_(j) * m * H_m / j )`` with ``H_m`` the m-th
    harmonic number and p-values sorted ascending.  BY controls the false
    discovery rate under arbitrary dependence, which is why it is used for
    adjacent grid cells.  Values are capped at 1.0.
    """

    count = len(p_values)
    if count == 0:
        return []
    checked: list[float] = []
    for value in p_values:
        number = _number(value)
        if number is None or not 0.0 <= number <= 1.0:
            raise analysis.DataContractError("p-values must be finite and in [0, 1]")
        checked.append(number)

    harmonic = math.fsum(1.0 / rank for rank in range(1, count + 1))
    order = sorted(range(count), key=lambda index: (checked[index], index))
    adjusted = [0.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        candidate = checked[index] * count * harmonic / rank
        running = min(running, max(candidate, 0.0))
        adjusted[index] = min(running, 1.0)
    return adjusted


# ---------------------------------------------------------------------------
# GeoJSON assembly
# ---------------------------------------------------------------------------


def _cell_id(region_id: str, latitude: float, longitude: float) -> str:
    return f"{region_id}:{latitude:.4f}:{longitude:.4f}"


def _cell_polygon(
    latitude: float,
    longitude: float,
    latitude_spacing: float,
    longitude_spacing: float,
    bounds: RegionSpec,
) -> list[list[list[float]]] | None:
    """Display rectangle around one returned centre, clipped to the region."""

    west = max(bounds.west, longitude - longitude_spacing / 2.0)
    east = min(bounds.east, longitude + longitude_spacing / 2.0)
    south = max(bounds.south, latitude - latitude_spacing / 2.0)
    north = min(bounds.north, latitude + latitude_spacing / 2.0)
    if east <= west or north <= south:
        return None
    return [
        [
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
        ]
    ]


def _normalise_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise analysis.DataContractError("source.retrieved_at must be ISO 8601 UTC")
    stamp = value.strip()
    candidate = f"{stamp[:-1]}+00:00" if stamp.endswith(("Z", "z")) else stamp
    try:
        retrieved = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise analysis.DataContractError(
            "source.retrieved_at must be ISO 8601 UTC"
        ) from exc
    if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0):
        raise analysis.DataContractError("source.retrieved_at must be UTC")
    return retrieved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_info(payload: Mapping[str, Any]) -> RegionalSourceInfo:
    raw = payload.get("source")
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError(
            "load_regional_cells returned no source metadata"
        )

    name = raw.get("name")
    if not isinstance(name, str) or not name.upper().startswith("NASA POWER"):
        raise analysis.DataContractError("source.name must identify NASA POWER")

    urls = raw.get("request_urls")
    if urls is None and raw.get("request_url") is not None:
        urls = [raw.get("request_url")]
    if not isinstance(urls, Sequence) or isinstance(urls, (str, bytes)):
        raise analysis.DataContractError("source.request_urls must be a list")

    analysis_cached = raw.get(
        "analysis_cached", payload.get("analysis_cached", False)
    )
    try:
        return RegionalSourceInfo(
            name=name,
            dataset=raw.get("dataset"),
            api_version=raw.get("api_version", raw.get("version")),
            time_standard=raw.get("time_standard"),
            community=raw.get("community"),
            request_urls=list(urls),
            retrieved_at=_normalise_timestamp(raw.get("retrieved_at")),
            cached=bool(raw.get("cached", False)),
            analysis_cached=bool(analysis_cached),
        )
    except ValidationError as exc:
        raise analysis.DataContractError(
            f"source metadata does not satisfy the regional contract: {exc}"
        ) from exc


def build_regional_map(
    *,
    region_id: str,
    variable: str,
    start_year: int,
    end_year: int,
) -> RegionalMapResponse:
    """Return a processed-cache hit or build one regional map per cache key."""
    spec = REGIONS_BY_ID.get(region_id)
    variable_spec = VARIABLE_SPECS.get(variable)
    if spec is None or variable_spec is None:
        return _build_regional_map_uncached(
            region_id=region_id, variable=variable,
            start_year=start_year, end_year=end_year,
        )
    # Load/cache provenance first. The processed key includes the NASA source
    # retrieval stamp and request URLs, so replacing/refreshing raw source
    # tiles cannot leave an indefinitely stale derived layer in use.
    payload = load_cells(spec.region_id, variable_spec.parameter, start_year, end_year)
    if not isinstance(payload, Mapping):
        raise analysis.DataContractError("load_regional_cells must return an object")
    source = _source_info(payload)
    source_identity = json.dumps(
        {
            "retrieved_at": source.retrieved_at,
            "tile_retrieved_at": payload["source"].get("tile_retrieved_at"),
            "request_urls": source.request_urls,
            "dataset": source.dataset,
            "api_version": source.api_version,
        },
        sort_keys=True, separators=(",", ":"),
    )
    cache_key = hashlib.sha256(
        f"v3|{region_id}|{variable}|{start_year}|{end_year}|{source_identity}".encode("utf-8")
    ).hexdigest()
    path = _analysis_cache_path(cache_key)
    lock = _ANALYSIS_BUILD_LOCKS[int(cache_key[:4], 16) % len(_ANALYSIS_BUILD_LOCKS)]
    with lock:
        cached = _read_analysis_cache(path)
        if cached is not None:
            cached_source = source.model_copy(update={"analysis_cached": True})
            metadata = cached.metadata.model_copy(update={"source": cached_source})
            return cached.model_copy(update={"metadata": metadata})
        result = _build_regional_map_uncached(
            region_id=region_id, variable=variable,
            start_year=start_year, end_year=end_year,
            payload=payload,
        )
        _write_analysis_cache(path, result.model_dump(mode="json"))
        return result


def _analysis_cache_path(key: str) -> Path:
    configured = os.environ.get("EARTHSCOPE_CACHE_DIR")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / ".cache" / "earthscope"
    return root / "regional" / f"trend-map-v1-{key}.json"


def _read_analysis_cache(path: Path) -> RegionalMapResponse | None:
    try:
        return RegionalMapResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, ValidationError):
        return None


def _write_analysis_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    except OSError:
        return


def _build_regional_map_uncached(
    *,
    region_id: str,
    variable: str,
    start_year: int,
    end_year: int,
    payload: Mapping[str, Any] | None = None,
) -> RegionalMapResponse:
    """Load one regional layer and build the bounded GeoJSON response.

    Raises :class:`analysis.DataContractError` when the Data-owned payload
    breaks the documented contract, so the API returns one honest 502 rather
    than a partial map.  No per-cell fetch happens here or in the route.
    """

    spec = REGIONS_BY_ID.get(region_id)
    if spec is None:
        raise ValueError(f"unknown region: {region_id!r}")
    variable_spec = VARIABLE_SPECS.get(variable)
    if variable_spec is None:
        raise ValueError(f"unsupported variable: {variable!r}")
    if (
        start_year < REGIONAL_YEAR_MIN
        or end_year > REGIONAL_YEAR_MAX
        or start_year > end_year
    ):
        raise ValueError("regional year span is outside the supported range")

    if payload is None:
        payload = load_cells(
            spec.region_id, variable_spec.parameter, start_year, end_year
        )
    if not isinstance(payload, Mapping):
        raise analysis.DataContractError(
            "load_regional_cells must return an object"
        )

    if payload.get("region_id") not in (None, spec.region_id):
        raise analysis.DataContractError(
            f"load_regional_cells returned region {payload.get('region_id')!r} "
            f"for {spec.region_id!r}"
        )
    if payload.get("parameter") not in (None, variable_spec.parameter):
        raise analysis.DataContractError(
            f"load_regional_cells returned parameter {payload.get('parameter')!r} "
            f"for {variable_spec.parameter!r}"
        )

    raw_cells = payload.get("cells")
    if (
        isinstance(raw_cells, (str, bytes, Mapping))
        or not isinstance(raw_cells, Sequence)
        or not raw_cells
    ):
        raise analysis.DataContractError(
            "load_regional_cells returned no regional cells"
        )

    cells: list[_CellInput] = [_parse_cell(raw) for raw in raw_cells]

    # Defensive dedup in addition to the Data-owned one: identical centres from
    # adjacent tiles must collapse, and disagreement between them is an error,
    # not a silent last-writer-wins.
    selected: dict[tuple[float, float], _CellInput] = {}
    series_by_key: dict[tuple[float, float], list[_CellYear]] = {}
    for cell in sorted(cells, key=lambda item: (item.latitude, item.longitude)):
        key = _cell_key(cell.latitude, cell.longitude)
        series = _annual_series(cell, variable, start_year, end_year)
        if key in selected:
            if series_by_key[key] != series:
                raise analysis.DataContractError(
                    "duplicate regional cell centres disagree on their values"
                )
            if selected[key].elevation_m is None and cell.elevation_m is not None:
                # Keep the first returned centre exactly; only fill metadata.
                selected[key] = replace(selected[key], elevation_m=cell.elevation_m)
            continue
        selected[key] = cell
        series_by_key[key] = series

    if len(selected) > MAX_CELLS:
        raise analysis.DataContractError(
            f"load_regional_cells returned {len(selected)} unique cells; "
            f"the regional layer is limited to {MAX_CELLS}"
        )

    declared_latitude, declared_longitude = _declared_resolution(payload)
    latitude_spacing = _axis_spacing(
        (cell.latitude for cell in selected.values()),
        declared_latitude,
        "latitude",
    )
    longitude_spacing = _axis_spacing(
        (cell.longitude for cell in selected.values()),
        declared_longitude,
        "longitude",
    )

    computed: list[dict[str, Any]] = []
    for key, cell in selected.items():
        polygon = _cell_polygon(
            cell.latitude,
            cell.longitude,
            latitude_spacing,
            longitude_spacing,
            spec,
        )
        if polygon is None:
            continue  # centre lies outside the declared display rectangle
        series = series_by_key[key]
        computed.append(
            {
                "cell": cell,
                "series": series,
                "polygon": polygon,
                "trend": _fit_cell_trend(series),
            }
        )

    if not computed:
        raise analysis.DataContractError(
            "no regional cell centres fall inside the declared region"
        )

    tested = [
        index
        for index, item in enumerate(computed)
        if item["trend"] is not None and item["trend"]["p_value"] is not None
    ]
    adjusted = benjamini_yekutieli(
        [computed[index]["trend"]["p_value"] for index in tested]
    )
    for index, q_value in zip(tested, adjusted, strict=True):
        trend = computed[index]["trend"]
        trend["q_value_by"] = q_value
        trend["significant_by"] = bool(q_value <= FDR_Q)

    features: list[RegionalCellFeature] = []
    for item in computed:
        cell: _CellInput = item["cell"]
        series: list[_CellYear] = item["series"]
        valid_years = sum(1 for entry in series if entry.value is not None)
        trend_raw = item["trend"]
        trend = None
        if trend_raw is not None:
            trend = RegionalTrend(
                per_decade=trend_raw["per_decade"],
                ci95_low=trend_raw["ci95_low"],
                ci95_high=trend_raw["ci95_high"],
                p_value=trend_raw["p_value"],
                q_value_by=trend_raw["q_value_by"],
                significant_by=trend_raw["significant_by"],
                hac_sensitivity=[
                    RegionalHacSensitivity(**entry)
                    for entry in trend_raw["hac_sensitivity"]
                ],
            )
        cell_id = _cell_id(spec.region_id, cell.latitude, cell.longitude)
        features.append(
            RegionalCellFeature(
                id=cell_id,
                geometry=RegionalGeometry(coordinates=item["polygon"]),
                properties=RegionalCellProperties(
                    cell_id=cell_id,
                    latitude=cell.latitude,
                    longitude=cell.longitude,
                    elevation_m=cell.elevation_m,
                    quality=_quality(valid_years, len(series)),
                    valid_years=valid_years,
                    expected_years=len(series),
                    max_gap_years=_max_gap_years(series),
                    annual=[
                        RegionalAnnualValue(
                            year=entry.year,
                            value=entry.value,
                            valid_months=entry.valid_months,
                        )
                        for entry in series
                    ],
                    trend=trend,
                ),
            )
        )

    metadata = RegionalMetadata(
        region_id=spec.region_id,
        region_label=spec.label,
        bounds=RegionalBounds(**spec.bounds),
        variable=variable,  # type: ignore[arg-type]
        parameter=variable_spec.parameter,  # type: ignore[arg-type]
        unit=variable_spec.unit,  # type: ignore[arg-type]
        aggregation=variable_spec.aggregation,  # type: ignore[arg-type]
        start_year=start_year,
        end_year=end_year,
        resolution=RegionalResolution(
            latitude_degrees=latitude_spacing,
            longitude_degrees=longitude_spacing,
        ),
        source=_source_info(payload),
        inference=RegionalInference(
            method=INFERENCE_METHOD,
            hac_lags=PRIMARY_HAC_LAGS,  # type: ignore[arg-type]
            fdr=FDR_METHOD,  # type: ignore[arg-type]
            fdr_q=FDR_Q,
            tested_cells=len(tested),
        ),
        summary=RegionalSummary(
            total_cells=len(features),
            trend_cells=sum(1 for feature in features if feature.properties.trend),
            fdr_cells=sum(
                1
                for feature in features
                if feature.properties.trend is not None
                and feature.properties.trend.significant_by is True
            ),
        ),
    )

    try:
        return RegionalMapResponse(metadata=metadata, features=features)
    except ValidationError as exc:
        raise analysis.DataContractError(
            f"regional layer failed response validation: {exc}"
        ) from exc
