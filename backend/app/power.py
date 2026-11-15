"""NASA POWER Daily Point ingestion for EarthScope.

Synchronous ingestion of NASA POWER daily gridded meteorology for a single
point, normalized to numeric daily values by parameter (docs/API.md internal
data boundary).

Boundary contract:
- ``load_daily(latitude, longitude, variable, start_year, end_year)``
  returns ``(daily, source)``.
- ``daily`` maps ``YYYYMMDD`` (UTC) keys to ``float``. Invalid, missing,
  fill-value, non-finite and (for precipitation) negative days are omitted.
  Variables: ``temperature`` (T2M), ``temperature_max`` (T2M_MAX),
  ``temperature_min`` (T2M_MIN) and ``precipitation`` (PRECTOTCORR). The
  extrema variables mirror T2M exactly (verified live via
  ``.tools/extreme-probes``: 13149 nonfill days per point at both probe
  points, header units ``C``, fill_value -999, UTC, no explicit grid
  spacing in the point payload).
- ``source`` is the ``TrendResponse.source`` metadata object, including
  header-derived dataset attribution and cache status.
- No synthetic data at runtime: every failure raises :class:`PowerError`
  with ``code`` ``"INVALID_REQUEST"`` (caller input) or
  ``"POWER_UNAVAILABLE"`` (unusable upstream data).

Validation is strict: the response must carry a header with ``UTC`` time
standard, non-empty dataset source attribution, ``fill_value``, coverage
matching the requested Jan 1..Dec 31 interval exactly (truncated header
coverage is rejected), and per-parameter units equal to ``C`` (T2M,
T2M_MAX, T2M_MIN) or ``mm/day`` (PRECTOTCORR).

Caching is a validated, best-effort JSON cache:
- ``EARTHSCOPE_CACHE_DIR`` (optional) selects the storage directory; when
  unset no disk cache is used.
- Entries are keyed by the exact request URL and stamped with
  ``retrieved_at`` (ISO 8601 UTC).
- Writes are atomic (temp file + ``os.replace``) and a failed cache write or
  read never fails the request.

Networking: HTTPS certificate verification stays enabled. If TLS
verification fails on the live path, the request is retried once with a
platform trust store via ``truststore`` (when installed) before failing.
The test path (injected transport) never leaves the process. A bounded
timeout applies to every request.

Only ``int``/``float`` daily values are accepted; ``None``, strings, booleans
and other types are treated as invalid days and omitted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

__all__ = ["PowerError", "load_daily"]

POWER_BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
USER = "EARTHSCOPE"
COMMUNITY = "AG"
DEFAULT_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_FILL_VALUE = -999.0
SCHEMA_VERSION = 1
CACHE_KIND = "earthscope-power-daily"
YEAR_MIN = 1900
YEAR_MAX = 2100

VARIABLE_TO_PARAMETER = {
    "temperature": "T2M",
    "temperature_max": "T2M_MAX",
    "temperature_min": "T2M_MIN",
    "precipitation": "PRECTOTCORR",
}
PARAMETER_UNITS = {
    "T2M": "C",
    "T2M_MAX": "C",
    "T2M_MIN": "C",
    "PRECTOTCORR": "mm/day",
}
_DATE_KEY_RE = re.compile(r"^\d{8}$")


class PowerError(Exception):
    """Invalid request or unusable upstream POWER data.

    ``code`` is ``"INVALID_REQUEST"`` for caller input problems and
    ``"POWER_UNAVAILABLE"`` for upstream/network/cache-unusable problems, so
    the API layer can map to HTTP 422 / 502-503.
    """

    def __init__(self, message: str, code: str = "POWER_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


def load_daily(
    latitude: float,
    longitude: float,
    variable: str,
    start_year: int,
    end_year: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, float], dict]:
    """Load normalized daily values for one parameter at one point.

    Returns ``(daily, source)``. ``daily`` maps ``YYYYMMDD`` UTC keys to
    finite floats; invalid and missing days are omitted. ``source`` carries
    the metadata required by the API contract, including ``cached`` status.
    """
    _validate_request(latitude, longitude, variable, start_year, end_year)
    parameter = VARIABLE_TO_PARAMETER[variable]
    request, start, end = _build_request(latitude, longitude, variable, start_year, end_year)
    request_url = str(request.url)

    cache_dir = _cache_dir()
    cache_path = _cache_path(cache_dir, request_url) if cache_dir is not None else None

    cached = _read_cache(cache_path, request_url, parameter) if cache_path is not None else None
    if cached is not None:
        try:
            daily, dataset = _extract(cached, parameter, start, end)
        except PowerError:
            cached = None  # unusable cache entry: fall through to the network
        else:
            return (
                daily,
                _source(parameter, request_url, cached["retrieved_at"], True, dataset),
            )

    payload, retrieved_at = _fetch(request, transport)
    daily, dataset = _extract(payload, parameter, start, end)
    source = _source(parameter, request_url, retrieved_at, False, dataset)

    if cache_path is not None:
        _write_cache(
            cache_path,
            _cache_payload(request_url, parameter, retrieved_at, payload),
        )
    return daily, source


def _validate_request(
    latitude: Any, longitude: Any, variable: Any, start_year: Any, end_year: Any
) -> None:
    if variable not in VARIABLE_TO_PARAMETER:
        raise PowerError(
            f"unsupported variable {variable!r}; expected one of "
            f"{sorted(VARIABLE_TO_PARAMETER)}",
            "INVALID_REQUEST",
        )
    latitude = _finite_number(latitude, "latitude")
    longitude = _finite_number(longitude, "longitude")
    if not -90.0 <= latitude <= 90.0:
        raise PowerError("latitude must be within [-90, 90]", "INVALID_REQUEST")
    if not -180.0 <= longitude <= 180.0:
        raise PowerError("longitude must be within [-180, 180]", "INVALID_REQUEST")
    for name, year in (("start_year", start_year), ("end_year", end_year)):
        if isinstance(year, bool) or not isinstance(year, int):
            raise PowerError(f"{name} must be an integer", "INVALID_REQUEST")
        if not YEAR_MIN <= year <= YEAR_MAX:
            raise PowerError(
                f"{name} must be within [{YEAR_MIN}, {YEAR_MAX}]", "INVALID_REQUEST"
            )
    if start_year > end_year:
        raise PowerError("start_year must not exceed end_year", "INVALID_REQUEST")


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PowerError(f"{name} must be a number", "INVALID_REQUEST")
    try:
        number = float(value)
    except OverflowError as exc:  # e.g. very large integers
        raise PowerError(f"{name} is out of range", "INVALID_REQUEST") from exc
    if not math.isfinite(number):
        raise PowerError(f"{name} must be finite", "INVALID_REQUEST")
    return number


def _build_request(
    latitude: float, longitude: float, variable: str, start_year: int, end_year: int
) -> tuple[httpx.Request, str, str]:
    """Build the POWER Daily Point request; returns (request, start, end)."""
    start = date(start_year, 1, 1).strftime("%Y%m%d")
    end = date(end_year, 12, 31).strftime("%Y%m%d")
    request = httpx.Request(
        "GET",
        POWER_BASE_URL,
        params={
            "parameters": VARIABLE_TO_PARAMETER[variable],
            "community": COMMUNITY,
            "latitude": str(float(latitude)),
            "longitude": str(float(longitude)),
            "start": start,
            "end": end,
            "format": "JSON",
            "time-standard": "UTC",
            # A user identifier makes POWER include the response header with
            # source attribution; without it the header is omitted.
            "user": USER,
        },
    )
    return request, start, end


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
    except PowerError as exc:
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
        raise PowerError(f"NASA POWER request failed: {exc}") from exc
    if response.status_code != 200:
        raise PowerError(f"NASA POWER returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise PowerError(f"NASA POWER returned non-JSON content: {exc}") from exc
    if not isinstance(payload, dict):
        raise PowerError("NASA POWER returned an unexpected JSON payload")
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


def _now_iso_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _extract(
    payload: dict, parameter: str, start: str, end: str
) -> tuple[dict[str, float], str]:
    """Validate header/units and normalize daily values for ``parameter``."""
    dataset, fill_value = _validate_header(payload, parameter, start, end)
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise PowerError("POWER response has no properties object")
    parameter_map = properties.get("parameter")
    if not isinstance(parameter_map, dict) or not isinstance(
        parameter_map.get(parameter), dict
    ):
        raise PowerError(f"POWER response is missing daily data for {parameter}")
    daily = _normalize_daily(parameter, parameter_map[parameter], fill_value)
    if not daily:
        raise PowerError(f"no valid daily values for {parameter} in {start}-{end}")
    return daily, dataset


def _validate_header(payload: dict, parameter: str, start: str, end: str) -> tuple[str, float]:
    """Validate the response header and units; return (dataset, fill_value)."""
    header = payload.get("header")
    if not isinstance(header, dict):
        raise PowerError("POWER response is missing a valid header")
    if header.get("time_standard") != "UTC":
        raise PowerError(
            f"unexpected POWER time_standard {header.get('time_standard')!r}; UTC required"
        )
    dataset = _dataset_from_sources(header.get("sources"))
    if dataset is None:
        raise PowerError("POWER header has no dataset source attribution")
    if header.get("start") != start or header.get("end") != end:
        raise PowerError(
            "POWER header coverage "
            f"{header.get('start')!r}-{header.get('end')!r} does not match "
            f"requested {start}-{end}"
        )
    fill_value = header.get("fill_value", DEFAULT_FILL_VALUE)
    if isinstance(fill_value, bool) or not isinstance(fill_value, (int, float)):
        raise PowerError("POWER header fill_value is not numeric")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict) or not isinstance(parameters.get(parameter), dict):
        raise PowerError(f"POWER response has no units metadata for {parameter}")
    units = parameters[parameter].get("units")
    if units != PARAMETER_UNITS[parameter]:
        raise PowerError(
            f"unexpected unit {units!r} for {parameter}; "
            f"expected {PARAMETER_UNITS[parameter]!r}"
        )
    return dataset, float(fill_value)


def _dataset_from_sources(sources: Any) -> str | None:
    """Extract the dataset attribution string from ``header.sources``."""
    if isinstance(sources, list) and sources:
        first = sources[0]
        if isinstance(first, str) and first.strip():
            return first
    if isinstance(sources, str) and sources.strip():
        return sources
    return None


def _normalize_daily(parameter: str, raw: Any, fill_value: float) -> dict[str, float]:
    """Normalize a raw POWER daily map to finite floats keyed by YYYYMMDD.

    Invalid, missing, non-finite, fill-value and (for precipitation)
    negative days are omitted. Keys that are not valid YYYYMMDD calendar
    dates are omitted as well.
    """
    if not isinstance(raw, dict):
        raise PowerError(f"POWER daily data for {parameter} is not an object")
    daily: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _DATE_KEY_RE.match(key):
            continue
        try:
            datetime.strptime(key, "%Y%m%d")
        except ValueError:
            continue
        numeric = _coerce_number(value)
        if numeric is None or not math.isfinite(numeric):
            continue
        if math.isclose(numeric, fill_value, rel_tol=0.0, abs_tol=1e-9):
            continue
        if parameter == "PRECTOTCORR" and numeric < 0.0:
            continue
        daily[key] = numeric
    return daily


def _coerce_number(value: Any) -> float | None:
    """Coerce strict numeric values; anything else is an invalid day."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _source(
    parameter: str, request_url: str, retrieved_at: str, cached: bool, dataset: str
) -> dict:
    return {
        "name": "NASA POWER",
        "parameter": parameter,
        "daily_unit": PARAMETER_UNITS[parameter],
        "dataset": dataset,
        "time_standard": "UTC",
        "request_url": request_url,
        "retrieved_at": retrieved_at,
        "cached": bool(cached),
    }


