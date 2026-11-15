"""Deterministic QA tests for the EarthScope 2.3 climate contract.

Scope: ``backend/app/climate_data.py`` (Data-owned arithmetic) and the
untested orchestration surface of ``backend/app/climate.py`` (fetch spans,
the winter-1981 guard and the ``build_*_response`` contract mappings).

Every fixture is synthetic calendar arithmetic; no test contacts NASA
POWER, imports the networking layer, or raises its timeout.  Units follow
the contract: temperature daily values are Celsius and are averaged;
precipitation daily values are mm/day rates integrated over one-day
intervals (mm).

Deliberately non-overlapping with ``backend/tests/test_climate_data.py``;
this file pins behaviours that suite does not cover: equally weighted
baseline annual means across leap years, fetch-span arithmetic, the
winter-1981 null through the API layer (including a defensive stub that
simulates an over-optimistic Data layer) and leap-day completeness for
DJF/February.
"""

from __future__ import annotations

import types
from datetime import date, timedelta
from typing import Any

import pytest

from app import climate
from app.analysis import AnnualAggregate
from app.climate_data import aggregate_period, anomaly_series
from app.schemas import COVERAGE_MAX_YEAR, COVERAGE_MIN_YEAR, SourceInfo

def _daily_for_years(
    value_by_year: dict[int, float],
    *,
    missing: tuple[str, ...] = (),
    overrides: dict[str, float] | None = None,
) -> dict[str, float]:
    """Deterministic daily mapping with one constant value per UTC year."""

    daily: dict[str, float] = {}
    for year, value in value_by_year.items():
        current = date(year, 1, 1)
        while current.year == year:
            daily[current.strftime("%Y%m%d")] = value
            current += timedelta(days=1)
    for key in missing:
        if key not in daily:
            raise AssertionError(f"fixture missing-key not generated: {key}")
        del daily[key]
    if overrides:
        daily.update(overrides)
    return daily

def _source(variable: str) -> SourceInfo:
    if variable == "temperature":
        parameter, daily_unit = "T2M", "C"
    else:
        parameter, daily_unit = "PRECTOTCORR", "mm/day"
    return SourceInfo(
        name="NASA POWER",
        parameter=parameter,
        daily_unit=daily_unit,
        dataset="MERRA2",
        time_standard="UTC",
        request_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        retrieved_at="2026-11-14T00:00:00Z",
        cached=False,
    )

def _build_seasonal(
    daily: dict[str, float],
    variable: str,
    start_year: int,
    end_year: int,
    period: str,
):
    return climate.build_seasonal_response(
        daily=daily,
        source=_source(variable),
        latitude=42.87,
        longitude=74.60,
        variable=variable,
        start_year=start_year,
        end_year=end_year,
        period=period,
    )


# ---------------------------------------------------------------------------
# Baseline means weight every annual aggregate equally, leap year included
# ---------------------------------------------------------------------------

def test_baseline_mean_weights_leap_and_ordinary_years_equally() -> None:
    # 1992 is the leap year; the annual aggregates are the yearly means.
    daily = _daily_for_years({1991: 10.0, 1992: 20.0})

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 1992, 1992, 1991, 1992
    )

    assert complete == 2
    # Equally weighted: (10 + 20) / 2. A day-weighted implementation would
    # give (365 * 10 + 366 * 20) / 731 instead.
    day_weighted = (365 * 10.0 + 366 * 20.0) / 731
    assert day_weighted != pytest.approx(15.0, abs=1e-9)
    assert baseline_mean == pytest.approx(15.0, abs=1e-12)
    assert rows[0]["value"] == pytest.approx(20.0)
    assert rows[0]["anomaly"] == pytest.approx(5.0)

def test_baseline_mean_for_precipitation_averages_annual_sums_equally() -> None:
    # Daily rates differ, so annual sums are 365 (1991) and 1098 (1992,
    # leap). Equal weighting averages the sums; a mean-of-rates answer
    # (about 2.0 mm/day) would violate the contract.
    daily = _daily_for_years({1991: 1.0, 1992: 3.0})

    rows, baseline_mean, complete = anomaly_series(
        daily, "precipitation", 1992, 1992, 1991, 1992
    )

    assert complete == 2
    assert baseline_mean == pytest.approx((365.0 + 1098.0) / 2)
    assert baseline_mean != pytest.approx(2.0)
    assert rows[0]["value"] == pytest.approx(1098.0)
    assert rows[0]["anomaly"] == pytest.approx(1098.0 - (365.0 + 1098.0) / 2)

# ---------------------------------------------------------------------------
# Leap-day completeness for DJF and February (an omitted leap day must null)
# ---------------------------------------------------------------------------

def test_winter_missing_leap_day_is_null_with_91_expected_days() -> None:
    daily = _daily_for_years({1991: 11.0, 1992: 12.0}, missing=("19920229",))

    rows = aggregate_period(daily, "temperature", 1992, 1992, "winter")

    assert rows[0].value is None
    assert rows[0].expected_days == 91
    assert rows[0].valid_days == 90

