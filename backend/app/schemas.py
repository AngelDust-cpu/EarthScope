"""Pydantic models and shared constants for the EarthScope API.

The response models mirror ``docs/API.md``.  They are deliberately strict:
the API must never silently emit a partially populated trend response, a
non-finite statistic, or a series that skips a requested year.  Model
validators enforce those invariants at the HTTP boundary as well as in tests.
"""

from __future__ import annotations

import calendar
import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# The MVP only requests years that NASA POWER can return as complete UTC
# calendar years.  2026 is deliberately excluded until it is complete.
COVERAGE_MIN_YEAR = 1981
COVERAGE_MAX_YEAR = 2025
MIN_TREND_YEARS = 5
SIGNIFICANCE_ALPHA = 0.05
COMPARISON_METHOD = "paired_annual_difference_descriptive"

# Anomaly baseline: 1991-2020 is the documented 30-year preset.  Any shorter
# baseline is a user reference period, not a climate normal.
BASELINE_PRESET_START_YEAR = 1991
BASELINE_PRESET_END_YEAR = 2020
CLIMATE_NORMAL_YEARS = 30

# Seasonal periods.  "all" expands to the four meteorological seasons in
# calendar order; "m01".."m12" select a single UTC calendar month.
SEASONS: tuple[str, ...] = ("winter", "spring", "summer", "autumn")
MONTH_PERIODS: tuple[str, ...] = tuple(f"m{month:02d}" for month in range(1, 13))
ALL_PERIOD = "all"
SEASONAL_PERIODS: tuple[str, ...] = SEASONS + (ALL_PERIOD,) + MONTH_PERIODS

# Extreme-event contract (docs/EXTREMES-API.md).  The event determines which
# daily POWER parameter is ingested, the strict comparison operator, the
# response unit and the allowed threshold range.  Equality is never counted.
EXTREME_EVENTS: tuple[str, ...] = ("hot_days", "cold_nights", "heavy_rain")
EXTREME_EVENT_UNITS: dict[str, str] = {
    "hot_days": "°C",
    "cold_nights": "°C",
    "heavy_rain": "mm/day",
}
EXTREME_EVENT_OPERATORS: dict[str, str] = {
    "hot_days": ">",
    "cold_nights": "<",
    "heavy_rain": ">",
}
EXTREME_THRESHOLD_BOUNDS: dict[str, tuple[float, float]] = {
    "hot_days": (-100.0, 100.0),
    "cold_nights": (-100.0, 100.0),
    "heavy_rain": (0.0, 2000.0),
}

Variable = Literal["temperature", "precipitation"]
Aggregation = Literal["mean", "sum"]
SeasonCode = Literal[
    "winter",
    "spring",
    "summer",
    "autumn",
    "m01",
    "m02",
    "m03",
    "m04",
    "m05",
    "m06",
    "m07",
    "m08",
    "m09",
    "m10",
    "m11",
    "m12",
]
SeasonalPeriod = SeasonCode | Literal["all"]

ExtremeEvent = Literal["hot_days", "cold_nights", "heavy_rain"]
ExtremeOperator = Literal[">", "<"]
ExtremeMethod = Literal["ols", "hac"]
ExtremeUnit = Literal["°C", "mm/day"]