def _cache_dir() -> Path | None:
    value = os.environ.get("EARTHSCOPE_CACHE_DIR")
    return Path(value) if value else None


def _cache_path(cache_dir: Path, request_url: str) -> Path:
    digest = hashlib.sha256(request_url.encode("utf-8")).hexdigest()
    return cache_dir / f"{CACHE_KIND}-v{SCHEMA_VERSION}-{digest}.json"


def _cache_payload(request_url: str, parameter: str, retrieved_at: str, payload: dict) -> dict:
    """Build the cache entry, preserving the upstream header metadata."""
    header = payload.get("header")
    parameters = payload.get("parameters")
    properties = payload.get("properties")
    parameter_map = properties.get("parameter") if isinstance(properties, dict) else None
    raw_daily = parameter_map.get(parameter) if isinstance(parameter_map, dict) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": CACHE_KIND,
        "request_url": request_url,
        "parameter": parameter,
        "retrieved_at": retrieved_at,
        "header": header,
        "parameters": parameters,
        "daily": raw_daily,
    }


def _read_cache(path: Path, request_url: str, parameter: str) -> dict | None:
    """Load a cache entry; return None on any miss, corruption or mismatch."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
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
        or not isinstance(entry.get("header"), dict)
        or not isinstance(entry.get("parameters"), dict)
        or not isinstance(entry.get("daily"), dict)
    ):
        return None
    # Re-shape into a POWER payload so the same validation runs on cache hits,
    # keeping retrieved_at for the source metadata.
    return {
        "retrieved_at": entry["retrieved_at"],
        "header": entry["header"],
        "parameters": entry["parameters"],
        "properties": {"parameter": {parameter: entry["daily"]}},
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