def test_february_missing_leap_day_is_null_not_complete() -> None:
    daily = _daily_for_years({1991: 11.0, 1992: 12.0}, missing=("19920229",))

    rows = aggregate_period(daily, "temperature", 1992, 1992, "m02")

    assert rows[0].value is None
    assert rows[0].expected_days == 29
    assert rows[0].valid_days == 28

def test_negative_temperature_days_are_valid_winter_observations() -> None:
    # Winter temperatures are frequently negative; only precipitation filters
    # negative daily values. An over-broad filter would null the window.
    daily = _daily_for_years({1989: -5.0, 1990: -7.0})

    rows = aggregate_period(daily, "temperature", 1990, 1990, "winter")

    expected = (31 * -5.0 + 31 * -7.0 + 28 * -7.0) / 90
    assert rows[0].value == pytest.approx(expected)
    assert rows[0].valid_days == rows[0].expected_days == 90

# ---------------------------------------------------------------------------
# Fetch spans: one contiguous range per request, cache key stays stable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("start_year", "end_year", "baseline_start_year", "baseline_end_year", "expected"),
    [
        (2000, 2002, 1981, 1985, (1981, 2002)),  # baseline before display
        (1981, 1985, 2000, 2002, (1981, 2002)),  # baseline after display
        (1981, 2002, 1990, 1992, (1981, 2002)),  # baseline inside display
        (1981, 1985, 1990, 1992, (1981, 1992)),  # baseline inside display tail
        (1990, 1994, 1991, 1993, (1990, 1994)),  # fully overlapping
    ],
)
def test_anomaly_fetch_span_returns_union_regardless_of_order(
    start_year: int,
    end_year: int,
    baseline_start_year: int,
    baseline_end_year: int,
    expected: tuple[int, int],
) -> None:
    assert (
        climate.anomaly_fetch_span(
            start_year, end_year, baseline_start_year, baseline_end_year
        )
        == expected
    )

@pytest.mark.parametrize(
    ("period", "expected_start"),
    [
        ("winter", 1989),   # previous December is required
        ("all", 1989),      # 'all' contains winter
        ("spring", 1990),
        ("summer", 1990),
        ("autumn", 1990),
        ("m01", 1990),
        ("m02", 1990),
        ("m12", 1990),
    ],
)
def test_seasonal_fetch_span_reaches_back_only_for_winter_and_all(
    period: str, expected_start: int
) -> None:
    assert climate.seasonal_fetch_span(1990, 1994, period) == (expected_start, 1994)

def test_seasonal_fetch_span_at_coverage_minimum_never_fetches_1980() -> None:
    # 1980 is outside the coverage contract; a winter 1981 fetch must not
    # silently reach into it.
    assert climate.seasonal_fetch_span(COVERAGE_MIN_YEAR, 1985, "winter") == (
        COVERAGE_MIN_YEAR,
        1985,
    )
    assert climate.seasonal_fetch_span(COVERAGE_MIN_YEAR, 1985, "all") == (
        COVERAGE_MIN_YEAR,
        1985,
    )
    assert COVERAGE_MAX_YEAR >= COVERAGE_MIN_YEAR  # sanity: coverage constants

# ---------------------------------------------------------------------------
# Winter 1981 stays null through the API layer ("false missing DJF" guard)
# ---------------------------------------------------------------------------

def test_winter_1981_stays_null_even_with_1980_december_supplied() -> None:
    # Jan-Feb 1981 complete and Dec 1980 deliberately handed in: the value
    # must stay null, December 1980 days must not count, and winter 1982
    # (Dec 1981 + Jan/Feb 1982) must remain computable.
    daily = _daily_for_years({1980: 99.0, 1981: -3.0, 1982: 4.0})

    response = _build_seasonal(daily, "temperature", 1981, 1982, "winter")

    by_year = {point.year: point for point in response.periods[0].series}
    assert by_year[1981].value is None
    assert by_year[1981].valid_days == 59  # Jan + Feb only
    assert by_year[1981].expected_days == 90  # full DJF window

    expected_1982 = (31 * -3.0 + 31 * 4.0 + 28 * 4.0) / 90
    assert by_year[1982].value == pytest.approx(expected_1982)
    assert by_year[1982].valid_days == 90
    assert any("1981" in warning for warning in response.warnings)

def test_api_layer_forces_winter_1981_null_even_if_data_layer_returns_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defense in depth: a (hypothetical) Data layer that returns a complete,
    # non-null winter 1981 aggregate must still yield null through the API
    # response, because 1980-12 is outside the coverage contract.
    stub = types.ModuleType("app.climate_data")

    def aggregate_period(
        daily: Any, variable: Any, start_year: int, end_year: int, period: str
    ) -> list[AnnualAggregate]:
        return [
            AnnualAggregate(year=year, value=7.5, valid_days=90, expected_days=90)
            for year in range(start_year, end_year + 1)
        ]

    stub.aggregate_period = aggregate_period  # type: ignore[attr-defined]
    monkeypatch.setattr("app.climate_data", stub)

    response = _build_seasonal({}, "temperature", 1981, 1982, "winter")

    by_year = {point.year: point for point in response.periods[0].series}
    assert by_year[1981].value is None
    # The guard is specific to the coverage minimum; later years pass through.
    assert by_year[1982].value == pytest.approx(7.5)
