"""NASA POWER Monthly/Annual Regional ingestion for EarthScope.

Synchronous ingestion of NASA POWER monthly/annual regional gridded data for
the documented demonstration region, normalized to per-cell parameter time
maps (docs/REGIONAL-MAP-API.md internal data boundary).

Boundary contract:
- ``load_regional_cells(region_id, parameter, start_year=1990, end_year=2025)``
  returns ``(cells, source)``.
- ``cells`` is the deduplicated list of native cell centers, sorted by
  latitude then longitude. Each :class:`RegionalCell` carries the returned
  center ``latitude``/``longitude`` (exact returned values),
  ``elevation_m`` (``None`` when the source omits it) and ``values`` mapping
  ``YYYYMM`` monthly keys and ``YYYY13`` annual keys of years inside the
  requested inclusive span to finite floats. Fill values (-999), non-finite
  numbers and non-numeric values are omitted; missing months/years stay
  missing (no interpolation, zero-fill or substitution).
- ``source`` is metadata for the API layer: NASA attribution
  (``dataset``, ``api_version``, ``time_standard`` ``"LST"``,
  ``community`` ``"AG"``), the raw parameter unit (``"C"`` for T2M,
  ``mm/day`` for PRECTOTCORR), both request URLs, cache status, and the
  grid geometry inferred from the returned centers (never assumed).
- Supported parameters: ``T2M`` and ``PRECTOTCORR`` (one per request, as
  NASA requires for regional submissions). Supported region: the fixed
  ``kg-surroundings`` rectangle (39-44 N, 69-81 E) with two 6-degree
  longitude tiles, 69-75 E and 75-81 E, fetched sequentially.

Fixed source coverage: the NASA request always covers 1990-2025
(inclusive). The requested ``start_year``/``end_year`` only slice the
returned time maps, so the raw per-URL cache is reused when the caller
changes the year range.

Deduplication: tile responses are merged by exactly equal centers (equal
only). A center reported twice (e.g. the 75 E tile boundary) must agree on
elevation and on every shared time key, otherwise the data is rejected;
near-equal but unequal centers are never merged.

Grid validation: latitude and longitude spacings are inferred from the
returned centers and must be uniform (within 1e-6 degrees), the merged set
must form a complete rows x columns grid, and every center must lie inside
the declared rectangle. NASA documents source-native resolution, so no
spacing value is hardcoded.

Caching is a validated, best-effort JSON raw cache:
- ``EARTHSCOPE_CACHE_DIR`` (optional) selects the storage directory; when
  unset no disk cache is used.
- Entries are keyed by the exact request URL (which encodes the fixed
  coverage) and stamped with ``retrieved_at`` (ISO 8601 UTC).
- Writes are atomic (temp file + ``os.replace``); a failed cache read or
  write never fails the request, and an unusable cache entry falls through
  to the network.

Rate limiting and deduplication of work: uncached tile fetches within one
build are separated by :data:`INTER_TILE_DELAY_SECONDS` (1.5 s); identical
in-process requests are deduplicated per request-URL key so concurrent
callers share one fetch (followers receive the leader's payload or error
and report ``cached=False`` because no disk cache was involved).

Networking: HTTPS certificate verification stays enabled. If TLS
verification fails on the live path, the request is retried once with a
platform trust store via ``truststore`` (when installed) before failing.
The test path (injected transport) never leaves the process. A bounded
timeout applies to every request.

No synthetic data at runtime: every failure raises :class:`RegionalError`
with ``code`` ``"INVALID_REQUEST"`` (caller input) or
``"POWER_UNAVAILABLE"`` (unusable upstream data), so the API layer can map
to HTTP 422 / 502-503.

Limitations: NASA POWER is a reanalysis/gridded product; cell values are
not station measurements. The regional LST product is temporally distinct
from the Daily UTC point product and must not be silently mixed (see
docs/REGIONAL-MAP-API.md). NASA license and rate-limit terms were not
independently established.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "INTER_TILE_DELAY_SECONDS",
    "RegionalCell",
    "RegionalError",
    "SUPPORTED_REGIONS",
    "load_regional_cells",
]

MONTHLY_REGIONAL_BASE_URL = (
    "https://power.larc.nasa.gov/api/temporal/monthly/regional"
)
USER = "EARTHSCOPE"
COMMUNITY = "AG"
TIME_STANDARD = "LST"
DEFAULT_TIMEOUT_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10.0
RAW_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_FILL_VALUE = -999.0
SCHEMA_VERSION = 1
CACHE_KIND = "earthscope-power-monthly-regional"

#: Minimum gap between two uncached NASA tile fetches inside one build.
INTER_TILE_DELAY_SECONDS = 1.5

#: Latitude/longitude spacing tolerance when validating the regular grid.
SPACING_TOLERANCE_DEGREES = 1e-6
#: Tolerance when checking that returned centers lie inside the rectangle.
BOUNDS_TOLERANCE_DEGREES = 1e-9
#: Tolerance for fill-value and header fill-value comparisons.
FILL_TOLERANCE = 1e-9

FIXED_COVERAGE_START_YEAR = 1990
FIXED_COVERAGE_END_YEAR = 2025
_COVERAGE_START_STAMP = f"{FIXED_COVERAGE_START_YEAR}0101"
_COVERAGE_END_STAMP = f"{FIXED_COVERAGE_END_YEAR}1231"

#: Raw NASA parameter units (the API layer maps these to display units).
SUPPORTED_PARAMETERS = {"T2M": "C", "PRECTOTCORR": "mm/day"}

#: The supported demonstration region: one bounded rectangle, not a
#: national boundary, tiled into two 6-degree longitude requests (NASA
#: rejects regional longitude spans above 10 degrees and requires at
#: least 2 degrees in both dimensions).
REGION_KG_SURROUNDINGS = {
    "id": "kg-surroundings",
    "label": "Kyrgyzstan and neighboring areas",
    "south": 39.0,
    "north": 44.0,
    "west": 69.0,
    "east": 81.0,
    "tiles": ((69.0, 75.0), (75.0, 81.0)),
}

SUPPORTED_REGIONS = {"kg-surroundings": REGION_KG_SURROUNDINGS}

_TIME_KEY_RE = re.compile(r"^\d{6}$")


class RegionalError(Exception):
    """Invalid request or unusable upstream POWER regional data.

    ``code`` is ``"INVALID_REQUEST"`` for caller input problems and
    ``"POWER_UNAVAILABLE"`` for upstream/network/cache-unusable problems, so
    the API layer can map to HTTP 422 / 502-503.
    """

    def __init__(
        self,
        message: str,
        code: str = "POWER_UNAVAILABLE",
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class RegionalCell:
    """One deduplicated native grid cell of the regional response.

    ``latitude``/``longitude`` are the exact returned center values,
    ``elevation_m`` is the returned third coordinate (``None`` when absent
    or not a finite number) and ``values`` maps ``YYYYMM`` monthly keys and
    ``YYYY13`` annual keys (restricted to the requested year span) to
    finite floats. Fill values and invalid entries are omitted.
    """

    latitude: float
    longitude: float
    elevation_m: float | None
    values: dict[str, float]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_regional_cells(
    region_id: str,
    parameter: str,
    start_year: int = FIXED_COVERAGE_START_YEAR,
    end_year: int = FIXED_COVERAGE_END_YEAR,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[list[RegionalCell], dict]:
    """Share the fixed NASA download across overlapping period selections."""
    _validate_request(region_id, parameter, start_year, end_year)
    key = (region_id, parameter)
    with _BUILD_INFLIGHT_LOCK:
        flight = _BUILD_INFLIGHT.get(key)
        if flight is None:
            flight = _BuildFlight(event=threading.Event())
            _BUILD_INFLIGHT[key] = flight
            leader = True
        else:
            leader = False
    if leader:
        try:
            flight.result = _load_regional_cells_uncached(
                region_id, parameter, transport=transport
            )
        except RegionalError as exc:
            flight.error = exc
            raise
        finally:
            flight.event.set()
            with _BUILD_INFLIGHT_LOCK:
                if _BUILD_INFLIGHT.get(key) is flight:
                    del _BUILD_INFLIGHT[key]
    elif not flight.event.wait(timeout=DEFAULT_TIMEOUT_SECONDS * 2):
        raise RegionalError(
            "timed out waiting for the same regional POWER build", retryable=True
        )
    if flight.error is not None:
        raise RegionalError(
            str(flight.error), flight.error.code, retryable=flight.error.retryable
        )
    if flight.result is None:
        raise RegionalError("shared regional POWER build completed without data")
    cells, source = flight.result
    if (start_year, end_year) == (FIXED_COVERAGE_START_YEAR, FIXED_COVERAGE_END_YEAR):
        return cells, source

    first, last = f"{start_year}01", f"{end_year}13"
    selected = [
        RegionalCell(cell.latitude, cell.longitude, cell.elevation_m, {
            key: value for key, value in cell.values.items() if first <= key <= last
        })
        for cell in cells
    ]
    if not any(cell.values for cell in selected):
        raise RegionalError(f"no valid regional values for {parameter} in {start_year}-{end_year}")
    return selected, {**source, "start_year": start_year, "end_year": end_year}


def _load_regional_cells_uncached(
    region_id: str,
    parameter: str,
    start_year: int = FIXED_COVERAGE_START_YEAR,
    end_year: int = FIXED_COVERAGE_END_YEAR,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[list[RegionalCell], dict]:
    """Load deduplicated regional cells for one parameter and year span.

    Performs the two sequential tile requests for the region (raw-cache
    first), merges features with exactly equal centers, infers and
    validates the regular grid and returns ``(cells, source)``. See the
    module docstring for the full contract.
    """
    region = _validate_request(region_id, parameter, start_year, end_year)

    cache_dir = _cache_dir()
    merged: dict[tuple[float, float], tuple[float | None, dict[str, float]]] = {}
    request_urls: list[str] = []
    tile_cache_states: list[bool] = []
    retrieved_ats: list[str] = []
    dataset: str | None = None
    api_version: str | None = None
    previous_uncached = False

    for lon_min, lon_max in region["tiles"]:
        request = _build_request(region, parameter, lon_min, lon_max)
        url = str(request.url)
        request_urls.append(url)
        cache_path = _cache_path(cache_dir, url) if cache_dir is not None else None

        parsed: list[tuple[float, float, float | None, dict[str, float]]] | None = None
        tile_header: dict[str, str] | None = None
        retrieved_at = ""
        cached = False

        if cache_path is not None:
            entry = _read_cache(cache_path, url, parameter)
            if entry is not None:
                try:
                    tile_header, parsed = _parse_tile(
                        entry, parameter, region, start_year, end_year
                    )
                except RegionalError:
                    parsed = None  # unusable cache entry: fall through to network
                else:
                    retrieved_at = entry["retrieved_at"]
                    cached = True

        if parsed is None:
            if previous_uncached:
                _sleep(INTER_TILE_DELAY_SECONDS)
            payload, retrieved_at = _dedup_fetch(request, url, transport)
            tile_header, parsed = _parse_tile(
                payload, parameter, region, start_year, end_year
            )
            cached = False
            if cache_path is not None:
                _write_cache(
                    cache_path,
                    _cache_payload(url, parameter, retrieved_at, payload),
                )

        assert tile_header is not None and parsed is not None  # for type checkers
        previous_uncached = not cached
        tile_cache_states.append(cached)
        retrieved_ats.append(retrieved_at)
        if dataset is None:
            dataset = tile_header["dataset"]
            api_version = tile_header["api_version"]
        elif (
            tile_header["dataset"] != dataset
            or tile_header["api_version"] != api_version
        ):
            raise RegionalError("regional tiles disagree on header attribution")

        for lat, lon, elevation, values in parsed:
            _merge_cell(merged, lat, lon, elevation, values)

    if dataset is None or api_version is None or not merged:
        raise RegionalError("regional responses contained no usable cells")
    if not any(values for _elevation, values in merged.values()):
        raise RegionalError(
            f"no valid regional values for {parameter} in "
            f"{start_year}-{end_year}"
        )

    grid = _infer_grid(merged)
    cells = [
        RegionalCell(latitude=lat, longitude=lon, elevation_m=elevation, values=values)
        for (lat, lon), (elevation, values) in sorted(merged.items())
    ]
    source = {
        "name": "NASA POWER",
        "service": "monthly-regional",
        "parameter": parameter,
        "native_unit": SUPPORTED_PARAMETERS[parameter],
        "dataset": dataset,
        "api_version": api_version,
        "time_standard": TIME_STANDARD,
        "community": COMMUNITY,
        "region_id": region["id"],
        "region_label": region["label"],
        "bounds": {
            "south": region["south"],
            "north": region["north"],
            "west": region["west"],
            "east": region["east"],
        },
        "coverage_start_year": FIXED_COVERAGE_START_YEAR,
        "coverage_end_year": FIXED_COVERAGE_END_YEAR,
        "start_year": start_year,
        "end_year": end_year,
        "request_urls": request_urls,
        "retrieved_at": max(retrieved_ats),
        "tile_retrieved_at": retrieved_ats,
        "cached": all(tile_cache_states),
        "tile_cache_states": tile_cache_states,
        "grid": grid,
    }
    return cells, source


# ---------------------------------------------------------------------------
# Request validation and construction
# ---------------------------------------------------------------------------


def _validate_request(
    region_id: Any, parameter: Any, start_year: Any, end_year: Any
) -> dict:
    if not isinstance(region_id, str) or region_id not in SUPPORTED_REGIONS:
        raise RegionalError(
            f"unsupported region_id {region_id!r}; expected one of "
            f"{sorted(SUPPORTED_REGIONS)}",
            "INVALID_REQUEST",
        )
    if not isinstance(parameter, str) or parameter not in SUPPORTED_PARAMETERS:
        raise RegionalError(
            f"unsupported parameter {parameter!r}; expected one of "
            f"{sorted(SUPPORTED_PARAMETERS)}",
            "INVALID_REQUEST",
        )
    for name, year in (("start_year", start_year), ("end_year", end_year)):
        if isinstance(year, bool) or not isinstance(year, int):
            raise RegionalError(f"{name} must be an integer", "INVALID_REQUEST")
        if not FIXED_COVERAGE_START_YEAR <= year <= FIXED_COVERAGE_END_YEAR:
            raise RegionalError(
                f"{name} must be within "
                f"[{FIXED_COVERAGE_START_YEAR}, {FIXED_COVERAGE_END_YEAR}]",
                "INVALID_REQUEST",
            )
    if start_year > end_year:
        raise RegionalError("start_year must not exceed end_year", "INVALID_REQUEST")
    return SUPPORTED_REGIONS[region_id]


def _build_request(
    region: dict, parameter: str, lon_min: float, lon_max: float
) -> httpx.Request:
    """Build one Monthly/Annual Regional tile request (fixed coverage)."""
    return httpx.Request(
        "GET",
        MONTHLY_REGIONAL_BASE_URL,
        params={
            "parameters": parameter,
            "community": COMMUNITY,
            "longitude-min": str(int(lon_min)),
            "longitude-max": str(int(lon_max)),
            "latitude-min": str(int(region["south"])),
            "latitude-max": str(int(region["north"])),
            # The Regional endpoint accepts years here (1990/2025); the
            # returned header still reports full YYYYMMDD coverage stamps.
            "start": str(FIXED_COVERAGE_START_YEAR),
            "end": str(FIXED_COVERAGE_END_YEAR),
            "format": "JSON",
            # A user identifier makes POWER include the response header with
            # source attribution; without it the header may be omitted.
            "user": USER,
        },
    )


# ---------------------------------------------------------------------------
# Response parsing and validation
# ---------------------------------------------------------------------------


def _parse_tile(
    payload: Any,
    parameter: str,
    region: dict,
    start_year: int,
    end_year: int,
) -> tuple[dict[str, str], list[tuple[float, float, float | None, dict[str, float]]]]:
    """Validate one tile payload and parse its features.

    Returns ``(header_info, cells)`` where ``cells`` are
    ``(latitude, longitude, elevation_m, values)`` tuples. Raises
    :class:`RegionalError` on any unusable response.
    """
    if not isinstance(payload, dict):
        raise RegionalError("regional response is not a JSON object")
    dataset, api_version, fill_value = _validate_header(payload, parameter)
    if payload.get("type") != "FeatureCollection":
        raise RegionalError(
            f"unexpected regional response type {payload.get('type')!r}; "
            "FeatureCollection required"
        )
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise RegionalError("regional response has no features")
    cells = [
        _parse_feature(feature, parameter, fill_value, region, start_year, end_year)
        for feature in features
    ]
    return {"dataset": dataset, "api_version": api_version}, cells


def _validate_header(payload: dict, parameter: str) -> tuple[str, str, float]:
    """Validate the response header and units; return (dataset, api_version, fill_value)."""
    header = payload.get("header")
    if not isinstance(header, dict):
        raise RegionalError("regional response is missing a valid header")
    if header.get("time_standard") != TIME_STANDARD:
        raise RegionalError(
            f"unexpected regional time_standard {header.get('time_standard')!r}; "
            f"{TIME_STANDARD} required"
        )
    dataset = _dataset_from_sources(header.get("sources"))
    if dataset is None:
        raise RegionalError("regional header has no dataset source attribution")
    api = header.get("api")
    if not isinstance(api, dict):
        raise RegionalError("regional header is missing the api metadata object")
    api_version = api.get("version")
    if not isinstance(api_version, str) or not api_version.strip():
        raise RegionalError("regional header api version is missing")
    fill_value = header.get("fill_value")
    if isinstance(fill_value, bool) or not isinstance(fill_value, (int, float)):
        raise RegionalError("regional header fill_value is not numeric")
    fill_value = float(fill_value)
    if not math.isfinite(fill_value):
        raise RegionalError("regional header fill_value is not finite")
    if not math.isclose(fill_value, DEFAULT_FILL_VALUE, rel_tol=0.0, abs_tol=FILL_TOLERANCE):
        raise RegionalError(
            f"unexpected regional fill_value {fill_value!r}; "
            f"expected {DEFAULT_FILL_VALUE!r}"
        )
    if header.get("start") != _COVERAGE_START_STAMP or header.get("end") != _COVERAGE_END_STAMP:
        raise RegionalError(
            "regional header coverage "
            f"{header.get('start')!r}-{header.get('end')!r} does not match the "
            f"fixed source coverage {_COVERAGE_START_STAMP}-{_COVERAGE_END_STAMP}"
        )
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict) or not isinstance(parameters.get(parameter), dict):
        raise RegionalError(f"regional response has no units metadata for {parameter}")
    units = parameters[parameter].get("units")
    if units != SUPPORTED_PARAMETERS[parameter]:
        raise RegionalError(
            f"unexpected unit {units!r} for {parameter}; "
            f"expected {SUPPORTED_PARAMETERS[parameter]!r}"
        )
    return dataset, api_version, fill_value


def _dataset_from_sources(sources: Any) -> str | None:
    """Extract the dataset attribution string from ``header.sources``."""
    if isinstance(sources, list) and sources:
        first = sources[0]
        if isinstance(first, str) and first.strip():
            return first
    if isinstance(sources, str) and sources.strip():
        return sources
    return None


def _parse_feature(
    feature: Any,
    parameter: str,
    fill_value: float,
    region: dict,
    start_year: int,
    end_year: int,
) -> tuple[float, float, float | None, dict[str, float]]:
    """Parse one GeoJSON feature into (lat, lon, elevation, values)."""
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise RegionalError("regional response contains a non-feature entry")
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise RegionalError("regional feature geometry is not a Point")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise RegionalError("regional feature coordinates must be [lon, lat, ...]")
    longitude = _coordinate(coordinates[0], "longitude")
    latitude = _coordinate(coordinates[1], "latitude")

    elevation: float | None = None
    if len(coordinates) >= 3 and isinstance(coordinates[2], (int, float)) and not isinstance(
        coordinates[2], bool
    ):
        candidate = float(coordinates[2])
        if math.isfinite(candidate):
            elevation = candidate

    south, north = region["south"], region["north"]
    west, east = region["west"], region["east"]
    if not (
        south - BOUNDS_TOLERANCE_DEGREES
        <= latitude
        <= north + BOUNDS_TOLERANCE_DEGREES
        and west - BOUNDS_TOLERANCE_DEGREES
        <= longitude
        <= east + BOUNDS_TOLERANCE_DEGREES
    ):
        raise RegionalError(
            "regional feature center "
            f"({longitude}, {latitude}) lies outside the declared rectangle"
        )

    properties = feature.get("properties")
    parameter_map = properties.get("parameter") if isinstance(properties, dict) else None
    if not isinstance(parameter_map, dict) or not isinstance(parameter_map.get(parameter), dict):
        raise RegionalError(f"regional feature is missing data for {parameter}")
    values = _parse_values(
        parameter_map[parameter], fill_value, start_year, end_year
    )
    return latitude, longitude, elevation, values


def _coordinate(value: Any, axis: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegionalError(f"regional feature {axis} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise RegionalError(f"regional feature {axis} is not finite")
    return number


def _parse_values(
    raw: Any, fill_value: float, start_year: int, end_year: int
) -> dict[str, float]:
    """Normalize one cell's raw time map to finite floats keyed YYYYMM/YYYY13.

    Keys that are not ``YYYYMM`` with a month in 1..13 and a year inside the
    fixed 1990-2025 coverage are ignored. Values that are missing, fill,
    non-finite or non-numeric are omitted, as are years outside the
    requested span. Missing months/years stay missing.
    """
    if not isinstance(raw, dict):
        raise RegionalError("regional cell time map is not an object")
    values: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _TIME_KEY_RE.match(key):
            continue
        year = int(key[:4])
        month = int(key[4:])
        if not FIXED_COVERAGE_START_YEAR <= year <= FIXED_COVERAGE_END_YEAR:
            continue
        if not 1 <= month <= 13:
            continue
        if year < start_year or year > end_year:
            continue
        numeric = _coerce_number(value)
        if numeric is None or not math.isfinite(numeric):
            continue
        if math.isclose(numeric, fill_value, rel_tol=0.0, abs_tol=FILL_TOLERANCE):
            continue
        values[key] = numeric
    return values


def _coerce_number(value: Any) -> float | None:
    """Coerce strict numeric values; anything else is an invalid entry."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# ---------------------------------------------------------------------------
