"""Anomaly and seasonal orchestration for the EarthScope 2.3 API.

This module owns the request-level behaviour of ``GET /api/anomalies`` and
``GET /api/seasonal``:

* exactly one contiguous daily POWER range per request, chosen so the existing
  exact-query cache in :mod:`app.power` is reused rather than re-fetched;
* response construction that reuses the existing annual aggregation, OLS fit
  (``app.analysis.fit_trend``) and source validation
  (``app.analysis._normalise_source``);
* explicit contract checks on the Data-owned helpers, so a malformed return
  value becomes a ``DataContractError`` (HTTP 502) instead of a partial or
  silently wrong response.

The arithmetic itself is owned by ``backend/app/climate_data.py`` (Data role),
which exposes the two functions documented in ``docs/CLIMATE-API.md``:

``anomaly_series(daily, variable, start_year, end_year, baseline_start_year,
baseline_end_year) -> tuple[list[dict], float | None, int]``

``aggregate_period(daily, variable, start_year, end_year, period) ->
list[AnnualAggregate]``

That module is imported lazily: the rest of the API stays importable if it has
not landed yet, and tests can substitute the agreed contract.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from . import analysis
from .schemas import (
    ALL_PERIOD,
    CLIMATE_NORMAL_YEARS,
    COVERAGE_MIN_YEAR,
    SEASONS,
    AnomalyPoint,
    AnomalyResponse,
    SeasonalPeriodEntry,
    SeasonalResponse,
    SeriesPoint,
)

__all__ = [
    "ClimateDataUnavailable",
    "anomaly_fetch_span",
    "build_anomaly_response",
    "build_seasonal_response",
    "seasonal_fetch_span",
]

# Winter needs the previous December, so a winter request reaches one year back
# when that year is inside the supported coverage.
_WINTER_PERIODS = frozenset({"winter", ALL_PERIOD})


class ClimateDataUnavailable(RuntimeError):
    """The Data-owned ``app.climate_data`` module is not installed."""


def _climate_data() -> Any:
    """Return the Data-owned ``climate_data`` module.

    Imported lazily so this module, the routes and the rest of the app stay
    importable while ``backend/app/climate_data.py`` is being delivered.
    """

    try:
        from . import climate_data
    except ModuleNotFoundError as exc:
        if exc.name not in ("app.climate_data", "climate_data"):
            raise
        raise ClimateDataUnavailable(
            "backend/app/climate_data.py is not installed; the climate "
            "analysis helpers are unavailable."
        ) from exc
    return climate_data


def anomaly_fetch_span(
    start_year: int,
    end_year: int,
    baseline_start_year: int,
    baseline_end_year: int,
) -> tuple[int, int]:
    """Union of the display and baseline spans: one contiguous daily request.

    The baseline may sit entirely before or after the display period, so the
    request covers the outer bounds and the cache key stays stable across
    requests that share the same union.
    """

    return (
        min(start_year, baseline_start_year),
        max(end_year, baseline_end_year),
    )


def seasonal_fetch_span(start_year: int, end_year: int, period: str) -> tuple[int, int]:
    """Daily span for one seasonal request.

    Only winter (and ``all``, which contains it) needs the previous December,
    and only when that year is inside the supported coverage.  Winter 1981
    therefore has no prior December and stays null.
    """

    if period in _WINTER_PERIODS and start_year - 1 >= COVERAGE_MIN_YEAR:
        return start_year - 1, end_year
    return start_year, end_year


def _coerce_aggregates(
    raw: Any,
    period: str,
    start_year: int,
    end_year: int,
) -> list[analysis.AnnualAggregate]:
    """Validate the Data-owned period aggregates against the internal contract."""

    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise analysis.DataContractError(
            f"aggregate_period returned {type(raw).__name__} for {period!r}, "
            "expected a sequence of AnnualAggregate"
        )

    aggregates: list[analysis.AnnualAggregate] = []
    for item in raw:
        if isinstance(item, analysis.AnnualAggregate):
            aggregates.append(item)
            continue
        if not isinstance(item, Mapping):
            raise analysis.DataContractError(
                f"aggregate_period returned {type(item).__name__} for {period!r}, "
                "expected AnnualAggregate"
            )
        try:
            aggregates.append(
                analysis.AnnualAggregate(
                    year=item["year"],
                    value=item["value"],
                    valid_days=item["valid_days"],
                    expected_days=item["expected_days"],
                )
            )
        except (KeyError, TypeError) as exc:
            raise analysis.DataContractError(
                f"aggregate_period returned an incomplete row for {period!r}"
            ) from exc

    years = [item.year for item in aggregates]
    if years != list(range(start_year, end_year + 1)):
        raise analysis.DataContractError(
            f"aggregate_period must return every requested year in ascending "
            f"order for {period!r}"
        )

    for item in aggregates:
        if isinstance(item.year, bool) or not isinstance(item.year, int):
            raise analysis.DataContractError(
                f"aggregate_period returned a non-integer year for {period!r}"
            )
        for name, count in (
            ("valid_days", item.valid_days),
            ("expected_days", item.expected_days),
        ):
            if isinstance(count, bool) or not isinstance(count, int):
                raise analysis.DataContractError(
                    f"aggregate_period returned a non-integer {name} for {period!r}"
                )
        if not 1 <= item.expected_days <= 366:
            raise analysis.DataContractError(
                f"aggregate_period returned expected_days={item.expected_days} "
                f"for {period!r}"
            )
        if not 0 <= item.valid_days <= item.expected_days:
            raise analysis.DataContractError(
                f"aggregate_period returned valid_days={item.valid_days} with "
                f"expected_days={item.expected_days} for {period!r}"
            )
        if item.value is not None:
            if isinstance(item.value, bool) or not isinstance(item.value, (int, float)):
                raise analysis.DataContractError(
                    f"aggregate_period returned a non-numeric value for {period!r}"
                )
            if not math.isfinite(float(item.value)):
                raise analysis.DataContractError(
                    f"aggregate_period returned a non-finite value for {period!r}"
                )
    return aggregates


def _winter_min_year_guard(
    aggregates: list[analysis.AnnualAggregate],
) -> list[analysis.AnnualAggregate]:
    """Keep winter 1981 null: its previous December is outside coverage."""

    if not any(
        item.year == COVERAGE_MIN_YEAR and item.value is not None
        for item in aggregates
    ):
        return aggregates
    return [
        analysis.AnnualAggregate(
            year=item.year,
            value=None if item.year == COVERAGE_MIN_YEAR else item.value,
            valid_days=item.valid_days,
            expected_days=item.expected_days,
        )
        for item in aggregates
    ]


def _series_points(
    aggregates: list[analysis.AnnualAggregate],
    fitted_by_year: Mapping[int, float],
) -> list[SeriesPoint]:
    try:
        return [
            SeriesPoint(
                year=item.year,
                value=item.value,
                valid_days=item.valid_days,
                expected_days=item.expected_days,
                fitted=fitted_by_year.get(item.year),
            )
            for item in aggregates
        ]
    except (ValidationError, TypeError, ValueError) as exc:
        raise analysis.DataContractError(
            f"period aggregates do not satisfy the series contract: {exc}"
        ) from exc


def build_anomaly_response(
    *,
    daily: Mapping[str, Any],
    source: Any,
    latitude: float,
    longitude: float,
    variable: str,
    start_year: int,
    end_year: int,
    baseline_start_year: int,
    baseline_end_year: int,
) -> AnomalyResponse:
    """Build the anomaly DTO from one validated POWER daily series.

    The baseline is the equally weighted mean of the complete baseline annual
    aggregates.  A single invalid baseline day makes the mean and every anomaly
    null; display years with incomplete coverage stay null.
    """

    source_info = analysis._normalise_source(source, variable)  # existing validation
    module = _climate_data()

    try:
        rows, baseline_mean, baseline_complete_years = module.anomaly_series(
            daily,
            variable,
            start_year,
            end_year,
            baseline_start_year,
            baseline_end_year,
        )
    except analysis.DataContractError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise analysis.DataContractError(
            f"anomaly_series failed: {' '.join(str(exc).split())}"
        ) from exc

    if baseline_mean is not None:
        if isinstance(baseline_mean, bool) or not isinstance(baseline_mean, (int, float)):
            raise analysis.DataContractError("anomaly_series returned a non-numeric baseline mean")
        baseline_mean = float(baseline_mean)
        if not math.isfinite(baseline_mean):
            raise analysis.DataContractError("anomaly_series returned a non-finite baseline mean")

    if isinstance(baseline_complete_years, bool) or not isinstance(
        baseline_complete_years, int
    ):
        raise analysis.DataContractError(
            "anomaly_series returned a non-integer complete baseline count"
        )

    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise analysis.DataContractError(
            "anomaly_series must return a sequence of display rows"
        )

    points: list[AnomalyPoint] = []
    for row in rows:
        try:
            points.append(AnomalyPoint.model_validate(row))
        except (ValidationError, TypeError, ValueError) as exc:
            raise analysis.DataContractError(
                f"anomaly_series returned an invalid display row: {exc}"
            ) from exc

    warnings: list[str] = []
    baseline_span = baseline_end_year - baseline_start_year + 1
    if baseline_mean is None:
        warnings.append(
            f"Baseline {baseline_start_year}-{baseline_end_year} is incomplete: "
            f"{baseline_complete_years} of {baseline_span} baseline year(s) have "
            "every expected day, so the baseline mean and all anomalies are null."
        )
    elif baseline_span < CLIMATE_NORMAL_YEARS:
        warnings.append(
            f"Baseline {baseline_start_year}-{baseline_end_year} spans "
            f"{baseline_span} year(s); a short baseline is a reference period, "
            f"not a {CLIMATE_NORMAL_YEARS}-year climate normal."
        )

    missing_years = [point.year for point in points if point.value is None]
    if missing_years:
        warnings.append(
            "Incomplete or invalid daily coverage prevented an annual value for "
            f"{len(missing_years)} requested year(s): "
            f"{analysis._format_year_list(missing_years)}."
        )

    try:
        return AnomalyResponse(
            latitude=latitude,
            longitude=longitude,
            variable=variable,  # type: ignore[arg-type]
            start_year=start_year,
            end_year=end_year,
            baseline_start_year=baseline_start_year,
            baseline_end_year=baseline_end_year,
            unit=analysis._UNIT_BY_VARIABLE[variable],
            aggregation=analysis._AGGREGATION_BY_VARIABLE[variable],  # type: ignore[arg-type]
            baseline_mean=baseline_mean,
            baseline_complete_years=baseline_complete_years,
            series=points,
            source=source_info,
            warnings=warnings,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise analysis.DataContractError(
            f"anomaly response does not satisfy the API contract: {exc}"
        ) from exc


def build_seasonal_response(
    *,
    daily: Mapping[str, Any],
    source: Any,
    latitude: float,
    longitude: float,
    variable: str,
    start_year: int,
    end_year: int,
    period: str,
) -> SeasonalResponse:
    """Build the seasonal DTO from one validated POWER daily series.

    ``period == "all"`` returns winter, spring, summer and autumn in order from
    the same daily mapping.  Each period reuses the existing complete-period
    aggregation and OLS fit, so a period with fewer than five complete years
    has a null trend.
    """

    source_info = analysis._normalise_source(source, variable)  # existing validation
    module = _climate_data()

    codes = SEASONS if period == ALL_PERIOD else (period,)
    entries: list[SeasonalPeriodEntry] = []
    warnings: list[str] = []

    for code in codes:
        try:
            raw = module.aggregate_period(daily, variable, start_year, end_year, code)
        except analysis.DataContractError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise analysis.DataContractError(
                f"aggregate_period failed for {code!r}: {' '.join(str(exc).split())}"
            ) from exc

        aggregates = _coerce_aggregates(raw, code, start_year, end_year)
        if code == "winter":
            aggregates = _winter_min_year_guard(aggregates)

        trend, fitted_by_year, trend_warnings = analysis.fit_trend(aggregates)
        points = _series_points(aggregates, fitted_by_year)

        missing_years = [item.year for item in aggregates if item.value is None]
        if missing_years:
            warnings.append(
                f"{code}: incomplete or invalid daily coverage prevented a period "
                f"value for {len(missing_years)} requested year(s): "
                f"{analysis._format_year_list(missing_years)}."
            )
        warnings.extend(f"{code}: {warning}" for warning in trend_warnings)

        entries.append(
            SeasonalPeriodEntry(
                period=code,  # type: ignore[arg-type]
                series=points,
                trend=trend,
            )
        )

    try:
        return SeasonalResponse(
            latitude=latitude,
            longitude=longitude,
            variable=variable,  # type: ignore[arg-type]
            start_year=start_year,
            end_year=end_year,
            period=period,  # type: ignore[arg-type]
            unit=analysis._UNIT_BY_VARIABLE[variable],
            aggregation=analysis._AGGREGATION_BY_VARIABLE[variable],  # type: ignore[arg-type]
            periods=entries,
            source=source_info,
            warnings=warnings,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise analysis.DataContractError(
            f"seasonal response does not satisfy the API contract: {exc}"
        ) from exc