def _require_finite(value: float, field: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


class HealthResponse(BaseModel):
    status: Literal["ok"]


class CoverageResponse(BaseModel):
    min_year: int
    max_year: int
    min_years: int


class SeriesPoint(BaseModel):
    year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    value: float | None
    valid_days: int = Field(ge=0, le=366)
    # Annual series expect 365/366 days, but seasonal series reuse this DTO for
    # shorter periods (a winter or one month), so the bound is structural only.
    # ``TrendResponse`` re-checks the annual day count.
    expected_days: int = Field(ge=1, le=366)
    fitted: float | None

    @field_validator("value", "fitted")
    @classmethod
    def _finite_point(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "series value")

    @model_validator(mode="after")
    def _coherent_counts(self) -> "SeriesPoint":
        if self.valid_days > self.expected_days:
            raise ValueError("valid_days must not exceed expected_days")
        return self


class TrendStats(BaseModel):
    years_used: int = Field(ge=MIN_TREND_YEARS)
    per_decade: float
    ci95_low: float
    ci95_high: float
    p_value: float = Field(ge=0.0, le=1.0)
    significant: bool
    r_squared: float = Field(ge=0.0, le=1.0)

    @field_validator("per_decade", "ci95_low", "ci95_high", "p_value", "r_squared")
    @classmethod
    def _finite_statistic(cls, value: float) -> float:
        return _require_finite(value, "trend statistic")

    @model_validator(mode="after")
    def _consistent_interval(self) -> "TrendStats":
        if self.ci95_low > self.ci95_high:
            raise ValueError("ci95_low must not exceed ci95_high")
        if self.significant != (self.p_value < SIGNIFICANCE_ALPHA):
            raise ValueError("significant must match p_value at alpha 0.05")
        return self


class SourceInfo(BaseModel):
    name: str = Field(min_length=1)
    parameter: Literal["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]
    daily_unit: Literal["C", "mm/day"]
    dataset: str = Field(min_length=1)
    time_standard: Literal["UTC"]
    request_url: str = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)
    cached: bool


class ExtremeSensitivity(BaseModel):
    """One regression specification reported for the extreme-event trend.

    ``ols`` is the conventional ordinary-least-squares interval; ``hac`` is a
    Bartlett Newey-West specification with the stated lag count.
    """

    method: ExtremeMethod
    lags: int | None = Field(default=None, ge=1)
    ci95_low: float
    ci95_high: float
    p_value: float = Field(ge=0.0, le=1.0)

    @field_validator("ci95_low", "ci95_high", "p_value")
    @classmethod
    def _finite_statistic(cls, value: float) -> float:
        return _require_finite(value, "sensitivity statistic")

    @model_validator(mode="after")
    def _coherent_specification(self) -> "ExtremeSensitivity":
        if self.method == "ols":
            if self.lags is not None:
                raise ValueError("an OLS sensitivity entry must not set lags")
        elif self.lags is None:
            raise ValueError("a HAC sensitivity entry must set lags")
        if self.ci95_low > self.ci95_high:
            raise ValueError("ci95_low must not exceed ci95_high")
        return self


def _extreme_sensitivity_pairs(
    sensitivity: list[ExtremeSensitivity],
) -> set[tuple[str, int | None]]:
    return {(item.method, item.lags) for item in sensitivity}


_EXPECTED_SENSITIVITY = {("ols", None), ("hac", 1), ("hac", 3), ("hac", 5)}


class ExtremeTrend(BaseModel):
    """Trend of annual extreme-event rates and raw event counts.

    ``per_decade`` is the primary slope of ``365 * events / valid_days`` in
    event days per 365 days per decade.  ``count_per_decade`` is the equally
    specified OLS slope of raw annual counts.  Inference fields are populated
    only when the fit's guards pass; otherwise the descriptive slopes remain
    and the inference fields are null with no sensitivity entries.
    """

    years_used: int = Field(ge=MIN_TREND_YEARS)
    per_decade: float
    count_per_decade: float
    ci95_low: float | None = None
    ci95_high: float | None = None
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    significant: bool | None = None
    residual_lag1: float | None = Field(default=None, ge=-1.0, le=1.0)
    sensitivity: list[ExtremeSensitivity] = Field(default_factory=list)

    @field_validator("per_decade", "count_per_decade", "ci95_low", "ci95_high")
    @classmethod
    def _finite_or_null(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "extreme trend statistic")

    @model_validator(mode="after")
    def _coherent_inference(self) -> "ExtremeTrend":
        if (self.ci95_low is None) != (self.ci95_high is None):
            raise ValueError("ci95_low and ci95_high must both be present or null")
        if self.ci95_low is not None and self.ci95_low > self.ci95_high:
            raise ValueError("ci95_low must not exceed ci95_high")

        if self.p_value is None:
            if self.ci95_low is not None:
                raise ValueError("a confidence interval requires a p_value")
            if self.significant is not None:
                raise ValueError("a null p_value requires a null significance flag")
            if self.sensitivity:
                raise ValueError(
                    "sensitivity specifications require a non-null p_value"
                )
        else:
            if self.ci95_low is None:
                raise ValueError("a p_value requires a confidence interval")
            if self.significant is None:
                raise ValueError("a p_value requires a significance flag")
            if self.significant != (self.p_value < SIGNIFICANCE_ALPHA):
                raise ValueError("significant must match p_value at alpha 0.05")
            if _extreme_sensitivity_pairs(self.sensitivity) != _EXPECTED_SENSITIVITY:
                raise ValueError(
                    "sensitivity must report OLS, HAC(1), HAC(3) and HAC(5)"
                )
        return self


class ExtremeSeriesPoint(BaseModel):
    """One UTC calendar year of extreme-event diagnostics.

    A complete year (``valid_days == expected_days``) has a real integer count,
    including zero.  An incomplete year retains its day counts but has null
    events, fraction and fitted values; no day is filled or imputed.
    """

    year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    events: int | None = Field(default=None, ge=0)
    fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_days: int = Field(ge=0, le=366)
    expected_days: int = Field(ge=365, le=366)
    excluded_days: int = Field(ge=0, le=366)
    fitted: float | None

    @field_validator("fraction", "fitted")
    @classmethod
    def _finite_point(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "extreme series value")

    @model_validator(mode="after")
    def _coherent_counts(self) -> "ExtremeSeriesPoint":
        annual_days = 366 if calendar.isleap(self.year) else 365
        if self.expected_days != annual_days:
            raise ValueError("expected_days must equal the calendar days of the year")
        if self.valid_days > self.expected_days:
            raise ValueError("valid_days must not exceed expected_days")
        if self.excluded_days != self.expected_days - self.valid_days:
            raise ValueError("excluded_days must equal expected_days - valid_days")

        if self.valid_days == self.expected_days:
            if self.events is None or self.fraction is None:
                raise ValueError("a complete year must have an event count and fraction")
            if self.events > self.valid_days:
                raise ValueError("events must not exceed valid_days")
            expected_fraction = self.events / self.valid_days
            if not math.isclose(
                self.fraction, expected_fraction, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise ValueError("fraction must equal events / valid_days")
        else:
            if self.events is not None or self.fraction is not None:
                raise ValueError(
                    "an incomplete year must have null events and fraction"
                )
            if self.fitted is not None:
                raise ValueError("an incomplete year cannot have a fitted value")
        return self


class ExtremesResponse(BaseModel):
    """Response of ``GET /api/extremes`` (docs/EXTREMES-API.md)."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    event: ExtremeEvent
    threshold: float
    threshold_unit: ExtremeUnit
    operator: ExtremeOperator
    source: SourceInfo
    warnings: list[str]
    series: list[ExtremeSeriesPoint]
    trend: ExtremeTrend | None

    @field_validator("latitude", "longitude", "threshold")
    @classmethod
    def _finite_value(cls, value: float) -> float:
        return _require_finite(value, "value")

    @model_validator(mode="after")
    def _coherent_response(self) -> "ExtremesResponse":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")

        expected_unit = EXTREME_EVENT_UNITS[self.event]
        expected_operator = EXTREME_EVENT_OPERATORS[self.event]
        if self.threshold_unit != expected_unit:
            raise ValueError(f"threshold_unit must be {expected_unit!r} for {self.event}")
        if self.operator != expected_operator:
            raise ValueError(f"operator must be {expected_operator!r} for {self.event}")

        minimum, maximum = EXTREME_THRESHOLD_BOUNDS[self.event]
        if self.threshold < minimum or self.threshold > maximum:
            raise ValueError(
                f"threshold must be between {minimum:g} and {maximum:g} "
                f"for {self.event}"
            )

        expected_years = list(range(self.start_year, self.end_year + 1))
        if [point.year for point in self.series] != expected_years:
            raise ValueError("series must contain every requested year in ascending order")

        complete = [point for point in self.series if point.events is not None]
        if self.trend is None:
            if any(point.fitted is not None for point in self.series):
                raise ValueError("fitted values require a trend")
            if len(complete) >= MIN_TREND_YEARS:
                raise ValueError(
                    "a trend is required when at least "
                    f"{MIN_TREND_YEARS} complete years are available"
                )
        else:
            if self.trend.years_used != len(complete):
                raise ValueError(
                    "trend.years_used must equal the complete years in series"
                )
            if any(
                (point.events is None) != (point.fitted is None)
                for point in self.series
            ):
                raise ValueError("fitted values must be present exactly at complete years")
        return self


def _check_annual_series(
    series: list["SeriesPoint"],
    trend: "TrendStats | None",
    start_year: int,
    end_year: int,
) -> None:
    """Enforce the annual series/trend invariants shared by every DTO."""

    expected_years = list(range(start_year, end_year + 1))
    actual_years = [point.year for point in series]
    if actual_years != expected_years:
        raise ValueError("series must contain every requested year in ascending order")

    complete = [point for point in series if point.value is not None]
    if trend is None:
        if any(point.fitted is not None for point in series):
            raise ValueError("fitted values require a trend")
        if len(complete) >= MIN_TREND_YEARS:
            raise ValueError(
                "a trend is required when at least five years are complete"
            )
    else:
        if trend.years_used != len(complete):
            raise ValueError("trend.years_used must equal the complete years in series")
        if any((point.value is None) != (point.fitted is None) for point in series):
            raise ValueError("fitted values must be present exactly at included years")


class TrendResponse(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    variable: Variable
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    unit: str = Field(min_length=1)
    aggregation: Aggregation
    series: list[SeriesPoint]
    trend: TrendStats | None
    source: SourceInfo
    warnings: list[str]

    @field_validator("latitude", "longitude")
    @classmethod
    def _finite_coordinate(cls, value: float) -> float:
        return _require_finite(value, "coordinate")

    @model_validator(mode="after")
    def _coherent_response(self) -> "TrendResponse":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")

        expected_unit = "°C" if self.variable == "temperature" else "mm"
        expected_aggregation = "mean" if self.variable == "temperature" else "sum"
        if self.unit != expected_unit:
            raise ValueError(f"unit must be {expected_unit!r} for {self.variable}")
        if self.aggregation != expected_aggregation:
            raise ValueError(
                f"aggregation must be {expected_aggregation!r} for {self.variable}"
            )

        for point in self.series:
            annual_days = 366 if calendar.isleap(point.year) else 365
            if point.expected_days != annual_days:
                raise ValueError(
                    "expected_days must equal the calendar days of the year"
                )

        _check_annual_series(self.series, self.trend, self.start_year, self.end_year)
        return self


class ComparePoint(BaseModel):
    """One WGS84 coordinate in a comparison request."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)

    @field_validator("latitude", "longitude")
    @classmethod
    def _finite_coordinate(cls, value: float) -> float:
        return _require_finite(value, "coordinate")


class CompareRequest(BaseModel):
    """Body of ``POST /api/compare``.

    A single variable and one inclusive year range apply to both points: the
    contract compares like with like and never mixes variables or periods.
    """

    point_a: ComparePoint
    point_b: ComparePoint
    variable: Variable
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)

    @model_validator(mode="after")
    def _ordered_years(self) -> "CompareRequest":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")
        return self


class ComparisonSummary(BaseModel):
    """Descriptive paired-difference summary.

    The confidence interval and p-value are typed as ``None`` on purpose: the
    contract forbids an inferential claim from two short, spatially related
    series, so a non-null value cannot be represented at the API boundary.
    """

    method: Literal["paired_annual_difference_descriptive"] = COMPARISON_METHOD
    years_used: int = Field(ge=0)
    slope_difference_per_decade: float | None
    ci95_low: None = None
    ci95_high: None = None
    p_value: None = None
    inferential: Literal[False] = False
    note: str = Field(min_length=1)

    @field_validator("slope_difference_per_decade")
    @classmethod
    def _finite_slope(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "difference slope")

    @model_validator(mode="after")
    def _consistent_availability(self) -> "ComparisonSummary":
        has_slope = self.slope_difference_per_decade is not None
        if has_slope != (self.years_used >= MIN_TREND_YEARS):
            raise ValueError(
                "a difference slope is required exactly when at least "
                f"{MIN_TREND_YEARS} common complete years are available"
            )
        return self


class CompareResponse(BaseModel):
    """Response of ``POST /api/compare``: two trends plus a descriptive summary."""

    point_a: TrendResponse
    point_b: TrendResponse
    comparison: ComparisonSummary

    @model_validator(mode="after")
    def _coherent_comparison(self) -> "CompareResponse":
        for field in ("variable", "start_year", "end_year", "unit", "aggregation"):
            if getattr(self.point_a, field) != getattr(self.point_b, field):
                raise ValueError(f"point_a and point_b must share {field}")

        years_a = {point.year for point in self.point_a.series if point.value is not None}
        years_b = {point.year for point in self.point_b.series if point.value is not None}
        if self.comparison.years_used != len(years_a & years_b):
            raise ValueError(
                "comparison.years_used must equal the common complete years"
            )
        return self


class AnomalyPoint(BaseModel):
    """One annual aggregate with its anomaly against the baseline mean."""

    year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    value: float | None
    anomaly: float | None
    valid_days: int = Field(ge=0, le=366)
    expected_days: int = Field(ge=365, le=366)

    @field_validator("value", "anomaly")
    @classmethod
    def _finite_point(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "anomaly series value")

    @model_validator(mode="after")
    def _coherent_counts(self) -> "AnomalyPoint":
        if self.valid_days > self.expected_days:
            raise ValueError("valid_days must not exceed expected_days")
        return self


class AnomalyResponse(BaseModel):
    """Response of ``GET /api/anomalies``.

    ``baseline_mean`` is the equally weighted mean of the baseline annual
    aggregates.  The strict baseline rule is enforced here: every day of every
    baseline year must be valid, so a null mean implies at least one incomplete
    baseline year and every anomaly in the series is null.
    """

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    variable: Variable
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    baseline_start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    baseline_end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    unit: str = Field(min_length=1)
    aggregation: Aggregation
    baseline_mean: float | None
    baseline_complete_years: int = Field(ge=0)
    series: list[AnomalyPoint]
    source: SourceInfo
    warnings: list[str]

    @field_validator("latitude", "longitude")
    @classmethod
    def _finite_coordinate(cls, value: float) -> float:
        return _require_finite(value, "coordinate")

    @field_validator("baseline_mean")
    @classmethod
    def _finite_baseline(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_finite(value, "baseline mean")

    @model_validator(mode="after")
    def _coherent_response(self) -> "AnomalyResponse":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")
        if self.baseline_start_year > self.baseline_end_year:
            raise ValueError("baseline_start_year must not exceed baseline_end_year")

        expected_unit = "°C" if self.variable == "temperature" else "mm"
        expected_aggregation = "mean" if self.variable == "temperature" else "sum"
        if self.unit != expected_unit:
            raise ValueError(f"unit must be {expected_unit!r} for {self.variable}")
        if self.aggregation != expected_aggregation:
            raise ValueError(
                f"aggregation must be {expected_aggregation!r} for {self.variable}"
            )

        expected_years = list(range(self.start_year, self.end_year + 1))
        if [point.year for point in self.series] != expected_years:
            raise ValueError("series must contain every requested year in ascending order")

        baseline_span = self.baseline_end_year - self.baseline_start_year + 1
        if self.baseline_complete_years > baseline_span:
            raise ValueError(
                "baseline_complete_years must not exceed the baseline span"
            )
        if self.baseline_mean is None:
            if self.baseline_complete_years >= baseline_span:
                raise ValueError(
                    "a null baseline_mean requires at least one incomplete "
                    "baseline year"
                )
        elif self.baseline_complete_years != baseline_span:
            raise ValueError(
                "baseline_mean requires every baseline year to be complete"
            )

        for point in self.series:
            annual_days = 366 if calendar.isleap(point.year) else 365
            if point.expected_days != annual_days:
                raise ValueError(
                    "expected_days must equal the calendar days of the year"
                )
            has_anomaly = point.value is not None and self.baseline_mean is not None
            if (point.anomaly is not None) != has_anomaly:
                raise ValueError(
                    "anomaly must be present exactly when the annual value and "
                    "the baseline mean are available"
                )
        return self


class SeasonalPeriodEntry(BaseModel):
    """One seasonal (or monthly) period inside a ``GET /api/seasonal`` response."""

    period: SeasonCode
    series: list[SeriesPoint]
    trend: TrendStats | None


class SeasonalResponse(BaseModel):
    """Response of ``GET /api/seasonal``.

    ``period == "all"`` expands to winter, spring, summer and autumn in that
    order, computed from a single daily download.  Every entry reuses the
    annual series and OLS trend DTOs.
    """

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    variable: Variable
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    period: SeasonalPeriod
    unit: str = Field(min_length=1)
    aggregation: Aggregation
    periods: list[SeasonalPeriodEntry]
    source: SourceInfo
    warnings: list[str]

    @field_validator("latitude", "longitude")
    @classmethod
    def _finite_coordinate(cls, value: float) -> float:
        return _require_finite(value, "coordinate")

    @model_validator(mode="after")
    def _coherent_response(self) -> "SeasonalResponse":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")

        expected_unit = "°C" if self.variable == "temperature" else "mm"
        expected_aggregation = "mean" if self.variable == "temperature" else "sum"
        if self.unit != expected_unit:
            raise ValueError(f"unit must be {expected_unit!r} for {self.variable}")
        if self.aggregation != expected_aggregation:
            raise ValueError(
                f"aggregation must be {expected_aggregation!r} for {self.variable}"
            )

        expected_periods = list(SEASONS) if self.period == ALL_PERIOD else [self.period]
        if [entry.period for entry in self.periods] != expected_periods:
            raise ValueError(
                "periods must match the requested period; 'all' expands to "
                f"{list(SEASONS)} in order"
            )

        for entry in self.periods:
            _check_annual_series(
                entry.series, entry.trend, self.start_year, self.end_year
            )
        return self


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