# Tile merging and grid inference
# ---------------------------------------------------------------------------


def _merge_cell(
    merged: dict[tuple[float, float], tuple[float | None, dict[str, float]]],
    latitude: float,
    longitude: float,
    elevation: float | None,
    values: dict[str, float],
) -> None:
    """Merge one feature; only exactly equal centers are deduplicated."""
    key = (latitude, longitude)
    existing = merged.get(key)
    if existing is None:
        merged[key] = (elevation, values)
        return
    existing_elevation, existing_values = existing
    if existing_elevation != elevation:
        raise RegionalError(
            f"duplicate feature for center ({longitude}, {latitude}) "
            "reports a different elevation"
        )
    if existing_values.keys() != values.keys():
        raise RegionalError(
            f"duplicate feature for center ({longitude}, {latitude}) "
            "reports a different set of time keys"
        )
    for name, value in values.items():
        if existing_values[name] != value:
            raise RegionalError(
                f"duplicate feature for center ({longitude}, {latitude}) "
                f"disagrees on {name}"
            )


def _infer_grid(
    merged: dict[tuple[float, float], tuple[float | None, dict[str, float]]],
) -> dict:
    """Infer and validate the regular grid from the merged centers."""
    latitudes = sorted({lat for lat, _ in merged})
    longitudes = sorted({lon for _, lon in merged})
    if len(latitudes) < 2 or len(longitudes) < 2:
        raise RegionalError(
            "regional grid needs at least two latitude rows and two "
            "longitude columns"
        )
    latitude_degrees = _uniform_spacing(latitudes, "latitude")
    longitude_degrees = _uniform_spacing(longitudes, "longitude")
    if len(merged) != len(latitudes) * len(longitudes):
        raise RegionalError(
            f"irregular regional grid: {len(merged)} unique centers for a "
            f"{len(latitudes)} x {len(longitudes)} center set"
        )
    return {
        "latitude_degrees": latitude_degrees,
        "longitude_degrees": longitude_degrees,
        "rows": len(latitudes),
        "columns": len(longitudes),
        "cells": len(merged),
    }


