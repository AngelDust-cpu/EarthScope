"""Extreme-event aggregation for the EarthScope 2.4 API.

``GET /api/extremes`` counts days that strictly exceed (or fall below) a
user-supplied threshold for one point and one inclusive UTC year range:

* ``hot_days``    uses T2M_MAX (``temperature_max``) with ``>``  (°C);
* ``cold_nights`` uses T2M_MIN (``temperature_min``) with ``<``  (°C);
* ``heavy_rain``  uses PRECTOTCORR (``precipitation``) with ``>`` (mm/day).

The daily mapping is the exact return value of :func:`app.power.load_daily`
(``YYYYMMDD`` UTC keys to finite floats, invalid/missing/fill days already
omitted).  Ingestion is shared with every other analysis route through
``app.main._load_point``; this module never contacts NASA POWER and never
substitutes or fills a day.

Completeness is strict: a year contributes an event count only when every one
of its 365/366 UTC calendar days is present.  An incomplete year keeps its
``valid_days``/``expected_days``/``excluded_days`` diagnostics but reports
null events, null fraction and no fitted value.  A zero count from a complete
year is a real observation, not a missing value.

Trend statistics are owned by ``backend/app/extremes_stats.py`` (Codex).  Its
``fit_extremes(aggregates) -> (trend_dict_or_none, fitted_by_year)`` receives
the existing :class:`app.analysis.AnnualAggregate` rows whose ``value`` is the
raw full-year event count (or ``None``).  The fitted map holds the primary
normalised rate in event days per 365 days.  This module validates that return
value defensively so a malformed statistics result becomes a
``DataContractError`` (HTTP 502) rather than a partial response.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from . import analysis
from .schemas import (
    COVERAGE_MAX_YEAR,
    COVERAGE_MIN_YEAR,
    EXTREME_EVENTS,
    EXTREME_EVENT_OPERATORS,
    EXTREME_EVENT_UNITS,
    EXTREME_THRESHOLD_BOUNDS,
    MIN_TREND_YEARS,
    ExtremeSeriesPoint,
    ExtremeTrend,
    ExtremesResponse,
    SourceInfo,
)

__all__ = [
    "EVENTS",
    "aggregate_extremes",
    "build_extremes_response",
    "fit_extremes",
    "power_variable",
]

#: Public event identifiers, in contract order.
EVENTS = EXTREME_EVENTS

#: ``power.load_daily`` variable key for each event.  The Data role extends
#: that module with ``temperature_max`` (T2M_MAX) and ``temperature_min``
#: (T2M_MIN); ``precipitation`` already exists.
_POWER_VARIABLE_BY_EVENT: dict[str, str] = {
    "hot_days": "temperature_max",
    "cold_nights": "temperature_min",
    "heavy_rain": "precipitation",
}

#: NASA POWER parameter name expected in the source metadata.
_PARAMETER_BY_EVENT: dict[str, str] = {
    "hot_days": "T2M_MAX",
    "cold_nights": "T2M_MIN",
    "heavy_rain": "PRECTOTCORR",
}

#: Daily unit expected in the source metadata.
_DAILY_UNIT_BY_EVENT: dict[str, str] = {
    "hot_days": "C",
    "cold_nights": "C",
    "heavy_rain": "mm/day",
}


def _extremes_stats() -> Any:
    """Return the Codex-owned ``app.extremes_stats`` module.

    Imported lazily so the app and every other route stay importable while the
    statistics module is being delivered.
    """

    try:
        from . import extremes_stats
    except ModuleNotFoundError as exc:
        if exc.name not in ("app.extremes_stats", "extremes_stats"):
            raise
        raise analysis.DataContractError(
            "backend/app/extremes_stats.py is not installed; extreme-event "
            "trend statistics are unavailable."
        ) from exc
    return extremes_stats


def fit_extremes(
    aggregates: list[analysis.AnnualAggregate],
) -> tuple[dict[str, Any] | None, Mapping[int, float]]:
    """Delegate the trend fit to the Codex-owned statistics helper.

    Kept as a module-level function so tests can substitute the agreed
    contract without importing an unfinished statistics module.
    """

    return _extremes_stats().fit_extremes(aggregates)  # type: ignore[no-any-return]


def power_variable(event: str) -> str:
    """Return the shared ``load_daily`` variable key for an event."""

    try:
        return _POWER_VARIABLE_BY_EVENT[event]
    except KeyError as exc:
        raise analysis.DataContractError(f"unsupported extreme event: {event!r}") from exc


def _validate_year(name: str, year: Any) -> int:
    if isinstance(year, bool) or not isinstance(year, int):
        raise analysis.DataContractError(f"{name} must be an integer")
    if not COVERAGE_MIN_YEAR <= year <= COVERAGE_MAX_YEAR:
        raise analysis.DataContractError(
            f"{name} must be within [{COVERAGE_MIN_YEAR}, {COVERAGE_MAX_YEAR}]"
        )
    return year


def _validate_event(event: Any) -> str:
    if event not in _POWER_VARIABLE_BY_EVENT:
        raise analysis.DataContractError(
            "event must be one of " + ", ".join(EXTREME_EVENTS)
        )
    return event


def _validate_threshold(event: str, threshold: Any) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise analysis.DataContractError("threshold must be numeric")
    try:
        value = float(threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise analysis.DataContractError("threshold must be numeric") from exc
    if not math.isfinite(value):
        raise analysis.DataContractError("threshold must be finite")
    minimum, maximum = EXTREME_THRESHOLD_BOUNDS[event]
    if value < minimum or value > maximum:
        raise analysis.DataContractError(
            f"threshold must be between {minimum:g} and {maximum:g} for {event}"
        )
    return value


def _validate_common(
    daily: Any,
    event: Any,
    threshold: Any,
    start_year: Any,
    end_year: Any,
) -> tuple[str, float, int, int]:
    if not isinstance(daily, Mapping):
        raise analysis.DataContractError("daily observations must be a mapping")
    resolved_event = _validate_event(event)
    resolved_threshold = _validate_threshold(resolved_event, threshold)
    start = _validate_year("start_year", start_year)
    end = _validate_year("end_year", end_year)
    if start > end:
        raise analysis.DataContractError("start_year must not exceed end_year")
    return resolved_event, resolved_threshold, start, end


def _is_event(event: str, value: float, threshold: float) -> bool:
    """Strict comparison: a day exactly at the threshold is never counted."""

    if EXTREME_EVENT_OPERATORS[event] == ">":
        return value > threshold
    return value < threshold


def aggregate_extremes(
    daily: Mapping[str, Any],
    event: str,
    threshold: float,
    start_year: int,
    end_year: int,
) -> list[analysis.AnnualAggregate]:
    """Aggregate strict threshold-exceedance days per UTC calendar year.

    Returns one :class:`app.analysis.AnnualAggregate` per requested year, in
    ascending order, with ``value`` equal to the raw event count for a complete
    year (including zero) or ``None`` for an incomplete year.  Keys that are
    not real ``YYYYMMDD`` dates, non-numeric values and duplicate dates are
    handled by the shared daily-frame cleaner; no day is filled.  For
    ``heavy_rain`` a negative rate is treated as an invalid day, matching the
    ingestion boundary.
    """

    resolved_event, resolved_threshold, start, end = _validate_common(
        daily, event, threshold, start_year, end_year
    )

    frame = analysis._daily_frame(daily)
    counts: dict[int, int] = {}
    hits: dict[int, int] = {}

    if not frame.empty:
        frame = frame.assign(year=frame["date"].dt.year.astype("int64"))
        frame = frame.loc[(frame["year"] >= start) & (frame["year"] <= end)]
        if resolved_event == "heavy_rain":
            frame = frame.loc[frame["value"] >= 0.0]

        if not frame.empty:
            years = frame["year"].tolist()
            values = frame["value"].tolist()
            for year_value, raw_value in zip(years, values, strict=True):
                year = int(year_value)
                value = float(raw_value)
                counts[year] = counts.get(year, 0) + 1
                if _is_event(resolved_event, value, resolved_threshold):
                    hits[year] = hits.get(year, 0) + 1

    aggregates: list[analysis.AnnualAggregate] = []
    for year in range(start, end + 1):
        expected_days = 366 if calendar.isleap(year) else 365
        valid_days = counts.get(year, 0)
        value: float | None = None
        if valid_days == expected_days:
            event_count = hits.get(year, 0)
            if 0 <= event_count <= valid_days:
                value = float(event_count)
        aggregates.append(
            analysis.AnnualAggregate(
                year=year,
                value=value,
                valid_days=valid_days,
                expected_days=expected_days,
            )
        )
    return aggregates


def _normalise_source(source: Any, event: str) -> SourceInfo:
    """Validate the shared ingestion metadata for the event's parameter."""

    if isinstance(source, SourceInfo):
        info = source
    elif isinstance(source, Mapping):
        try:
            info = SourceInfo.model_validate(dict(source))
        except (ValidationError, TypeError, ValueError) as exc:
            raise analysis.DataContractError(
                "NASA POWER source metadata is incomplete or invalid"
            ) from exc
    else:
        raise analysis.DataContractError("NASA POWER source metadata is missing")

    if not info.name.upper().startswith("NASA POWER"):
        raise analysis.DataContractError("source.name must identify NASA POWER")
    expected_parameter = _PARAMETER_BY_EVENT[event]
    if info.parameter != expected_parameter:
        raise analysis.DataContractError(
            f"source.parameter must be {expected_parameter!r} for {event}"
        )
    expected_unit = _DAILY_UNIT_BY_EVENT[event]
    if info.daily_unit != expected_unit:
        raise analysis.DataContractError(
            f"source.daily_unit must be {expected_unit!r} for {event}"
        )

    parsed_url = urlsplit(info.request_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise analysis.DataContractError("source.request_url must be an HTTPS URL")

    stamp = info.retrieved_at.strip()
    iso_candidate = f"{stamp[:-1]}+00:00" if stamp.endswith(("Z", "z")) else stamp
    try:
        retrieved = datetime.fromisoformat(iso_candidate)
    except ValueError as exc:
        raise analysis.DataContractError("source.retrieved_at must be ISO 8601") from exc
    if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0):
        raise analysis.DataContractError("source.retrieved_at must be in UTC")
    normalised_stamp = (
        retrieved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    return info.model_copy(update={"retrieved_at": normalised_stamp})


def _coerce_fitted(
    raw: Any, complete_years: set[int], *, require_all: bool
) -> dict[int, float]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError(
            "fit_extremes must return a mapping of fitted values by year"
        )

    fitted: dict[int, float] = {}
    for key, value in raw.items():
        if isinstance(key, (str, bytes, bool)):
            raise analysis.DataContractError(
                "fit_extremes returned a non-integer fitted year"
            )
        try:
            year = int(key)
        except (TypeError, ValueError, OverflowError) as exc:
            raise analysis.DataContractError(
                "fit_extremes returned a non-integer fitted year"
            ) from exc
        if isinstance(value, (str, bytes, bool)):
            raise analysis.DataContractError(
                f"fit_extremes returned a non-numeric fitted value for {year}"
            )
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise analysis.DataContractError(
                f"fit_extremes returned a non-numeric fitted value for {year}"
            ) from exc
        if not math.isfinite(number):
            raise analysis.DataContractError(
                f"fit_extremes returned a non-finite fitted value for {year}"
            )
        fitted[year] = number

    if set(fitted) != complete_years:
        if require_all or fitted:
            raise analysis.DataContractError(
                "fit_extremes must return fitted values exactly for complete years"
            )
    return fitted


def _coerce_trend(
    raw: Any,
    complete_years: set[int],
) -> ExtremeTrend | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise analysis.DataContractError(
            "fit_extremes must return a trend mapping or None"
        )
    try:
        return ExtremeTrend.model_validate(dict(raw))
    except (ValidationError, TypeError, ValueError) as exc:
        raise analysis.DataContractError(
            f"fit_extremes returned an invalid trend: {exc}"
        ) from exc


def _fit(aggregates: list[analysis.AnnualAggregate]) -> tuple[Any, Any]:
    try:
        result = fit_extremes(aggregates)
    except analysis.DataContractError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise analysis.DataContractError(
            f"fit_extremes failed: {' '.join(str(exc).split())}"
        ) from exc
    if not isinstance(result, tuple) or len(result) != 2:
        raise analysis.DataContractError(
            "fit_extremes must return a (trend, fitted_by_year) tuple"
        )
    return result


def build_extremes_response(
    *,
    daily: Mapping[str, Any],
    source: Any,
    latitude: float,
    longitude: float,
    event: str,
    threshold: float,
    start_year: int,
    end_year: int,
) -> ExtremesResponse:
    """Build the complete extreme-event DTO from one validated daily series.

    The raw counts go to the shared statistics helper; only the response shape
    and the strict completeness rules are owned here.
    """

    resolved_event, resolved_threshold, start, end = _validate_common(
        daily, event, threshold, start_year, end_year
    )
    source_info = _normalise_source(source, resolved_event)
    aggregates = aggregate_extremes(
        daily, resolved_event, resolved_threshold, start, end
    )

    complete_years = {
        item.year for item in aggregates if item.value is not None
    }
    trend_raw, fitted_raw = _fit(aggregates)
    trend = _coerce_trend(trend_raw, complete_years)
    fitted_by_year = _coerce_fitted(
        fitted_raw, complete_years, require_all=trend is not None
    )

    if trend is None and fitted_by_year:
        raise analysis.DataContractError("fitted values require a trend")

    points: list[ExtremeSeriesPoint] = []
    for item in aggregates:
        events: int | None = None
        fraction: float | None = None
        if item.value is not None:
            event_count = int(item.value)
            if not math.isclose(item.value, float(event_count), rel_tol=0.0, abs_tol=0.0):
                raise analysis.DataContractError(
                    "aggregate event counts must be whole numbers"
                )
            events = event_count
            fraction = event_count / item.valid_days
        points.append(
            ExtremeSeriesPoint(
                year=item.year,
                events=events,
                fraction=fraction,
                valid_days=item.valid_days,
                expected_days=item.expected_days,
                excluded_days=item.expected_days - item.valid_days,
                fitted=fitted_by_year.get(item.year),
            )
        )

    warnings: list[str] = []
    missing_years = [item.year for item in aggregates if item.value is None]
    if missing_years:
        warnings.append(
            "Incomplete or invalid daily coverage prevented an event count for "
            f"{len(missing_years)} requested year(s): "
            f"{analysis._format_year_list(missing_years)}."
        )
    if trend is None:
        warnings.append(
            f"A trend requires at least {MIN_TREND_YEARS} complete years; "
            f"only {len(complete_years)} available."
        )

    try:
        return ExtremesResponse(
            latitude=latitude,
            longitude=longitude,
            start_year=start,
            end_year=end,
            event=resolved_event,  # type: ignore[arg-type]
            threshold=resolved_threshold,
            threshold_unit=EXTREME_EVENT_UNITS[resolved_event],  # type: ignore[arg-type]
            operator=EXTREME_EVENT_OPERATORS[resolved_event],  # type: ignore[arg-type]
            source=source_info,
            warnings=warnings,
            series=points,
            trend=trend,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise analysis.DataContractError(
            f"extreme-event response does not satisfy the API contract: {exc}"
        ) from exc
