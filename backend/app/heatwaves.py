"""Heatwave episodes and descriptive annual trends for the EarthScope API.

``GET /api/heatwaves`` reports runs of consecutive UTC calendar days whose
daily maximum temperature (T2M_MAX) is strictly above a user threshold and that
last at least ``min_duration`` days.

Method notes
------------
* The daily mapping is the exact return value of :func:`app.power.load_daily`
  (``YYYYMMDD`` UTC keys to finite floats; missing, non-finite and fill days
  already omitted).  Ingestion is shared with every other analysis route
  through ``app.main._load_point``; this module never contacts NASA POWER and
  never fills, interpolates or substitutes a day.
* A day participates when ``T2M_MAX > threshold``.  A day exactly at the
  threshold is not a heatwave day, mirroring ``hot_days`` in
  :mod:`app.extremes`.  A missing day breaks the sequence instead of bridging
  it: consecutiveness cannot be confirmed across an absent observation.
* Detection runs over the whole requested window, so a spell that starts on
  Dec 31 and continues into the following January is one episode with one
  duration.  Every episode is attributed entirely to the year of its first
  day: ``count``, ``total_days``, ``max_duration`` and ``max_tmax`` never split
  an episode across two rows.  ``total_days`` therefore counts days inside the
  episodes that *started* in that year, some of which may fall in the next
  January.
* Outer-window censoring is real and disclosed, never hidden: an episode that
  begins on the first day of ``start_year`` or ends on the last day of
  ``end_year`` may continue outside the requested years.  Its duration is then
  a lower bound and the response says so in ``warnings``.  A clean window
  carries no censoring warning.
* Completeness is strict.  A year contributes yearly statistics only when every
  one of its 365/366 UTC calendar days is present.  An incomplete year keeps
  its ``valid_days``/``expected_days`` diagnostics but reports null ``count``,
  ``total_days``, ``max_duration``, ``mean_duration`` and ``max_tmax``, and is
  excluded from every trend fit.  Episodes that start in such a year still
  appear in ``episodes`` because they describe the observations that do exist,
  and a warning states that the year is incomplete.
* A complete year with no episode reports real zeros (``count``,
  ``total_days``, ``max_duration`` = 0) with null ``mean_duration`` and
  ``max_tmax``: there is no episode to average or maximise over.  Zero is an
  observation, not a missing value, so it enters the trend fits.
* Trends are descriptive ordinary least squares of the annual aggregate on
  calendar year, scaled to a decade.  Only ``per_decade``, ``years_used`` and
  ``residual_lag1`` are reported: no confidence interval, p-value or
  significance flag.  ``residual_lag1`` is the Pearson correlation of the OLS
  residuals at consecutive calendar-year pairs; it is null when fewer than
  three such pairs exist or the residuals carry no variation.

Limitations: residuals are assumed independent and homoscedastic for ordinary
inference, which this endpoint deliberately does not attempt.  Absolute
thresholds carry different climatological meaning in different places, counts
are small integers, and POWER/MERRA-2 is a gridded reanalysis product, so a
grid-cell value is not a station measurement and every slope is an exploratory
association rather than a causal attribution.
"""

from __future__ import annotations

import calendar
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import analysis
from .schemas import (
    COVERAGE_MAX_YEAR,
    COVERAGE_MIN_YEAR,
    EXTREME_THRESHOLD_BOUNDS,
    MIN_TREND_YEARS,
    SourceInfo,
)

__all__ = [
    "DEFAULT_MIN_DURATION",
    "DEFAULT_THRESHOLD",
    "MIN_DURATION_MAX",
    "MIN_DURATION_MIN",
    "POWER_PARAMETER",
    "POWER_VARIABLE",
    "THRESHOLD_BOUNDS",
    "EpisodeScan",
    "HeatwaveEpisode",
    "HeatwaveResponse",
    "HeatwaveTrend",
    "HeatwaveTrends",
    "HeatwaveYear",
    "build_heatwave_response",
    "detect_episodes",
]

#: ``power.load_daily`` variable key and NASA POWER parameter for this route.
POWER_VARIABLE = "temperature_max"
POWER_PARAMETER = "T2M_MAX"
DAILY_UNIT = "C"

