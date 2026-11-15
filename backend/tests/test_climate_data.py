"""Tests for backend/app/climate_data.py (docs/CLIMATE-API.md internal contract).

Every daily fixture is synthetic and deterministic (constants derived from
calendar arithmetic in this file); no test contacts NASA POWER, imports the
networking layer, or monkeypatches it.  Units follow the contract:
temperature daily values are °C and are averaged; precipitation daily
values are mm/day rates integrated over one-day intervals (mm).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.analysis import AnnualAggregate
from app.climate_data import DataContractError, aggregate_period, anomaly_series

ROW_KEYS = {"year", "value", "anomaly", "valid_days", "expected_days"}


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


def _december(year: int, value: float) -> dict[str, float]:
    return {f"{year}12{day:02d}": value for day in range(1, 32)}


# --------------------------------------------------------------------------
# aggregate_period: DJF uses the previous December
# --------------------------------------------------------------------------


def test_winter_uses_previous_december_and_current_jan_feb() -> None:
    # Constant per-year °C values: Dec 1989 = 10, 1990 = 11, 1991 = 12.
    daily = _daily_for_years({1989: 10.0, 1990: 11.0, 1991: 12.0})

    rows = aggregate_period(daily, "temperature", 1990, 1991, "winter")

    assert [row.year for row in rows] == [1990, 1991]
    assert all(isinstance(row, AnnualAggregate) for row in rows)

    # Winter 1990 = Dec 1989 (31 d) + Jan 1990 (31 d) + Feb 1990 (28 d).
    expected_1990 = (31 * 10.0 + 31 * 11.0 + 28 * 11.0) / 90
    assert rows[0].value == pytest.approx(expected_1990)
    assert rows[0].valid_days == 90
    assert rows[0].expected_days == 90
    # Proof that December entered the mean: the Jan-Feb-only mean would be 11.
    assert rows[0].value != pytest.approx(11.0)

    # Winter 1991 = Dec 1990 + Jan/Feb 1991, not Dec 1991 (which is absent
    # from the display year's own window but present in the daily input).
    expected_1991 = (31 * 11.0 + 31 * 12.0 + 28 * 12.0) / 90
    assert rows[1].value == pytest.approx(expected_1991)
    assert rows[1].valid_days == 90
    assert rows[1].expected_days == 90


def test_winter_1981_is_null_and_prior_december_days_are_not_counted() -> None:
    daily = _daily_for_years({1981: 1.0, 1982: 2.0, 1983: 3.0})

    rows = aggregate_period(daily, "temperature", 1981, 1982, "winter")

    # Winter 1981 stays null even though Jan-Feb 1981 are complete: its
    # December (1980) is outside the coverage contract.
    assert rows[0].year == 1981
    assert rows[0].value is None
    assert rows[0].valid_days == 31 + 28  # Jan + Feb only
    assert rows[0].expected_days == 90  # full DJF window

    # Winter 1982 is complete: Dec 1981 + Jan/Feb 1982.
    expected_1982 = (31 * 1.0 + 31 * 2.0 + 28 * 2.0) / 90
    assert rows[1].value == pytest.approx(expected_1982)
    assert rows[1].valid_days == 90

    # Even if out-of-coverage 1980 December days are handed in, they are
    # neither counted as valid nor used; winter 1981 remains null.
    guarded_daily = daily | _december(1980, 99.0)
    guarded = aggregate_period(guarded_daily, "temperature", 1981, 1981, "winter")
    assert guarded[0].value is None
    assert guarded[0].valid_days == 59
    assert guarded[0].expected_days == 90


def test_single_missing_day_nullifies_the_period_value() -> None:
    daily = _daily_for_years(
        {1989: 10.0, 1990: 11.0}, missing=("19900215", "19891215")
    )

    rows = aggregate_period(daily, "temperature", 1990, 1990, "winter")

    assert rows[0].value is None
    assert rows[0].valid_days == 88  # one day missing from Dec 1989, one from Feb
    assert rows[0].expected_days == 90


def test_invalid_daily_values_nullify_the_period_value() -> None:
    temperature = _daily_for_years(
        {1989: 10.0, 1990: 11.0},
        overrides={"19900105": float("nan"), "19900106": float("inf")},
    )
    rows = aggregate_period(temperature, "temperature", 1990, 1990, "winter")
    assert rows[0].value is None
    assert rows[0].valid_days == 88
    assert rows[0].expected_days == 90

    # Negative precipitation rates are invalid days (not clamped to zero).
    precipitation = _daily_for_years(
        {1990: 2.0}, overrides={"19900710": -0.5}
    )
    summer = aggregate_period(precipitation, "precipitation", 1990, 1990, "summer")
    assert summer[0].value is None
    assert summer[0].valid_days == 91
    assert summer[0].expected_days == 92


def test_leap_day_counts_in_winter_and_february() -> None:
    daily = _daily_for_years({1991: 11.0, 1992: 12.0})

    winter = aggregate_period(daily, "temperature", 1992, 1992, "winter")
    expected_winter = (31 * 11.0 + 31 * 12.0 + 29 * 12.0) / 91  # Feb 29 included
    assert winter[0].value == pytest.approx(expected_winter)
    assert winter[0].valid_days == 91
    assert winter[0].expected_days == 91

    february = aggregate_period(daily, "temperature", 1991, 1992, "m02")
    assert [row.year for row in february] == [1991, 1992]
    assert february[0].value == pytest.approx(11.0)
    assert february[0].expected_days == 28
    assert february[1].value == pytest.approx(12.0)
    assert february[1].expected_days == 29  # leap February
    assert february[1].valid_days == 29


def test_precipitation_sums_daily_rates_over_the_window() -> None:
    # Constant per-year rates (mm/day): 1990 = 2.0, 1991 = 2.5, 1992 = 3.0.
    daily = _daily_for_years({1990: 2.0, 1991: 2.5, 1992: 3.0})

    summer = aggregate_period(daily, "precipitation", 1990, 1990, "summer")
    assert summer[0].value == pytest.approx(92 * 2.0)  # 30+31+31 days
    assert summer[0].valid_days == 92

    january = aggregate_period(daily, "precipitation", 1991, 1991, "m01")
    assert january[0].value == pytest.approx(31 * 2.5)
    assert january[0].expected_days == 31

    autumn = aggregate_period(daily, "precipitation", 1991, 1991, "autumn")
    assert autumn[0].value == pytest.approx(91 * 2.5)  # 30+31+30 days

    winter = aggregate_period(daily, "precipitation", 1992, 1992, "winter")
    expected = 31 * 2.5 + 31 * 3.0 + 29 * 3.0  # Dec 1991 + Jan + Feb (leap) 1992
    assert winter[0].value == pytest.approx(expected)
    assert winter[0].valid_days == winter[0].expected_days == 91


def test_aggregate_period_rejects_all_and_invalid_inputs() -> None:
    daily = _daily_for_years({1990: 1.0, 1991: 2.0})

    # 'all' is assembled by the API layer; this contract takes one code only.
    with pytest.raises(DataContractError, match="single code"):
        aggregate_period(daily, "temperature", 1990, 1991, "all")
    for period in ("m00", "m13", "Winter", "djf", ""):
        with pytest.raises(DataContractError):
            aggregate_period(daily, "temperature", 1990, 1991, period)
    with pytest.raises(DataContractError, match="variable"):
        aggregate_period(daily, "humidity", 1990, 1991, "spring")
    with pytest.raises(DataContractError, match="start_year"):
        aggregate_period(daily, "temperature", 1991, 1990, "spring")
    with pytest.raises(DataContractError, match="mapping"):
        aggregate_period([("19900101", 1.0)], "temperature", 1990, 1991, "spring")


# --------------------------------------------------------------------------
# anomaly_series
# --------------------------------------------------------------------------


def test_anomaly_series_basic_with_leap_display_year() -> None:
    annual = {1990: 10.0, 1991: 11.0, 1992: 12.0, 1993: 13.0, 1994: 14.0}
    daily = _daily_for_years(annual)

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 1990, 1994, 1990, 1994
    )

    assert [row["year"] for row in rows] == list(range(1990, 1995))
    assert all(set(row) == ROW_KEYS for row in rows)
    assert baseline_mean == pytest.approx(12.0)
    assert complete == 5
    assert [row["anomaly"] for row in rows] == pytest.approx([-2.0, -1.0, 0.0, 1.0, 2.0])
    assert [row["value"] for row in rows] == pytest.approx(list(annual.values()))

    leap = rows[2]
    assert leap["year"] == 1992
    assert leap["expected_days"] == 366
    assert leap["valid_days"] == 366
    assert leap["value"] == pytest.approx(12.0)
    # The zero anomaly is a real float, not a null.
    assert leap["anomaly"] == 0.0
    assert leap["anomaly"] is not None
    assert isinstance(baseline_mean, float)
    assert isinstance(complete, int)


def test_anomaly_series_baseline_outside_display_period() -> None:
    # Display 2000-2002, reference baseline 1981-1985; the union span
    # (1981-2002) is aggregated once and gap years never appear as rows.
    value_by_year = {1981: 1.0, 1982: 2.0, 1983: 3.0, 1984: 4.0, 1985: 5.0}
    value_by_year |= {2000: 100.0, 2001: 101.0, 2002: 102.0}
    daily = _daily_for_years(value_by_year)

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 2000, 2002, 1981, 1985
    )

    assert [row["year"] for row in rows] == [2000, 2001, 2002]
    assert baseline_mean == pytest.approx(3.0)  # mean of 1..5
    assert complete == 5
    assert [row["value"] for row in rows] == pytest.approx([100.0, 101.0, 102.0])
    assert [row["anomaly"] for row in rows] == pytest.approx([97.0, 98.0, 99.0])
    assert [row["expected_days"] for row in rows] == [366, 365, 365]  # 2000 is leap


def test_anomaly_series_zero_anomaly_against_complete_baseline() -> None:
    value_by_year = {year: 7.5 for year in range(1990, 1995)}
    value_by_year[2000] = 7.5
    daily = _daily_for_years(value_by_year)

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 2000, 2000, 1990, 1994
    )

    assert baseline_mean == 7.5
    assert complete == 5
    assert rows[0]["value"] == 7.5
    assert rows[0]["anomaly"] == 0.0  # exact zero, not None


@pytest.mark.parametrize(
    "breakage",
    ["missing-day", "nan-day"],
)
def test_strict_baseline_single_bad_day_nullifies_mean_and_all_anomalies(
    breakage: str,
) -> None:
    value_by_year = {year: float(year - 1989) for year in range(1990, 1995)}
    value_by_year |= {1995: 20.0, 1996: 21.0, 1997: 22.0}
    missing: tuple[str, ...] = ()
    overrides: dict[str, float] | None = None
    if breakage == "missing-day":
        missing = ("19930701",)
    else:
        overrides = {"19930701": float("nan")}
    daily = _daily_for_years(value_by_year, missing=missing, overrides=overrides)

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 1995, 1997, 1990, 1994
    )

    # Strict rule: one invalid baseline day is enough to null everything.
    assert baseline_mean is None
    assert complete == 4  # 1990, 1991, 1992, 1994 stayed complete
    assert all(row["anomaly"] is None for row in rows)
    # Display values themselves are unaffected and stay non-null.
    assert [row["value"] for row in rows] == pytest.approx([20.0, 21.0, 22.0])


def test_strict_baseline_negative_precip_day_nullifies_mean_and_anomalies() -> None:
    daily = _daily_for_years(
        {1990: 1.0, 1991: 1.0, 1992: 1.0},  # rates; annual sums 365/365/366 mm
        overrides={"19910901": -1.0},
    )

    rows, baseline_mean, complete = anomaly_series(
        daily, "precipitation", 1992, 1992, 1990, 1992
    )

    assert baseline_mean is None
    assert complete == 2
    assert rows[0]["value"] == pytest.approx(366.0)
    assert rows[0]["anomaly"] is None


def test_missing_display_year_stays_null_but_baseline_survives() -> None:
    value_by_year = {
        1985: 5.0, 1986: 6.0, 1987: 7.0, 1988: 8.0, 1989: 9.0,
        1990: 10.0, 1991: 11.0, 1992: 12.0, 1993: 13.0, 1994: 14.0,
    }
    daily = _daily_for_years(value_by_year, missing=("19920229",))

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 1990, 1994, 1985, 1989
    )

    assert baseline_mean == pytest.approx(7.0)  # mean of 5..9
    assert complete == 5
    by_year = {row["year"]: row for row in rows}
    assert by_year[1992]["value"] is None
    assert by_year[1992]["anomaly"] is None
    assert by_year[1992]["valid_days"] == 365  # the leap day was missing
    assert by_year[1992]["expected_days"] == 366
    for year in (1990, 1991, 1993, 1994):
        assert by_year[year]["value"] is not None
        assert by_year[year]["anomaly"] == pytest.approx(value_by_year[year] - 7.0)


def test_anomaly_series_supports_overlapping_baseline_and_display() -> None:
    value_by_year = {1990: 1.0, 1991: 2.0, 1992: 3.0, 1993: 4.0, 1994: 5.0}
    daily = _daily_for_years(value_by_year)

    rows, baseline_mean, complete = anomaly_series(
        daily, "temperature", 1992, 1994, 1990, 1992
    )

    assert baseline_mean == pytest.approx(2.0)  # mean of 1, 2, 3
    assert complete == 3
    assert [row["year"] for row in rows] == [1992, 1993, 1994]
    assert [row["anomaly"] for row in rows] == pytest.approx([1.0, 2.0, 3.0])


def test_anomaly_series_precipitation_uses_annual_sums() -> None:
    daily = _daily_for_years({1990: 1.0, 1991: 1.0, 1992: 1.0})

    rows, baseline_mean, complete = anomaly_series(
        daily, "precipitation", 1990, 1992, 1990, 1992
    )

    annual_sums = [365.0, 365.0, 366.0]  # 1 mm/day integrated over each day
    mean = sum(annual_sums) / 3
    assert baseline_mean == pytest.approx(mean)
    assert complete == 3
    assert [row["value"] for row in rows] == pytest.approx(annual_sums)
    assert [row["anomaly"] for row in rows] == pytest.approx(
        [value - mean for value in annual_sums]
    )


def test_anomaly_series_rejects_invalid_inputs() -> None:
    daily = _daily_for_years({1990: 1.0, 1991: 2.0})

    with pytest.raises(DataContractError, match="start_year"):
        anomaly_series(daily, "temperature", 1991, 1990, 1990, 1991)
    with pytest.raises(DataContractError, match="baseline"):
        anomaly_series(daily, "temperature", 1990, 1991, 1991, 1990)
    with pytest.raises(DataContractError, match="start_year"):
        anomaly_series(daily, "temperature", 1980, 1990, 1990, 1990)
    with pytest.raises(DataContractError, match="end_year"):
        anomaly_series(daily, "temperature", 1990, 2026, 1990, 1990)
    with pytest.raises(DataContractError, match="baseline_start_year"):
        anomaly_series(daily, "temperature", 1990, 1991, 1980, 1990)
    with pytest.raises(DataContractError, match="baseline_end_year"):
        anomaly_series(daily, "temperature", 1990, 1991, 1990, 2026)
    with pytest.raises(DataContractError, match="variable"):
        anomaly_series(daily, "humidity", 1990, 1991, 1990, 1991)
    with pytest.raises(DataContractError, match="mapping"):
        anomaly_series([("19900101", 1.0)], "temperature", 1990, 1991, 1990, 1991)