def _uniform_spacing(values: list[float], axis: str) -> float:
    diffs = [later - earlier for earlier, later in zip(values, values[1:])]
    reference = diffs[0]
    if reference <= 0.0 or not math.isfinite(reference):
        raise RegionalError(f"regional {axis} spacing is not positive")
    for diff in diffs[1:]:
        if abs(diff - reference) > SPACING_TOLERANCE_DEGREES:
            raise RegionalError(
                f"irregular regional {axis} spacing: expected "
                f"{reference!r} but found {diff!r}"
            )
    return sum(diffs) / len(diffs)


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------


@dataclass
class _Inflight:
    """Shared state of one in-flight fetch, keyed by request URL."""

    event: threading.Event
    payload: dict | None = None
    retrieved_at: str = ""
    error: RegionalError | None = None


@dataclass
class _BuildFlight:
    """In-process deduplication for the complete two-tile regional build."""

    event: threading.Event
    result: tuple[list[RegionalCell], dict] | None = None
    error: RegionalError | None = None


_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, _Inflight] = {}
_BUILD_INFLIGHT_LOCK = threading.Lock()
_BUILD_INFLIGHT: dict[tuple[str, str], _BuildFlight] = {}


def _dedup_fetch(
    request: httpx.Request,
    url: str,
    transport: httpx.BaseTransport | None,
) -> tuple[dict, str]:
    """Fetch one request URL at most once per process at a time.

    The first caller executes the request; concurrent callers with the same
    URL wait and receive the leader's payload (or error). The entry is
    always removed afterwards, so a later request refetches normally.
    """
    with _INFLIGHT_LOCK:
        inflight = _INFLIGHT.get(url)
        if inflight is None:
            inflight = _Inflight(event=threading.Event())
            _INFLIGHT[url] = inflight
            leader = True
        else:
            leader = False

    if leader:
        try:
            payload, retrieved_at = _fetch(request, transport)
            # Publish the result before the finally block releases waiters.
            inflight.payload = payload
            inflight.retrieved_at = retrieved_at
            return payload, retrieved_at
        except RegionalError as exc:
            inflight.error = exc
            raise
        finally:
            inflight.event.set()
            with _INFLIGHT_LOCK:
                if _INFLIGHT.get(url) is inflight:
                    del _INFLIGHT[url]

    if not inflight.event.wait(timeout=DEFAULT_TIMEOUT_SECONDS * 2):
        raise RegionalError("timed out waiting for the NASA POWER tile", retryable=True)
    if inflight.error is not None:
        # Re-raise a fresh instance so waiters do not share one traceback.
        raise RegionalError(
            str(inflight.error), inflight.error.code, retryable=inflight.error.retryable
        )
    if inflight.payload is None:
        raise RegionalError("shared NASA POWER request completed without a payload")
    return inflight.payload, inflight.retrieved_at