DEFAULT_THRESHOLD = 30.0
DEFAULT_MIN_DURATION = 3

#: A single hot day is a valid (if trivial) episode; a whole leap year is the
#: longest possible one.
MIN_DURATION_MIN = 1
MIN_DURATION_MAX = 366

#: T2M_MAX shares the ``hot_days`` temperature range at the extremes boundary,
#: so the same finite bounds are reused rather than re-declared.
THRESHOLD_BOUNDS: tuple[float, float] = EXTREME_THRESHOLD_BOUNDS["hot_days"]

#: Minimum number of consecutive-year residual pairs behind ``residual_lag1``.
#: Two pairs would only ever yield a degenerate correlation of +/-1.
MIN_LAG1_PAIRS = 3

#: A perfect linear fit leaves residuals at round-off scale; that is numerical
#: zero, not a correlation, so lag-1 is withheld instead of reporting noise.
_LAG1_NOISE_EPSILON = 1e-10


def _require_finite(value: float, field: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


class HeatwaveEpisode(BaseModel):
    """One qualifying run of consecutive UTC days above the threshold."""

    start: date
    end: date
    duration: int = Field(ge=1)
    max_tmax: float
    mean_tmax: float
    max_excess: float

    @field_validator("max_tmax", "mean_tmax", "max_excess")
    @classmethod
    def _finite_temperature(cls, value: float) -> float:
        return _require_finite(value, "episode temperature")

    @model_validator(mode="after")
    def _coherent_episode(self) -> "HeatwaveEpisode":
        if self.end < self.start:
            raise ValueError("start must not be after end")
        if self.duration != (self.end - self.start).days + 1:
            raise ValueError("duration must equal the inclusive day count")
        if self.mean_tmax > self.max_tmax:
            raise ValueError("mean_tmax must not exceed max_tmax")
        return self


class HeatwaveYear(BaseModel):
    """One UTC calendar year of heatwave diagnostics.

    A complete year carries an episode count, total days and longest duration,
    including zero.  ``mean_duration`` and ``max_tmax`` stay null for a
    zero-episode year because there is no episode to average or maximise over.
    An incomplete year carries only its day counts.
    """

    year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    count: int | None = Field(default=None, ge=0)
    total_days: int | None = Field(default=None, ge=0)
    max_duration: int | None = Field(default=None, ge=0)
    mean_duration: float | None = Field(default=None, ge=1.0)
    max_tmax: float | None = None
    valid_days: int = Field(ge=0, le=366)
    expected_days: int = Field(ge=365, le=366)

    @field_validator("mean_duration", "max_tmax")
    @classmethod
    def _finite_point(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "heatwave yearly statistic")

    @model_validator(mode="after")
    def _coherent_year(self) -> "HeatwaveYear":
        annual_days = 366 if calendar.isleap(self.year) else 365
        if self.expected_days != annual_days:
            raise ValueError("expected_days must equal the calendar days of the year")
        if self.valid_days > self.expected_days:
            raise ValueError("valid_days must not exceed expected_days")

        statistics = (
            self.count,
            self.total_days,
            self.max_duration,
            self.mean_duration,
            self.max_tmax,
        )
        if self.valid_days != self.expected_days:
            if any(value is not None for value in statistics):
                raise ValueError("an incomplete year must have null heatwave statistics")
            return self

        if self.count is None or self.total_days is None or self.max_duration is None:
            raise ValueError(
                "a complete year must report an episode count, total days and "
                "longest duration"
            )
        if self.count == 0:
            if self.total_days != 0 or self.max_duration != 0:
                raise ValueError("a complete year without episodes reports zeros")
            if self.mean_duration is not None or self.max_tmax is not None:
                raise ValueError(
                    "a complete year without episodes has no mean duration or "
                    "maximum temperature"
                )
            return self

        if self.total_days < self.count:
            raise ValueError("total_days must be at least one day per episode")
        if not 1 <= self.max_duration <= self.total_days:
            raise ValueError("max_duration must be within the episode durations")
        if self.mean_duration is None or self.max_tmax is None:
            raise ValueError(
                "a year with episodes must report a mean duration and a maximum "
                "temperature"
            )
        expected_mean = self.total_days / self.count
        if not math.isclose(
            self.mean_duration, expected_mean, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("mean_duration must equal total_days / count")
        if self.mean_duration > self.max_duration + 1e-9:
            raise ValueError("mean_duration must not exceed max_duration")
        return self


class HeatwaveTrend(BaseModel):
    """Descriptive OLS slope plus the lag-1 residual diagnostic.

    Deliberately carries no interval, p-value or significance flag: the trend
    is descriptive only.
    """

    per_decade: float
    years_used: int = Field(ge=MIN_TREND_YEARS)
    residual_lag1: float | None = Field(default=None, ge=-1.0, le=1.0)

    @field_validator("per_decade", "residual_lag1")
    @classmethod
    def _finite_slope(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "heatwave trend statistic")


class HeatwaveTrends(BaseModel):
    """Trends for the three raw annual aggregates, all or none."""

    count: HeatwaveTrend | None
    total_days: HeatwaveTrend | None
    max_duration: HeatwaveTrend | None

    @model_validator(mode="after")
    def _coherent_trends(self) -> "HeatwaveTrends":
        entries = [self.count, self.total_days, self.max_duration]
        present = [entry for entry in entries if entry is not None]
        if present and len(present) != len(entries):
            raise ValueError("every heatwave trend must be present or all must be null")
        if len({entry.years_used for entry in present}) > 1:
            raise ValueError("all heatwave trends must be fitted on the same years")
        return self


class HeatwaveResponse(BaseModel):
    """Response of ``GET /api/heatwaves``.

    ``series`` has one row per requested year in ascending order and
    ``episodes`` is ordered by start date.  For every complete year the row
    agrees exactly with the episodes that start in it; for an incomplete year
    the row is null while its episodes still describe the observed days.
    """

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    threshold: float
    min_duration: int = Field(ge=MIN_DURATION_MIN, le=MIN_DURATION_MAX)
    source: SourceInfo
    series: list[HeatwaveYear]
    episodes: list[HeatwaveEpisode]
    trends: HeatwaveTrends
    warnings: list[str]

    @field_validator("latitude", "longitude", "threshold")
    @classmethod
    def _finite_value(cls, value: float) -> float:
        return _require_finite(value, "value")

    @model_validator(mode="after")
    def _coherent_response(self) -> "HeatwaveResponse":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")

        minimum, maximum = THRESHOLD_BOUNDS
        if self.threshold < minimum or self.threshold > maximum:
            raise ValueError(
                f"threshold must be between {minimum:g} and {maximum:g}"
            )

        expected_years = list(range(self.start_year, self.end_year + 1))
        if [row.year for row in self.series] != expected_years:
            raise ValueError("series must contain every requested year in ascending order")

        window_start = date(self.start_year, 1, 1)
        window_end = date(self.end_year, 12, 31)
        previous_end: date | None = None
        for episode in self.episodes:
            if not window_start <= episode.start <= episode.end <= window_end:
                raise ValueError("every episode must lie inside the requested window")
            if episode.duration < self.min_duration:
                raise ValueError("every episode must last at least min_duration days")
            if previous_end is not None and episode.start <= previous_end:
                raise ValueError("episodes must be ordered and must not overlap")
            if not math.isclose(
                episode.max_excess,
                episode.max_tmax - self.threshold,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise ValueError("max_excess must equal max_tmax - threshold")
            previous_end = episode.end

        by_start: dict[int, list[HeatwaveEpisode]] = defaultdict(list)
        for episode in self.episodes:
            by_start[episode.start.year].append(episode)

        for row in self.series:
            if row.count is None:
                continue
            episodes = by_start.get(row.year, [])
            if row.count != len(episodes):
                raise ValueError(
                    f"the {row.year} episode count must match the episodes that "
                    "start that year"
                )
            if row.total_days != sum(episode.duration for episode in episodes):
                raise ValueError(
                    f"the {row.year} total_days must match its episode durations"
                )
            if row.max_duration != max(
                (episode.duration for episode in episodes), default=0
            ):
                raise ValueError(
                    f"the {row.year} max_duration must match its episodes"
                )
            hottest = max((episode.max_tmax for episode in episodes), default=None)
            if row.max_tmax != hottest:
                raise ValueError(
                    f"the {row.year} max_tmax must match its episodes"
                )

        complete_years = len(expected_years) - len(
            [row for row in self.series if row.count is None]
        )
        trends = [self.trends.count, self.trends.total_days, self.trends.max_duration]
        if all(trend is None for trend in trends):
            if complete_years >= MIN_TREND_YEARS:
                raise ValueError(
                    "heatwave trends are required when at least "
                    f"{MIN_TREND_YEARS} complete years are available"
                )
        else:
            if complete_years < MIN_TREND_YEARS:
                raise ValueError(
                    f"heatwave trends require at least {MIN_TREND_YEARS} complete years"
                )
            for trend in trends:
                if trend is None:  # all-or-none is enforced by HeatwaveTrends
                    raise ValueError("every heatwave trend must be present together")
                if trend.years_used != complete_years:
                    raise ValueError(
                        "trend.years_used must equal the complete years in series"
                    )
        return self


@dataclass(frozen=True, slots=True)
class EpisodeScan:
    """Result of one pass over a validated daily series.

    ``episodes`` is what the response publishes.  The counters exist so the
    warnings can disclose every decision that removed or truncated evidence.
    """

    episodes: tuple[HeatwaveEpisode, ...]
    valid_days: Mapping[int, int]
    expected_days: Mapping[int, int]
    censored_start: int
    censored_end: int
    censored_short: int
    uncertain_edge: int


def _validate_year(name: str, year: Any) -> int:
    if isinstance(year, bool) or not isinstance(year, int):
        raise analysis.DataContractError(f"{name} must be an integer")
    if not COVERAGE_MIN_YEAR <= year <= COVERAGE_MAX_YEAR:
        raise analysis.DataContractError(
            f"{name} must be within [{COVERAGE_MIN_YEAR}, {COVERAGE_MAX_YEAR}]"
        )
    return year


def _validate_threshold(threshold: Any) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise analysis.DataContractError("threshold must be numeric")
    try:
        value = float(threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise analysis.DataContractError("threshold must be numeric") from exc
    if not math.isfinite(value):
        raise analysis.DataContractError("threshold must be finite")
    minimum, maximum = THRESHOLD_BOUNDS
    if value < minimum or value > maximum:
        raise analysis.DataContractError(
            f"threshold must be between {minimum:g} and {maximum:g}"
        )
    return value


def _validate_min_duration(min_duration: Any) -> int:
    if isinstance(min_duration, bool) or not isinstance(min_duration, int):
        raise analysis.DataContractError("min_duration must be an integer")
    if not MIN_DURATION_MIN <= min_duration <= MIN_DURATION_MAX:
        raise analysis.DataContractError(
            f"min_duration must be within "
            f"[{MIN_DURATION_MIN}, {MIN_DURATION_MAX}] days"
        )
    return min_duration


def _validate_coordinate(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise analysis.DataContractError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise analysis.DataContractError(f"{name} must be finite")
    limit = 90.0 if name == "latitude" else 180.0
    if not -limit <= number <= limit:
        raise analysis.DataContractError(f"{name} must be within [-{limit:g}, {limit:g}]")
    return number


def _daily_points(
    daily: Mapping[str, Any], start_year: int, end_year: int
) -> list[tuple[date, float]]:
    """Return the present days of the window as ascending (date, value) pairs.

    The shared daily-frame cleaner rejects impossible dates, non-numeric and
    non-finite values and duplicate stamps, so every returned day is a real UTC
    calendar date with one finite observation.
    """

    if not isinstance(daily, Mapping):
        raise analysis.DataContractError("daily observations must be a mapping")

    frame = analysis._daily_frame(daily)
    if frame.empty:
        return []

    stamps = frame["date"]
    values = frame["value"].to_numpy(dtype="float64")
    window_start = datetime.combine(
        date(start_year, 1, 1), datetime.min.time(), tzinfo=timezone.utc
    )
    window_end = datetime.combine(
        date(end_year, 12, 31), datetime.max.time(), tzinfo=timezone.utc
    )

    points: list[tuple[date, float]] = []
    for stamp, value in zip(stamps.tolist(), values.tolist(), strict=True):
        if not isinstance(stamp, datetime) or not window_start <= stamp <= window_end:
            continue
        points.append((stamp.date(), float(value)))
    points.sort(key=lambda point: point[0])
    return points


def _detect_runs(
    days: Sequence[tuple[date, float]], threshold: float
) -> list[tuple[int, int]]:
    """Return inclusive index ranges of consecutive calendar days above threshold."""

    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(days):
        if days[index][1] <= threshold:
            index += 1
            continue
        last = index
        while (
            last + 1 < len(days)
            and days[last + 1][1] > threshold
            and days[last + 1][0] - days[last][0] == timedelta(days=1)
        ):
            last += 1
        runs.append((index, last))
        index = last + 1
    return runs


def detect_episodes(
    daily: Mapping[str, Any],
    threshold: float,
    min_duration: int,
    start_year: int,
    end_year: int,
) -> EpisodeScan:
    """Detect qualifying episodes and the day coverage behind them.

    The comparison is strict (``>``) and a missing day breaks the sequence
    instead of bridging it.  Episodes are attributed to their start year.
    Nothing is imputed: a run shorter than ``min_duration`` is not an episode,
    and the returned counters record boundary censoring instead of hiding it.
    """

    resolved_threshold = _validate_threshold(threshold)
    resolved_min = _validate_min_duration(min_duration)
    start = _validate_year("start_year", start_year)
    end = _validate_year("end_year", end_year)
    if start > end:
        raise analysis.DataContractError("start_year must not exceed end_year")

    days = _daily_points(daily, start, end)
    observations = Counter(day.year for day, _ in days)
    expected_days = {
        year: 366 if calendar.isleap(year) else 365
        for year in range(start, end + 1)
    }
    valid_days = {year: observations.get(year, 0) for year in expected_days}

    window_start = date(start, 1, 1)
    window_end = date(end, 12, 31)
    episodes: list[HeatwaveEpisode] = []
    censored_start = 0
    censored_end = 0
    censored_short = 0
    uncertain_edge = 0

    for first, last in _detect_runs(days, resolved_threshold):
        run_start = days[first][0]
        run_end = days[last][0]
        duration = (run_end - run_start).days + 1
        touches_start = run_start == window_start
        touches_end = run_end == window_end
        missing_before = (
            first > 0 and run_start - days[first - 1][0] > timedelta(days=1)
        )
        missing_after = (
            last + 1 < len(days)
            and days[last + 1][0] - run_end > timedelta(days=1)
        )

        if duration < resolved_min:
            if touches_start or touches_end:
                censored_short += 1
            continue

        temperatures = [value for _, value in days[first : last + 1]]
        if not all(math.isfinite(value) for value in temperatures):
            raise analysis.DataContractError(
                "a heatwave run contains a non-finite temperature"
            )
        hottest = max(temperatures)
        episodes.append(
            HeatwaveEpisode(
                start=run_start,
                end=run_end,
                duration=duration,
                max_tmax=hottest,
                mean_tmax=math.fsum(temperatures) / len(temperatures),
                max_excess=hottest - resolved_threshold,
            )
        )
        if touches_start:
            censored_start += 1
        if touches_end:
            censored_end += 1
        if missing_before or missing_after:
            uncertain_edge += 1

    return EpisodeScan(
        episodes=tuple(episodes),
        valid_days=valid_days,
        expected_days=expected_days,
        censored_start=censored_start,
        censored_end=censored_end,
        censored_short=censored_short,
        uncertain_edge=uncertain_edge,
    )


def _series_rows(scan: EpisodeScan, start_year: int, end_year: int) -> list[HeatwaveYear]:
    """Build one row per requested year, with strict completeness rules."""

    by_start: dict[int, list[HeatwaveEpisode]] = defaultdict(list)
    for episode in scan.episodes:
        by_start[episode.start.year].append(episode)

    rows: list[HeatwaveYear] = []
    for year in range(start_year, end_year + 1):
        expected = scan.expected_days[year]
        valid = scan.valid_days[year]
        if valid != expected:
            rows.append(
                HeatwaveYear(
                    year=year,
                    valid_days=valid,
                    expected_days=expected,
                )
            )
            continue

        episodes = by_start.get(year, [])
        total_days = sum(episode.duration for episode in episodes)
        rows.append(
            HeatwaveYear(
                year=year,
                count=len(episodes),
                total_days=total_days,
                max_duration=max(
                    (episode.duration for episode in episodes), default=0
                ),
                mean_duration=total_days / len(episodes) if episodes else None,
                max_tmax=max(
                    (episode.max_tmax for episode in episodes), default=None
                ),
                valid_days=valid,
                expected_days=expected,
            )
        )
    return rows


def _residual_lag1(
    aggregates: Sequence[analysis.AnnualAggregate],
    fitted_by_year: Mapping[int, float],
) -> float | None:
    """Pearson correlation of OLS residuals at consecutive year pairs."""

    years = np.asarray([item.year for item in aggregates], dtype="float64")
    observed = np.asarray(
        [float(item.value) for item in aggregates], dtype="float64"
    )
    try:
        modelled = np.asarray(
            [float(fitted_by_year[item.year]) for item in aggregates],
            dtype="float64",
        )
    except KeyError as exc:
        raise analysis.DataContractError(
            "a fitted value is missing for a complete heatwave year"
        ) from exc

    residuals = observed - modelled
    adjacent = np.diff(years) == 1.0
    if int(np.count_nonzero(adjacent)) < MIN_LAG1_PAIRS:
        return None

    signal = float(np.linalg.norm(observed - float(observed.mean())))
    if float(np.linalg.norm(residuals)) <= _LAG1_NOISE_EPSILON * max(signal, 1.0):
        # A perfect linear fit leaves only round-off: no correlation to report.
        return None

    before = residuals[:-1][adjacent]
    after = residuals[1:][adjacent]
    if float(np.std(before)) <= 0.0 or float(np.std(after)) <= 0.0:
        return None
    correlation = float(np.corrcoef(before, after)[0, 1])
    if not math.isfinite(correlation):
        return None
    return max(-1.0, min(1.0, correlation))


def _metric_values(rows: Sequence[HeatwaveYear], attribute: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw = getattr(row, attribute)
        if raw is None:
            raise analysis.DataContractError(
                f"{attribute} is missing for the complete year {row.year}"
            )
        values.append(float(raw))
    return values


def _fit_trend(rows: Sequence[HeatwaveYear], values: Sequence[float]) -> HeatwaveTrend | None:
    """Descriptive OLS of one annual aggregate on calendar year."""

    if len(rows) < MIN_TREND_YEARS:
        return None
    if len(rows) != len(values):
        raise analysis.DataContractError("heatwave trend inputs must align")

    aggregates = [
        analysis.AnnualAggregate(
            year=row.year,
            value=value,
            valid_days=row.valid_days,
            expected_days=row.expected_days,
        )
        for row, value in zip(rows, values, strict=True)
    ]
    try:
        statistics, fitted_by_year, _warnings = analysis.fit_trend(aggregates)
    except analysis.DataContractError:
        raise
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise analysis.DataContractError(
            f"heatwave trend fitting failed: {' '.join(str(exc).split())}"
        ) from exc
    if statistics is None:
        return None
    return HeatwaveTrend(
        per_decade=statistics.per_decade,
        years_used=statistics.years_used,
        residual_lag1=_residual_lag1(aggregates, fitted_by_year),
    )


def _normalise_source(source: Any) -> SourceInfo:
    """Validate that the ingestion metadata describes UTC T2M_MAX in Celsius."""

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
    if info.parameter != POWER_PARAMETER:
        raise analysis.DataContractError(
            f"source.parameter must be {POWER_PARAMETER!r} for heatwaves"
        )
    if info.daily_unit != DAILY_UNIT:
        raise analysis.DataContractError(
            f"source.daily_unit must be {DAILY_UNIT!r} for heatwaves"
        )
    if info.time_standard != "UTC":
        raise analysis.DataContractError("source.time_standard must be 'UTC'")

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
    return info.model_copy(
        update={
            "retrieved_at": retrieved.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        }
    )


def _warnings(
    scan: EpisodeScan,
    rows: Sequence[HeatwaveYear],
    trends: HeatwaveTrends,
    start_year: int,
    end_year: int,
    min_duration: int,
) -> list[str]:
    warnings: list[str] = []

    incomplete = [row.year for row in rows if row.count is None]
    if incomplete:
        warnings.append(
            "Incomplete or invalid daily coverage prevented heatwave statistics "
            f"for {len(incomplete)} requested year(s): "
            f"{analysis._format_year_list(incomplete)}. Episodes in those years "
            "describe only the available observations and are excluded from the "
            "yearly totals and from every trend."
        )

    if scan.censored_start:
        warnings.append(
            f"Outer-window censoring: {scan.censored_start} episode(s) begin on "
            f"{start_year:04d}-01-01, the first day of the requested window, so "
            "their duration and totals are lower bounds; the heatwave may have "
            "started before the window."
        )
    if scan.censored_end:
        warnings.append(
            f"Outer-window censoring: {scan.censored_end} episode(s) reach "
            f"{end_year:04d}-12-31, the last day of the requested window, so "
            "their duration and totals are lower bounds; the heatwave may "
            "continue after the window."
        )
    if scan.censored_short:
        warnings.append(
            f"Outer-window censoring: {scan.censored_short} exceedance run(s) "
            f"shorter than min_duration={min_duration} touch the window boundary "
            "and are not counted; they may be part of a longer episode outside "
            "the requested years."
        )

    if scan.uncertain_edge:
        warnings.append(
            f"{scan.uncertain_edge} episode(s) start or end next to a day with no "
            "observation. That day breaks the sequence, so the reported start or "
            "end is bounded by the available observations rather than confirmed."
        )

    if trends.count is None:
        complete_years = len([row for row in rows if row.count is not None])
        warnings.append(
            f"Heatwave trends require at least {MIN_TREND_YEARS} complete years; "
            f"{complete_years} available."
        )
    return warnings


def build_heatwave_response(
    *,
    daily: Mapping[str, Any],
    source: Any,
    latitude: float,
    longitude: float,
    start_year: int,
    end_year: int,
    threshold: float,
    min_duration: int,
) -> HeatwaveResponse:
    """Build the complete heatwave DTO from one validated daily series."""

    resolved_threshold = _validate_threshold(threshold)
    resolved_min = _validate_min_duration(min_duration)
    start = _validate_year("start_year", start_year)
    end = _validate_year("end_year", end_year)
    if start > end:
        raise analysis.DataContractError("start_year must not exceed end_year")
    resolved_latitude = _validate_coordinate("latitude", latitude)
    resolved_longitude = _validate_coordinate("longitude", longitude)
    source_info = _normalise_source(source)

    scan = detect_episodes(daily, resolved_threshold, resolved_min, start, end)
    rows = _series_rows(scan, start, end)
    complete_rows = [row for row in rows if row.count is not None]
    trends = HeatwaveTrends(
        count=_fit_trend(complete_rows, _metric_values(complete_rows, "count")),
        total_days=_fit_trend(
            complete_rows, _metric_values(complete_rows, "total_days")
        ),
        max_duration=_fit_trend(
            complete_rows, _metric_values(complete_rows, "max_duration")
        ),
    )
    warnings = _warnings(
        scan, rows, trends, start, end, resolved_min
    )

    try:
        return HeatwaveResponse(
            latitude=resolved_latitude,
            longitude=resolved_longitude,
            start_year=start,
            end_year=end,
            threshold=resolved_threshold,
            min_duration=resolved_min,
            source=source_info,
            series=rows,
            episodes=list(scan.episodes),
            trends=trends,
            warnings=warnings,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise analysis.DataContractError(
            f"heatwave response does not satisfy the API contract: {exc}"
        ) from exc
