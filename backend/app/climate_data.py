"""Internal climate-data helpers for the EarthScope anomaly and seasonal routes.

Boundary contract (docs/CLIMATE-API.md)
---------------------------------------
Two pure functions back ``GET /api/anomalies`` and ``GET /api/seasonal``.
They never contact NASA POWER: the caller supplies ``daily`` from the
exact-query cache of :func:`app.power.load_daily` (``YYYYMMDD`` UTC keys
mapped to finite ``float`` values, invalid/missing/fill days already
omitted).  The input is nevertheless re-validated defensively with the same
cleaning semantics as the annual aggregation, so malformed keys or values
become invalid days instead of silently inflating a mean or sum.

Method notes
------------
* All dates are UTC calendar days (``datetime64[ns, UTC]`` keys).
* Temperature aggregates the daily mean of T2M values (unit ``°C``).
  Precipitation sums daily PRECTOTCORR rates integrated over one-day
  intervals (``mm/day`` × 1 day = ``mm`` per window).
* Strict completeness: any missing, invalid, non-finite or (for
  precipitation) negative expected UTC day makes the whole year/period
  value ``None``; nothing is imputed, rescaled or partially aggregated.
* Winter is meteorological DJF: previous December plus current January and
  February.  The prior-year fetch is the API layer's responsibility (one
  contiguous span).  Winter for ``COVERAGE_MIN_YEAR`` (1981) is always
  ``None`` — its December belongs to 1980, outside the coverage contract —
  even when January and February are complete.  Days belonging to years
  before the coverage minimum are neither counted as valid nor used.
* ``aggregate_period`` accepts one period code only (``winter``,
  ``spring``, ``summer``, ``autumn``, ``m01``…``m12``).  The ``all``
  response is assembled by the API layer from one daily fetch.
* Anomalies subtract the equally weighted mean of the baseline annual
  aggregates from each display-year value.  Strict baseline rule: every day
  of every baseline year must be valid; otherwise ``baseline_mean`` and all
  anomalies are ``None`` while the count of complete baseline years is
  still reported.  Missing display years stay ``None``.  1991–2020 is a
  30-year preset (a WMO climate normal period); custom shorter baselines
  are references only, not climate normals.

Limitations: POWER is a reanalysis/gridded product, so a grid-cell value is
not a station measurement; anomalies are descriptive differences against a
reference, and no multiple-comparison correction is applied across
variables, periods or locations.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from .analysis import (
    AnnualAggregate,
    DataContractError,
    _daily_frame,
    aggregate_annual,
)
from .schemas import COVERAGE_MAX_YEAR, COVERAGE_MIN_YEAR, Variable

__all__ = ["DataContractError", "aggregate_period", "anomaly_series"]

_VARIABLES: tuple[str, ...] = ("temperature", "precipitation")

#: Month cells for every period code except ``winter``, which is DJF and
#: therefore needs the previous December (see ``_window_cells``).
_PERIOD_MONTHS: dict[str, tuple[int, ...]] = {
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
    "autumn": (9, 10, 11),
    **{f"m{month:02d}": (month,) for month in range(1, 13)},
}

_SINGLE_PERIOD_CODES = frozenset(("winter", *_PERIOD_MONTHS))


def _validate_year(name: str, year: Any) -> int:
    if isinstance(year, bool) or not isinstance(year, int):
        raise DataContractError(f"{name} must be an integer")
    if not COVERAGE_MIN_YEAR <= year <= COVERAGE_MAX_YEAR:
        raise DataContractError(
            f"{name} must be within "
            f"[{COVERAGE_MIN_YEAR}, {COVERAGE_MAX_YEAR}]"
        )
    return year


def _validate_common(
    daily: Any,
    variable: Any,
    start_year: Any,
    end_year: Any,
) -> None:
    if not isinstance(daily, Mapping):
        raise DataContractError("daily observations must be a mapping")
    if variable not in _VARIABLES:
        raise DataContractError(f"unsupported variable: {variable!r}")
    _validate_year("start_year", start_year)
    _validate_year("end_year", end_year)
    if start_year > end_year:
        raise DataContractError("start_year must not exceed end_year")


def _window_cells(period: str, year: int) -> tuple[tuple[int, int], ...]:
    """UTC (year, month) cells of the period window for ``year``."""

    if period == "winter":
        return ((year - 1, 12), (year, 1), (year, 2))
    return tuple((year, month) for month in _PERIOD_MONTHS[period])


def _window_totals(
    counts: "pd.Series[Any]",
    sums: "pd.Series[Any]",
    cells: tuple[tuple[int, int], ...],
) -> tuple[int, float]:
    """Count valid days and sum values over the given (year, month) cells."""

    if counts.empty:
        return 0, 0.0
    valid_days = 0
    total = 0.0
    for cell in cells:
        try:
            valid_days += int(counts.loc[cell])
        except KeyError:
            pass
        try:
            total += float(sums.loc[cell])
        except KeyError:
            pass
    return valid_days, total


def aggregate_period(
    daily: Mapping[str, Any],
    variable: Variable,
    start_year: int,
    end_year: int,
    period: str,
) -> list[AnnualAggregate]:
    """Aggregate daily values into one period for every requested year.

    ``period`` is a single code (``winter``, ``spring``, ``summer``,
    ``autumn`` or ``m01``…``m12``); ``all`` is deliberately rejected here
    because the API layer assembles that response from one daily fetch.
    Returns the existing :class:`~app.analysis.AnnualAggregate` dataclass,
    with ``expected_days`` set to the full calendar window size (90/91 for
    DJF, 92 for the three-month seasons, 29/30/31 for single months) so a
    caller can see exactly how many expected UTC days were missing.
    """

    _validate_common(daily, variable, start_year, end_year)
    if period not in _SINGLE_PERIOD_CODES:
        raise DataContractError(
            "period must be a single code (winter, spring, summer, autumn, "
            f"m01-m12); got {period!r}. The 'all' response is assembled by "
            "the API layer."
        )

    frame = _daily_frame(daily)
    if variable == "precipitation" and not frame.empty:
        frame = frame.loc[frame["value"] >= 0.0]

    if frame.empty:
        counts = pd.Series(dtype="int64")
        sums = pd.Series(dtype="float64")
    else:
        frame = frame.assign(
            year=frame["date"].dt.year.astype("int64"),
            month=frame["date"].dt.month.astype("int64"),
        )
        grouped = frame.groupby(["year", "month"], sort=True)["value"]
        counts = grouped.size()
        sums = grouped.sum()

    aggregates: list[AnnualAggregate] = []
    for year in range(start_year, end_year + 1):
        cells = _window_cells(period, year)
        # Winter at the coverage minimum cannot include its previous
        # December: 1980 is outside the coverage contract, so those days
        # are neither counted nor used and the value stays null.
        prior_year_outside = period == "winter" and year - 1 < COVERAGE_MIN_YEAR
        counted = cells[1:] if prior_year_outside else cells
        expected_days = sum(
            calendar.monthrange(cell_year, cell_month)[1]
            for cell_year, cell_month in cells
        )
        valid_days, window_sum = _window_totals(counts, sums, counted)

        value: float | None = None
        if not prior_year_outside and valid_days == expected_days:
            candidate = (
                window_sum / valid_days if variable == "temperature" else window_sum
            )
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


def anomaly_series(
    daily: Mapping[str, Any],
    variable: Variable,
    start_year: int,
    end_year: int,
    baseline_start_year: int,
    baseline_end_year: int,
) -> tuple[list[dict[str, Any]], float | None, int]:
    """Compute display rows, baseline mean and complete baseline years.

    The union span ``min(start, baseline_start) .. max(end, baseline_end)``
    is aggregated once (the API layer fetches exactly that contiguous
    range), so baseline and display years can be disjoint without a second
    load.  Rows cover only the display years, in ascending order, with the
    keys ``year``, ``value``, ``anomaly``, ``valid_days`` and
    ``expected_days``.

    Baseline rule: the equally weighted mean is computed only when every
    baseline year is complete (every UTC day valid); otherwise
    ``baseline_mean`` is ``None`` and every anomaly is ``None``, while the
    number of complete baseline years is still returned.  A display year
    with an incomplete day set keeps ``value`` and ``anomaly`` ``None``.

    Returns ``(rows, baseline_mean, baseline_complete_years)``.
    """

    _validate_common(daily, variable, start_year, end_year)
    _validate_year("baseline_start_year", baseline_start_year)
    _validate_year("baseline_end_year", baseline_end_year)
    if baseline_start_year > baseline_end_year:
        raise DataContractError(
            "baseline_start_year must not exceed baseline_end_year"
        )

    union_start = min(start_year, baseline_start_year)
    union_end = max(end_year, baseline_end_year)
    by_year = {
        item.year: item
        for item in aggregate_annual(daily, variable, union_start, union_end)
    }

    baseline_items = [
        by_year[year]
        for year in range(baseline_start_year, baseline_end_year + 1)
    ]
    baseline_complete_years = sum(
        1 for item in baseline_items if item.value is not None
    )

    baseline_mean: float | None = None
    if baseline_complete_years == len(baseline_items):
        candidate = math.fsum(
            float(item.value) for item in baseline_items
        ) / len(baseline_items)
        if math.isfinite(candidate):
            baseline_mean = candidate

    rows: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        item = by_year[year]
        anomaly: float | None = None
        if item.value is not None and baseline_mean is not None:
            candidate = float(item.value) - baseline_mean
            if math.isfinite(candidate):
                anomaly = candidate
        rows.append(
            {
                "year": year,
                "value": item.value,
                "anomaly": anomaly,
                "valid_days": item.valid_days,
                "expected_days": item.expected_days,
            }
        )
    return rows, baseline_mean, baseline_complete_years