def _fetch(
    request: httpx.Request, transport: httpx.BaseTransport | None
) -> tuple[dict, str]:
    """Fetch the payload over HTTPS with a bounded timeout.

    On the live path (no injected transport), a TLS verification failure is
    retried once with the platform trust store via ``truststore``, when the
    optional dev dependency is importable.
    """
    try:
        return _send_once(request, transport, verify=True)
    except RegionalError as exc:
        if transport is not None or not _is_tls_failure(exc.__cause__ or exc.__context__):
            raise
        context = _truststore_ssl_context()
        if context is None:
            raise
        # Platform trust store fallback: verification stays enabled, only the
        # trust root source changes to the OS certificate store.
        return _send_once(request, None, verify=context)


def _send_once(
    request: httpx.Request,
    transport: httpx.BaseTransport | None,
    verify: bool | ssl.SSLContext,
) -> tuple[dict, str]:
    timeout = httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    try:
        with httpx.Client(verify=verify, timeout=timeout, transport=transport) as client:
            response = client.send(request)
    except httpx.HTTPError as exc:
            raise RegionalError(f"NASA POWER request failed: {exc}", retryable=True) from exc
    if response.status_code != 200:
        raise RegionalError(
            f"NASA POWER returned HTTP {response.status_code}",
            retryable=response.status_code in (429, 500, 502, 503, 504),
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RegionalError(f"NASA POWER returned non-JSON content: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegionalError("NASA POWER returned an unexpected JSON payload")
    return payload, _now_iso_utc()


def _is_tls_failure(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLError):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _truststore_ssl_context() -> ssl.SSLContext | None:
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - optional dependency at runtime
        return None


def _sleep(seconds: float) -> None:
    """Rate-limit hook; tests replace this to observe the inter-tile gap."""
    time.sleep(seconds)


def _now_iso_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Raw cache
# ---------------------------------------------------------------------------


def _cache_dir() -> Path | None:
    value = os.environ.get("EARTHSCOPE_CACHE_DIR")
    return Path(value) if value else None


def _cache_path(cache_dir: Path, request_url: str) -> Path:
    digest = hashlib.sha256(request_url.encode("utf-8")).hexdigest()
    return cache_dir / f"{CACHE_KIND}-v{SCHEMA_VERSION}-{digest}.json"


def _cache_payload(request_url: str, parameter: str, retrieved_at: str, payload: dict) -> dict:
    """Build the raw cache entry, preserving the upstream header metadata."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": CACHE_KIND,
        "request_url": request_url,
        "parameter": parameter,
        "retrieved_at": retrieved_at,
        "type": payload.get("type"),
        "header": payload.get("header"),
        "parameters": payload.get("parameters"),
        "features": payload.get("features"),
    }


def _read_cache(path: Path, request_url: str, parameter: str) -> dict | None:
    """Load a raw cache entry; return None on any miss, corruption or mismatch."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    if (
        entry.get("schema_version") != SCHEMA_VERSION
        or entry.get("kind") != CACHE_KIND
        or entry.get("request_url") != request_url
        or entry.get("parameter") != parameter
        or not isinstance(entry.get("retrieved_at"), str)
        or not isinstance(entry.get("type"), str)
        or not isinstance(entry.get("header"), dict)
        or not isinstance(entry.get("parameters"), dict)
        or not isinstance(entry.get("features"), list)
    ):
        return None
    try:
        retrieved = datetime.fromisoformat(entry["retrieved_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    age = (datetime.now(timezone.utc) - retrieved.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > RAW_CACHE_TTL_SECONDS:
        return None
    # Re-shape into a regional payload so the same validation runs on cache
    # hits, keeping retrieved_at for the source metadata.
    return {
        "retrieved_at": entry["retrieved_at"],
        "type": entry["type"],
        "header": entry["header"],
        "parameters": entry["parameters"],
        "features": entry["features"],
    }


def _write_cache(path: Path, entry: dict) -> bool:
    """Atomically write the cache entry; best-effort, never raises."""
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # allow_nan=False keeps the cache strict; a payload containing NaN
        # fails the write (tolerated) rather than poisoning the cache file.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            tmp = Path(handle.name)
            json.dump(entry, handle, allow_nan=False)
        os.replace(tmp, path)
        tmp = None
        return True
    except (OSError, TypeError, ValueError):
        return False  # cache write failure must not fail the request
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
