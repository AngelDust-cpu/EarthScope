"""Annual aggregation and trend statistics for EarthScope.

Method notes
------------
Daily NASA POWER values are reduced to one value per UTC calendar year,
using the mean for temperature (T2M) and the sum for precipitation
(PRECTOTCORR).  A year is usable only when every calendar day has a finite,
non-negative (for precipitation) observation.  Missing, null, non-finite,
fill-value (already removed by :mod:`app.power`) and negative-precipitation
days therefore invalidate that year instead of being imputed or silently
rescaled.

The trend is an ordinary least-squares fit of annual value on calendar year,
parameterised with centred years.  The reported slope is multiplied by ten
for a per-decade change.  The confidence interval and two-sided p-value use
Student's t with ``n - 2`` degrees of freedom and the residual-based standard
error of the slope (alpha 0.05).

Statistical assumptions and limitations: residuals are assumed independent,
homoscedastic and approximately normal.  Autocorrelation is not modelled, so
nominal intervals and p-values may be optimistic for serially correlated
climate series.  The analysis is exploratory: multiple variables, intervals
and locations are not corrected for multiple comparisons.  POWER is a
reanalysis/gridded product; a grid-cell value is not a direct station
measurement, and changes are associations rather than causal attribution.

Two locations are compared with the slope of the paired annual difference
series (A minus B) restricted to years where both points are complete.  That
difference is descriptive only: no standard error, interval, p-value or
significance flag is computed for it, because two short, spatially related
series from possibly overlapping grid cells do not satisfy ordinary
independent-error inference.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
from pydantic import ValidationError
from scipy import stats as scipy_stats

from .schemas import (
    MIN_TREND_YEARS,
    SIGNIFICANCE_ALPHA,
    CompareResponse,
    ComparisonSummary,
    SeriesPoint,
    SourceInfo,
    TrendResponse,
    TrendStats,
    Variable,
)

_PARAMETER_BY_VARIABLE: dict[str, str] = {
    "temperature": "T2M",
    "precipitation": "PRECTOTCORR",
}
_UNIT_BY_VARIABLE: dict[str, str] = {
    "temperature": "°C",
    "precipitation": "mm",
}
_DAILY_UNIT_BY_VARIABLE: dict[str, str] = {
    "temperature": "C",
    "precipitation": "mm/day",
}
_AGGREGATION_BY_VARIABLE: dict[str, str] = {
    "temperature": "mean",
    "precipitation": "sum",
}


class DataContractError(RuntimeError):
    """Raised when POWER data or metadata does not match the internal contract."""


@dataclass(frozen=True, slots=True)
class AnnualAggregate:
    year: int
    value: float | None
    valid_days: int
    expected_days: int


def _empty_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series([], dtype="datetime64[ns, UTC]"),
            "value": pd.Series([], dtype="float64"),
        }
    )


def _daily_frame(daily: Mapping[str, Any]) -> pd.DataFrame:
    """Build a clean date/value frame from YYYYMMDD-keyed daily observations."""

    if not daily:
        return _empty_daily_frame()

    stamps = pd.Series(list(daily.keys()), dtype="object").astype("string").str.strip()
    raw_values = pd.Series(list(daily.values()), dtype="object")
    numeric_values = pd.to_numeric(raw_values, errors="coerce")

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                stamps, format="%Y%m%d", errors="coerce", utc=True
            ),
            "value": numeric_values.astype("float64"),
        }
    )
    frame = frame.loc[frame["date"].notna() & frame["value"].notna()].copy()
    if frame.empty:
        return _empty_daily_frame()

    finite = np.isfinite(frame["value"].to_numpy(dtype="float64"))
    frame = frame.loc[finite]
    # The upstream contract uses a dictionary, but duplicate/ambiguous dates
    # must never make a year look complete when a calendar day is absent.
    return frame.drop_duplicates(subset="date", keep="last")


def aggregate_annual(
    daily: Mapping[str, Any],
    variable: Variable,
    start_year: int,
    end_year: int,
) -> list[AnnualAggregate]:
    """Aggregate daily values into every requested UTC calendar year.

    Years with an incomplete or invalid day set receive ``value=None`` while
    still remaining in the returned series, in ascending order.
    """

    if variable not in _PARAMETER_BY_VARIABLE:
        raise DataContractError(f"unsupported variable: {variable!r}")
    if start_year > end_year:
        raise DataContractError("start_year must not exceed end_year")
    if not isinstance(daily, Mapping):
        raise DataContractError("daily observations must be a mapping")

    frame = _daily_frame(daily)
    if not frame.empty:
        frame["year"] = frame["date"].dt.year.astype("int64")
        frame = frame.loc[
            (frame["year"] >= start_year) & (frame["year"] <= end_year)
        ]
        if variable == "precipitation":
            frame = frame.loc[frame["value"] >= 0.0]

    if frame.empty:
        counts = pd.Series(dtype="int64")
        means = pd.Series(dtype="float64")
        sums = pd.Series(dtype="float64")
    else:
        grouped = frame.groupby("year", sort=True)["value"]
        counts = grouped.size()
        means = grouped.mean()
        sums = grouped.sum()

    aggregates: list[AnnualAggregate] = []
    for year in range(start_year, end_year + 1):
        expected_days = 366 if calendar.isleap(year) else 365
        valid_days = int(counts.get(year, 0))
        value: float | None = None
        if valid_days == expected_days:
            aggregated = means.get(year) if variable == "temperature" else sums.get(year)
            if aggregated is not None:
                candidate = float(aggregated)
                if math.isfinite(candidate):
                    value = candidate
        aggregates.append(
            AnnualAggregate(
                year=year,
                value=value,
                valid_days=valid_days,
                expected_days=expected_days,
            )
        )
    return aggregates


def _format_year_list(years: list[int], limit: int = 6) -> str:
    shown = ", ".join(str(year) for year in years[:limit])
    if len(years) > limit:
        shown += f" and {len(years) - limit} more"
    return shown


def fit_trend(
    aggregates: list[AnnualAggregate],
) -> tuple[TrendStats | None, dict[int, float], list[str]]:
    """Fit an OLS trend to complete annual values.

    Returns the statistics (or ``None``), fitted values keyed by included
    year, and warnings.  Fitted values exist only for years used in the fit.
    """

    complete = [item for item in aggregates if item.value is not None]
    years_used = len(complete)
    if years_used < MIN_TREND_YEARS:
        warning = (
            f"A trend requires at least {MIN_TREND_YEARS} complete years; "
            f"only {years_used} available."
        )
        return None, {}, [warning]

    years = np.asarray([item.year for item in complete], dtype="float64")
    values = np.asarray([item.value for item in complete], dtype="float64")
    if not np.all(np.isfinite(years)) or not np.all(np.isfinite(values)):
        raise DataContractError("annual values must be finite before fitting")

    centred_years = years - float(years.mean())
    sxx = float(np.dot(centred_years, centred_years))
    if sxx <= 0.0:
        raise DataContractError("trend years must contain at least two distinct values")

    if np.all(values == values[0]) or (
        float(values.max() - values.min())
        <= max(float(np.max(np.abs(values))), 1.0) * 1e-12
    ):
        constant = float(values.mean())
        fitted = {item.year: constant for item in complete}
        stats = TrendStats(
            years_used=years_used,
            per_decade=0.0,
            ci95_low=0.0,
            ci95_high=0.0,
            p_value=1.0,
            significant=False,
            r_squared=0.0,
        )
        return stats, fitted, []

    mean_value = float(values.mean())
    slope = float(np.dot(centred_years, values - mean_value) / sxx)
    intercept = mean_value
    fitted_values = intercept + slope * centred_years
    residuals = values - fitted_values
    ss_res = float(np.dot(residuals, residuals))
    ss_total = float(np.dot(values - mean_value, values - mean_value))

    # A perfect linear fit produces residual round-off around 1e-15 relative to
    # the signal.  Treat only that numerical zero as the zero-error limit: a
    # point interval and p=0, instead of dividing by a zero standard error.
    zero_error = ss_res <= ss_total * (10 * np.finfo(float).eps) ** 2

    if zero_error:
        ci_low = ci_high = slope
        p_value = 0.0
        r_squared = 1.0
    else:
        degrees_of_freedom = years_used - 2
        residual_variance = ss_res / degrees_of_freedom
        standard_error = math.sqrt(max(residual_variance, 0.0) / sxx)
        t_critical = float(scipy_stats.t.ppf(0.975, degrees_of_freedom))
        ci_low = slope - t_critical * standard_error
        ci_high = slope + t_critical * standard_error
        t_statistic = slope / standard_error if standard_error > 0.0 else 0.0
        p_value = float(2.0 * scipy_stats.t.sf(abs(t_statistic), degrees_of_freedom))
        p_value = min(max(p_value, 0.0), 1.0)
        r_squared = 1.0 - ss_res / ss_total if ss_total > 0.0 else 0.0
        r_squared = min(max(r_squared, 0.0), 1.0)

    fitted_by_year = {
        item.year: float(value)
        for item, value in zip(complete, fitted_values, strict=True)
    }
    stats = TrendStats(
        years_used=years_used,
        per_decade=float(slope * 10.0),
        ci95_low=float(ci_low * 10.0),
        ci95_high=float(ci_high * 10.0),
        p_value=p_value,
        significant=p_value < SIGNIFICANCE_ALPHA,
        r_squared=r_squared,
    )
    return stats, fitted_by_year, []


def hac_slope_standard_error(
    years: Sequence[float],
    values: Sequence[float],
    fitted: Sequence[float],
    lags: int,
) -> float | None:
    """Bartlett (Newey-West) standard error of an OLS slope, per year.

    The regression is the one already fitted by :func:`fit_trend`: a simple
    OLS of ``values`` on ``years``.  ``fitted`` must carry the aligned fitted
    values so the residuals are the fit's residuals, not a refit.  The slope's
    variance uses the Bartlett kernel with ``lags`` autocovariances and the
    finite-sample factor ``n / (n - 2)``::

        Var(b) = meat / Sxx**2 * n / (n - 2)
        meat   = sum(s_i**2) + 2 * sum_{l=1..L} (1 - l/(L+1)) * sum(s_i s_{i-l})

    where ``s_i = (x_i - mean(x)) * e_i``.  Observations must be in ascending
    time order; callers that need a gap-free series (for example the regional
    map) must check that themselves.

    Returns ``None`` when the variance is not finite and positive, for
    example a perfect linear fit with zero residuals, so the caller can omit
    inference rather than divide by a zero standard error.
    """

    if isinstance(lags, bool) or not isinstance(lags, int) or lags < 1:
        raise DataContractError("HAC lags must be a positive integer")
    if len(years) != len(values) or len(years) != len(fitted):
        raise DataContractError("HAC inputs must have equal length")

    year_values = np.asarray(years, dtype="float64")
    observed = np.asarray(values, dtype="float64")
    fitted_values = np.asarray(fitted, dtype="float64")
    if not (
        np.all(np.isfinite(year_values))
        and np.all(np.isfinite(observed))
        and np.all(np.isfinite(fitted_values))
    ):
        raise DataContractError("HAC inputs must be finite")

    count = int(year_values.size)
    if count <= 2:
        return None
    centred_years = year_values - float(year_values.mean())
    sxx = float(np.dot(centred_years, centred_years))
    if sxx <= 0.0:
        return None

    scores = centred_years * (observed - fitted_values)
    base = float(np.dot(scores, scores))
    meat = base + 2.0 * math.fsum(
        (1.0 - lag / (lags + 1.0)) * float(np.dot(scores[lag:], scores[:-lag]))
        for lag in range(1, lags + 1)
    )
    variance = meat / sxx**2 * count / (count - 2)
    if not math.isfinite(variance) or variance <= 0.0:
        return None
    return math.sqrt(variance)


_DESCRIPTIVE_NOTE = (
    "Descriptive slope of the paired annual difference series (A minus B) on "
    "years where both points have a complete annual value. No confidence "
    "interval, p-value or significance claim is reported: two short, "
    "spatially related climate series, often from overlapping grid cells, do "
    "not justify ordinary independent-error inference. Treat the estimate as "
    "an exploratory association, not a causal finding."
)
_INSUFFICIENT_NOTE = (
    " Only {years_used} common complete year(s) are available; at least "
    f"{MIN_TREND_YEARS} are required, so no difference slope is estimated."
)


def compare_annual_differences(
    series_a: Sequence[SeriesPoint],
    series_b: Sequence[SeriesPoint],
) -> ComparisonSummary:
    """Describe the slope of the paired annual difference series (A minus B).

    Years enter the difference fit only when both points produced a complete
    annual value, so differing missing years are excluded pairwise.  Each
    point's own trend is unaffected and keeps its own complete years.  The
    result carries no standard error, interval, p-value or significance flag.
    """

    values_a = {point.year: point.value for point in series_a if point.value is not None}
    values_b = {point.year: point.value for point in series_b if point.value is not None}
    common_years = sorted(set(values_a).intersection(values_b))
    years_used = len(common_years)

    if years_used < MIN_TREND_YEARS:
        return ComparisonSummary(
            years_used=years_used,
            slope_difference_per_decade=None,
            note=_DESCRIPTIVE_NOTE + _INSUFFICIENT_NOTE.format(years_used=years_used),
        )

    years = np.asarray(common_years, dtype="float64")
    differences = np.asarray(
        [float(values_a[year]) - float(values_b[year]) for year in common_years],
        dtype="float64",
    )
    if not np.all(np.isfinite(years)) or not np.all(np.isfinite(differences)):
        raise DataContractError("annual difference values must be finite before fitting")

    centred_years = years - float(years.mean())
    sxx = float(np.dot(centred_years, centred_years))
    if sxx <= 0.0:
        raise DataContractError(
            "difference years must contain at least two distinct values"
        )

    slope = float(
        np.dot(centred_years, differences - float(differences.mean())) / sxx
    )
    return ComparisonSummary(
        years_used=years_used,
        slope_difference_per_decade=float(slope * 10.0),
        note=_DESCRIPTIVE_NOTE,
    )


def _normalise_source(source: Any, variable: Variable) -> SourceInfo:
    if isinstance(source, SourceInfo):
        info = source
    elif isinstance(source, Mapping):
        try:
            info = SourceInfo.model_validate(dict(source))
        except (ValidationError, TypeError, ValueError) as exc:
            raise DataContractError(
                "NASA POWER source metadata is incomplete or invalid"
            ) from exc
    else:
        raise DataContractError("NASA POWER source metadata is missing")

    if not info.name.upper().startswith("NASA POWER"):
        raise DataContractError("source.name must identify NASA POWER")
    if info.parameter != _PARAMETER_BY_VARIABLE[variable]:
        raise DataContractError(
            f"source.parameter must be {_PARAMETER_BY_VARIABLE[variable]!r} "
            f"for {variable}"
        )
    if info.daily_unit != _DAILY_UNIT_BY_VARIABLE[variable]:
        raise DataContractError(
            f"source.daily_unit must be {_DAILY_UNIT_BY_VARIABLE[variable]!r} "
            f"for {variable}"
        )

    parsed_url = urlsplit(info.request_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise DataContractError("source.request_url must be an HTTPS URL")

    stamp = info.retrieved_at.strip()
    iso_candidate = f"{stamp[:-1]}+00:00" if stamp.endswith(("Z", "z")) else stamp
    try:
        retrieved = datetime.fromisoformat(iso_candidate)
    except ValueError as exc:
        raise DataContractError("source.retrieved_at must be ISO 8601") from exc
    if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0):
        raise DataContractError("source.retrieved_at must be in UTC")
    normalised_stamp = (
        retrieved.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return info.model_copy(update={"retrieved_at": normalised_stamp})


def build_trend_response(
    *,
    daily: Mapping[str, Any],
    source: Any,
    latitude: float,
    longitude: float,
    variable: Variable,
    start_year: int,
    end_year: int,
) -> TrendResponse:
    """Build the complete trend DTO from a validated POWER response."""

    source_info = _normalise_source(source, variable)
    aggregates = aggregate_annual(daily, variable, start_year, end_year)
    trend, fitted_by_year, trend_warnings = fit_trend(aggregates)

    warnings: list[str] = []
    missing_value_years = [item.year for item in aggregates if item.value is None]
    if missing_value_years:
        warnings.append(
            "Incomplete or invalid daily coverage prevented an annual value for "
            f"{len(missing_value_years)} requested year(s): "
            f"{_format_year_list(missing_value_years)}."
        )
    warnings.extend(trend_warnings)

    series = [
        SeriesPoint(
            year=item.year,
            value=item.value,
            valid_days=item.valid_days,
            expected_days=item.expected_days,
            fitted=fitted_by_year.get(item.year),
        )
        for item in aggregates
    ]

    return TrendResponse(
        latitude=latitude,
        longitude=longitude,
        variable=variable,
        start_year=start_year,
        end_year=end_year,
        unit=_UNIT_BY_VARIABLE[variable],
        aggregation=_AGGREGATION_BY_VARIABLE[variable],
        series=series,
        trend=trend,
        source=source_info,
        warnings=warnings,
    )


def build_compare_response(
    *,
    daily_a: Mapping[str, Any],
    source_a: Any,
    latitude_a: float,
    longitude_a: float,
    daily_b: Mapping[str, Any],
    source_b: Any,
    latitude_b: float,
    longitude_b: float,
    variable: Variable,
    start_year: int,
    end_year: int,
) -> CompareResponse:
    """Build the comparison DTO from two independently loaded POWER responses.

    Each point goes through the same complete-year aggregation and OLS routine
    as ``GET /api/trend``; the comparison then intersects the two series
    pairwise.  A data-contract failure in either point propagates, so no
    partial result is ever returned.
    """

    point_a = build_trend_response(
        daily=daily_a,
        source=source_a,
        latitude=latitude_a,
        longitude=longitude_a,
        variable=variable,
        start_year=start_year,
        end_year=end_year,
    )
    point_b = build_trend_response(
        daily=daily_b,
        source=source_b,
        latitude=latitude_b,
        longitude=longitude_b,
        variable=variable,
        start_year=start_year,
        end_year=end_year,
    )
    comparison = compare_annual_differences(point_a.series, point_b.series)
    return CompareResponse(point_a=point_a, point_b=point_b, comparison=comparison)
