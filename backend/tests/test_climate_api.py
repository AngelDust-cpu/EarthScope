"""Contract tests for ``GET /api/anomalies`` and ``GET /api/seasonal``.

Every POWER response in this module is a deterministic synthetic fixture and
``app.main.load_daily`` is monkeypatched, so no test contacts NASA and no
synthetic data enters the runtime application path.

``backend/app/climate_data.py`` is owned by the Data role.  Its two documented
functions are replaced here by :class:`FakeClimateData`, an independent test
double that implements the contract in ``docs/CLIMATE-API.md``; the API-level
behaviour (validation before fetch, one daily range per request, response
coherence and error envelopes) is what these tests pin down.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app import analysis, climate
from app.power import PowerError
from app.schemas import (
    COVERAGE_MIN_YEAR,
    MIN_TREND_YEARS,
    AnomalyPoint,
    AnomalyResponse,
    SeasonalPeriodEntry,
    SeasonalResponse,
    SeriesPoint,
    SourceInfo,
    TrendStats,
)

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URL = "https://example.invalid/earthscope-climate-synthetic-test"
RETRIEVED_AT = "2026-11-14T12:34:56Z"

ANOMALY_KEYS = {
    "latitude",
    "longitude",
    "variable",
    "start_year",
    "end_year",
    "baseline_start_year",
    "baseline_end_year",
    "unit",
    "aggregation",
    "baseline_mean",
    "baseline_complete_years",
    "series",
    "source",
    "warnings",
}
ANOMALY_POINT_KEYS = {"year", "value", "anomaly", "valid_days", "expected_days"}

SEASONAL_KEYS = {
    "latitude",
    "longitude",
    "variable",
    "start_year",
    "end_year",
    "period",
    "unit",
    "aggregation",
    "periods",
    "source",
    "warnings",
}
PERIOD_KEYS = {"period", "series", "trend"}
SERIES_KEYS = {"year", "value", "valid_days", "expected_days", "fitted"}

_SEASON_MONTHS: dict[str, tuple[int, ...]] = {
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
    "autumn": (9, 10, 11),
}


def _source(variable: str, cached: bool = False) -> dict[str, object]:
    return {
        "name": "NASA POWER",
        "parameter": "T2M" if variable == "temperature" else "PRECTOTCORR",
        "daily_unit": "C" if variable == "temperature" else "mm/day",
        "dataset": SYNTHETIC_DATASET,
        "time_standard": "UTC",
        "request_url": SYNTHETIC_URL,
        "retrieved_at": RETRIEVED_AT,
        "cached": cached,
    }


def _days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def _year_dates(year: int) -> Iterator[date]:
    current = date(year, 1, 1)
    while current.year == year:
        yield current
        current += timedelta(days=1)


def _build_daily(
    variable: str,
    annual_values: Mapping[int, float],
    *,
    missing: Sequence[str] = (),
    overrides: Mapping[str, float] | None = None,
) -> dict[str, float]:
    missing_keys = set(missing)
    daily: dict[str, float] = {}
    for year, annual_target in annual_values.items():
        days = list(_year_dates(year))
        daily_value = (
            annual_target if variable == "temperature" else annual_target / len(days)
        )
        for day in days:
            key = f"{year:04d}{day.month:02d}{day.day:02d}"
            if key not in missing_keys:
                daily[key] = daily_value
    if overrides:
        daily.update(overrides)
    return daily


def _install_daily(
    monkeypatch: pytest.MonkeyPatch,
    daily: Mapping[str, float],
    variable: str,
    *,
    cached: bool = False,
) -> list[dict[str, object]]:
    """Serve one synthetic daily series and record every load_daily call."""

    source = _source(variable, cached)
    calls: list[dict[str, object]] = []

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        return dict(daily), source

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    return calls


def _install_power(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    annual_values: Mapping[int, float],
    *,
    missing: Sequence[str] = (),
    overrides: Mapping[str, float] | None = None,
    cached: bool = False,
) -> list[dict[str, object]]:
    daily = _build_daily(
        variable, annual_values, missing=missing, overrides=overrides
    )
    return _install_daily(monkeypatch, daily, variable, cached=cached)


def _month_dates(year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, day) for day in range(1, last + 1)]


def _period_dates(year: int, period: str) -> list[date]:
    if period == "winter":
        # Previous December + current January and February.
        return _month_dates(year - 1, 12) + _month_dates(year, 1) + _month_dates(year, 2)
    if period in _SEASON_MONTHS:
        return [
            day
            for month in _SEASON_MONTHS[period]
            for day in _month_dates(year, month)
        ]
    return _month_dates(year, int(period[1:]))


def _period_aggregate(
    daily: Mapping[str, float], variable: str, year: int, period: str
) -> analysis.AnnualAggregate:
    dates = _period_dates(year, period)
    values: list[float] = []
    for day in dates:
        value = daily.get(f"{day:%Y%m%d}")
        if value is None or not math.isfinite(value):
            continue
        if variable == "precipitation" and value < 0.0:
            continue
        values.append(float(value))
    expected = len(dates)
    value: float | None = None
    if values and len(values) == expected:
        value = (
            sum(values) / len(values)
            if variable == "temperature"
            else sum(values)
        )
    return analysis.AnnualAggregate(
        year=year,
        value=value,
        valid_days=len(values),
        expected_days=expected,
    )


class FakeClimateData:
    """Test double for the Data-owned ``app.climate_data`` contract."""

    def anomaly_series(
        self,
        daily: Mapping[str, float],
        variable: str,
        start_year: int,
        end_year: int,
        baseline_start_year: int,
        baseline_end_year: int,
    ) -> tuple[list[dict[str, object]], float | None, int]:
        display = analysis.aggregate_annual(daily, variable, start_year, end_year)
        baseline = analysis.aggregate_annual(
            daily, variable, baseline_start_year, baseline_end_year
        )
        span = baseline_end_year - baseline_start_year + 1
        complete = sum(1 for item in baseline if item.value is not None)
        mean: float | None = None
        if span > 0 and complete == span:
            mean = sum(float(item.value) for item in baseline) / span
        rows: list[dict[str, object]] = [
            {
                "year": item.year,
                "value": item.value,
                "anomaly": (
                    None
                    if item.value is None or mean is None
                    else float(item.value) - mean
                ),
                "valid_days": item.valid_days,
                "expected_days": item.expected_days,
            }
            for item in display
        ]
        return rows, mean, complete

    def aggregate_period(
        self,
        daily: Mapping[str, float],
        variable: str,
        start_year: int,
        end_year: int,
        period: str,
    ) -> list[analysis.AnnualAggregate]:
        return [
            _period_aggregate(daily, variable, year, period)
            for year in range(start_year, end_year + 1)
        ]


def _install_climate_data(monkeypatch: pytest.MonkeyPatch) -> FakeClimateData:
    stub = FakeClimateData()
    monkeypatch.setattr(climate, "_climate_data", lambda: stub)
    return stub


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "latitude": 42.87,
        "longitude": 74.60,
        "variable": "temperature",
        "start_year": 1990,
        "end_year": 1994,
    }
    params.update(overrides)
    return params


def _assert_error(response: object, status_code: int, code: str) -> dict[str, str]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message"}
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    return payload["error"]


def _expected_call(
    variable: str, start_year: int, end_year: int
) -> dict[str, object]:
    return {
        "latitude": 42.87,
        "longitude": 74.60,
        "variable": variable,
        "start_year": start_year,
        "end_year": end_year,
    }


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# GET /api/anomalies
# ---------------------------------------------------------------------------


def test_anomalies_use_one_union_fetch_and_equally_weighted_baseline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 10.0 + 0.5 * (year - 1985) for year in range(1985, 1995)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/anomalies",
        params=_params(baseline_start_year=1985, baseline_end_year=1989),
    )
    assert response.status_code == 200
    payload = response.json()

    assert set(payload) == ANOMALY_KEYS
    assert all(set(point) == ANOMALY_POINT_KEYS for point in payload["series"])
    assert payload["variable"] == "temperature"
    assert payload["unit"] == "°C"
    assert payload["aggregation"] == "mean"
    assert payload["start_year"] == 1990
    assert payload["end_year"] == 1994
    assert payload["baseline_start_year"] == 1985
    assert payload["baseline_end_year"] == 1989

    baseline_mean = sum(annual[year] for year in range(1985, 1990)) / 5
    assert payload["baseline_mean"] == pytest.approx(baseline_mean)
    assert payload["baseline_complete_years"] == 5
    assert [point["year"] for point in payload["series"]] == list(range(1990, 1995))

    for point in payload["series"]:
        assert point["value"] == pytest.approx(annual[point["year"]])
        assert point["anomaly"] == pytest.approx(annual[point["year"]] - baseline_mean)
        assert point["valid_days"] == _days_in_year(point["year"])
        assert point["expected_days"] == _days_in_year(point["year"])

    # One contiguous request spanning display and baseline: the baseline never
    # triggers a second download, and the exact-query cache key stays reusable.
    assert calls == [_expected_call("temperature", 1985, 1994)]
    # A five-year baseline is a reference period, not a 30-year climate normal.
    assert any("not a 30-year climate normal" in w for w in payload["warnings"])
    assert not any("prevented an annual value" in w for w in payload["warnings"])


def test_anomalies_baseline_after_display_still_uses_one_fetch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 5.0 + 0.25 * (year - 1990) for year in range(1990, 2005)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/anomalies",
        params=_params(
            start_year=1990,
            end_year=1992,
            baseline_start_year=2000,
            baseline_end_year=2004,
        ),
    )
    assert response.status_code == 200
    payload = response.json()

    baseline_mean = sum(annual[year] for year in range(2000, 2005)) / 5
    assert payload["baseline_mean"] == pytest.approx(baseline_mean)
    assert [point["year"] for point in payload["series"]] == [1990, 1991, 1992]
    for point in payload["series"]:
        assert point["anomaly"] == pytest.approx(annual[point["year"]] - baseline_mean)
    assert calls == [_expected_call("temperature", 1990, 2004)]


def test_anomalies_overlapping_baseline_is_still_a_single_fetch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 12.0 + 0.1 * (year - 2000) for year in range(2000, 2011)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/anomalies",
        params=_params(
            start_year=2000,
            end_year=2010,
            baseline_start_year=2005,
            baseline_end_year=2009,
        ),
    )
    assert response.status_code == 200
    assert response.json()["baseline_complete_years"] == 5
    assert calls == [_expected_call("temperature", 2000, 2010)]


def test_anomalies_incomplete_baseline_nulls_the_mean_and_every_anomaly(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 10.0 + (year - 1985) for year in range(1985, 1995)}
    _install_power(
        monkeypatch,
        "temperature",
        annual,
        missing=("19870301",),
    )
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/anomalies",
        params=_params(baseline_start_year=1985, baseline_end_year=1989),
    )
    assert response.status_code == 200
    payload = response.json()

    # Strict rule: one invalid baseline day invalidates the whole baseline.
    assert payload["baseline_mean"] is None
    assert payload["baseline_complete_years"] == 4
    assert all(point["anomaly"] is None for point in payload["series"])
    # Display values are unaffected by the unusable baseline.
    assert all(point["value"] is not None for point in payload["series"])
    assert any("baseline mean and all anomalies are null" in w for w in payload["warnings"])


def test_anomalies_incomplete_display_year_stays_null(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 10.0 + (year - 1985) for year in range(1985, 1995)}
    _install_power(
        monkeypatch,
        "temperature",
        annual,
        missing=("19920615",),
    )
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/anomalies",
        params=_params(baseline_start_year=1985, baseline_end_year=1989),
    )
    assert response.status_code == 200
    payload = response.json()

    incomplete = payload["series"][2]
    assert incomplete["year"] == 1992
    assert incomplete["value"] is None
    assert incomplete["anomaly"] is None
    assert incomplete["valid_days"] == 365
    assert incomplete["expected_days"] == 366
    assert any("1992" in warning for warning in payload["warnings"])

    complete = [point for point in payload["series"] if point["year"] != 1992]
    assert all(point["value"] is not None for point in complete)
    assert all(point["anomaly"] is not None for point in complete)


def test_anomalies_short_custom_baseline_is_a_reference_not_a_normal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 8.0 + 0.1 * (year - 2010) for year in range(2010, 2015)}
    _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/anomalies",
        params=_params(
            start_year=2012,
            end_year=2014,
            baseline_start_year=2010,
            baseline_end_year=2014,
        ),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["baseline_mean"] is not None
    assert payload["baseline_complete_years"] == 5
    assert any("not a 30-year climate normal" in w for w in payload["warnings"])
    assert not any("is incomplete" in w for w in payload["warnings"])


def test_anomalies_default_baseline_is_the_1991_2020_preset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 9.0 + 0.01 * (year - 1991) for year in range(1991, 2021)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get("/api/anomalies", params=_params(start_year=2000, end_year=2004))
    assert response.status_code == 200
    payload = response.json()

    assert payload["baseline_start_year"] == 1991
    assert payload["baseline_end_year"] == 2020
    assert payload["baseline_complete_years"] == 30
    assert payload["baseline_mean"] is not None
    assert calls == [_expected_call("temperature", 1991, 2020)]
    assert payload["warnings"] == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": 90.0001},
        {"longitude": -180.0001},
        {"latitude": "nan"},
        {"variable": "humidity"},
        {"start_year": 1980},
        {"end_year": 2026},
        {"start_year": 2001, "end_year": 2000},
        {"baseline_start_year": 1980},
        {"baseline_end_year": 2026},
        {"baseline_start_year": 2010, "baseline_end_year": 2005},
        {"start_year": "not-a-year"},
    ],
)
def test_anomalies_invalid_inputs_are_rejected_before_fetch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    calls = _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 for year in range(1990, 1995)},
    )
    _install_climate_data(monkeypatch)

    response = client.get("/api/anomalies", params=_params(**overrides))
    _assert_error(response, 422, "INVALID_REQUEST")
    assert calls == []


def test_anomalies_missing_query_parameter_uses_error_envelope(
    client: TestClient,
) -> None:
    params = _params()
    params.pop("latitude")
    response = client.get("/api/anomalies", params=params)
    error = _assert_error(response, 422, "INVALID_REQUEST")
    assert "latitude" in error["message"]


def test_anomalies_requires_get(client: TestClient) -> None:
    response = client.post("/api/anomalies", params=_params())
    _assert_error(response, 405, "METHOD_NOT_ALLOWED")


def test_anomalies_power_failure_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_climate_data(monkeypatch)

    def failing_load_daily(**_kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("synthetic anomaly outage")

    monkeypatch.setattr(main, "load_daily", failing_load_daily)
    response = client.get("/api/anomalies", params=_params())
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "synthetic anomaly outage" in error["message"]


def test_anomalies_malformed_source_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_source = _source("temperature")
    bad_source["daily_unit"] = "mm/day"  # deliberately wrong for T2M
    calls: list[dict[str, object]] = []

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        return _build_daily("temperature", {1990: 1.0}), bad_source

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    _install_climate_data(monkeypatch)

    response = client.get("/api/anomalies", params=_params(end_year=1990))
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "validation" in error["message"]
    assert len(calls) == 1


class _BrokenAnomalyData:
    """Returns an anomaly that the response contract must reject."""

    def anomaly_series(self, *_args: object, **_kwargs: object):
        return (
            [
                {
                    "year": 1990,
                    "value": 1.0,
                    "anomaly": 0.5,
                    "valid_days": 365,
                    "expected_days": 365,
                }
            ],
            None,
            0,
        )

    def aggregate_period(self, *_args: object, **_kwargs: object):
        return []


def test_anomalies_incoherent_data_contract_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_power(monkeypatch, "temperature", {1990: 1.0})
    monkeypatch.setattr(climate, "_climate_data", lambda: _BrokenAnomalyData())

    response = client.get("/api/anomalies", params=_params(end_year=1990))
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "validation" in error["message"]


def test_anomalies_missing_climate_module_is_explicit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_power(monkeypatch, "temperature", {1990: 1.0})

    def missing() -> Any:
        raise climate.ClimateDataUnavailable("synthetic missing module")

    monkeypatch.setattr(climate, "_climate_data", missing)

    with pytest.raises(climate.ClimateDataUnavailable):
        client.get("/api/anomalies", params=_params(end_year=1990))


def test_anomalies_integrates_with_the_real_climate_data_module(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end against ``backend/app/climate_data.py`` (no stub)."""

    annual = {year: 10.0 + 0.5 * (year - 1991) for year in range(1991, 2021)}
    calls = _install_power(monkeypatch, "temperature", annual)

    response = client.get(
        "/api/anomalies", params=_params(start_year=2015, end_year=2019)
    )
    assert response.status_code == 200
    payload = response.json()

    mean = sum(annual[year] for year in range(1991, 2021)) / 30
    assert payload["baseline_start_year"] == 1991
    assert payload["baseline_end_year"] == 2020
    assert payload["baseline_complete_years"] == 30
    assert payload["baseline_mean"] == pytest.approx(mean)
    assert [point["year"] for point in payload["series"]] == list(range(2015, 2020))
    for point in payload["series"]:
        assert point["anomaly"] == pytest.approx(annual[point["year"]] - mean)
    assert calls == [_expected_call("temperature", 1991, 2020)]


# ---------------------------------------------------------------------------
# GET /api/seasonal
# ---------------------------------------------------------------------------


def test_seasonal_all_returns_four_seasons_from_one_fetch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1989 is included because winter 1990 needs the previous December.
    annual = {year: 10.0 + 0.5 * (year - 1990) for year in range(1989, 1995)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/seasonal", params=_params(period="all")
    )
    assert response.status_code == 200
    payload = response.json()

    assert set(payload) == SEASONAL_KEYS
    assert payload["period"] == "all"
    assert payload["unit"] == "°C"
    assert payload["aggregation"] == "mean"
    assert [entry["period"] for entry in payload["periods"]] == [
        "winter",
        "spring",
        "summer",
        "autumn",
    ]

    for entry in payload["periods"]:
        assert set(entry) == PERIOD_KEYS
        assert all(set(point) == SERIES_KEYS for point in entry["series"])
        assert [point["year"] for point in entry["series"]] == list(range(1990, 1995))
        assert entry["trend"]["years_used"] == 5

    winter = payload["periods"][0]["series"]
    for point in winter:
        expected = 90 + (1 if calendar.isleap(point["year"]) else 0)
        assert point["expected_days"] == expected
        assert point["valid_days"] == expected

    summer = payload["periods"][2]["series"]
    assert all(point["expected_days"] == 92 for point in summer)

    # Winter needs the previous December: one request covering 1989-1994.
    assert calls == [_expected_call("temperature", 1989, 1994)]
    assert payload["warnings"] == []


def test_seasonal_winter_1981_stays_null_without_requesting_1980(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 4.0 + (year - COVERAGE_MIN_YEAR) for year in range(1981, 1984)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/seasonal",
        params=_params(start_year=1981, end_year=1983, period="winter"),
    )
    assert response.status_code == 200
    payload = response.json()

    series = payload["periods"][0]["series"]
    assert series[0]["year"] == 1981
    assert series[0]["value"] is None
    assert series[1]["value"] is not None
    assert series[2]["value"] is not None
    # 1980 is outside coverage, so it is never requested.
    assert calls == [_expected_call("temperature", 1981, 1983)]
    assert payload["periods"][0]["trend"] is None
    assert any("at least 5" in warning for warning in payload["warnings"])


def test_seasonal_single_month_does_not_extend_the_span(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 20.0 + 0.4 * (year - 1990) for year in range(1990, 1995)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get("/api/seasonal", params=_params(period="m07"))
    assert response.status_code == 200
    payload = response.json()

    assert payload["period"] == "m07"
    assert len(payload["periods"]) == 1
    entry = payload["periods"][0]
    assert entry["period"] == "m07"
    assert [point["year"] for point in entry["series"]] == list(range(1990, 1995))
    assert all(point["expected_days"] == 31 for point in entry["series"])
    assert entry["trend"]["years_used"] == 5
    assert calls == [_expected_call("temperature", 1990, 1994)]


def test_seasonal_single_season_echoes_the_period(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 18.0 + 0.3 * (year - 1990) for year in range(1990, 1995)}
    calls = _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get("/api/seasonal", params=_params(period="summer"))
    assert response.status_code == 200
    payload = response.json()

    assert payload["period"] == "summer"
    assert [entry["period"] for entry in payload["periods"]] == ["summer"]
    assert all(point["expected_days"] == 92 for point in payload["periods"][0]["series"])
    # A summer-only request does not need the previous December.
    assert calls == [_expected_call("temperature", 1990, 1994)]


def test_seasonal_trend_requires_five_complete_years(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 15.0 + (year - 1990) for year in range(1990, 1993)}
    _install_power(monkeypatch, "temperature", annual)
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/seasonal", params=_params(start_year=1990, end_year=1992, period="spring")
    )
    assert response.status_code == 200
    payload = response.json()

    entry = payload["periods"][0]
    assert entry["trend"] is None
    assert all(point["fitted"] is None for point in entry["series"])
    assert any(
        f"at least {MIN_TREND_YEARS}" in warning for warning in payload["warnings"]
    )
    assert all(warning.startswith("spring:") for warning in payload["warnings"])


def test_seasonal_missing_day_nulls_only_the_affected_period(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 22.0 + (year - 1990) for year in range(1990, 1995)}
    _install_power(monkeypatch, "temperature", annual, missing=("19900715",))
    _install_climate_data(monkeypatch)

    july = client.get("/api/seasonal", params=_params(period="m07"))
    assert july.status_code == 200
    july_series = july.json()["periods"][0]["series"]
    assert july_series[0]["year"] == 1990
    assert july_series[0]["value"] is None
    assert july_series[0]["valid_days"] == 30
    assert july_series[0]["expected_days"] == 31
    assert any("1990" in warning for warning in july.json()["warnings"])

    january = client.get("/api/seasonal", params=_params(period="m01"))
    assert january.status_code == 200
    january_series = january.json()["periods"][0]["series"]
    assert all(point["value"] is not None for point in january_series)


def test_seasonal_precipitation_uses_period_sums(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = {
        f"{day:%Y%m%d}": 1.0
        for year in range(1990, 1995)
        for day in _year_dates(year)
    }
    _install_daily(monkeypatch, daily, "precipitation")
    _install_climate_data(monkeypatch)

    response = client.get(
        "/api/seasonal", params=_params(variable="precipitation", period="summer")
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["unit"] == "mm"
    assert payload["aggregation"] == "sum"
    for point in payload["periods"][0]["series"]:
        # One millimetre per day integrated over the 92 summer days.
        assert point["value"] == pytest.approx(92.0)
    assert payload["periods"][0]["trend"]["per_decade"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"period": "monsoon"},
        {"period": "m13"},
        {"period": "ALL"},
        {"period": ""},
        {"latitude": 90.0001},
        {"longitude": 180.0001},
        {"variable": "humidity"},
        {"start_year": 1980},
        {"end_year": 2026},
        {"start_year": 2001, "end_year": 2000},
    ],
)
def test_seasonal_invalid_inputs_are_rejected_before_fetch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    calls = _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 for year in range(1990, 1995)},
    )
    _install_climate_data(monkeypatch)

    response = client.get("/api/seasonal", params=_params(**overrides))
    _assert_error(response, 422, "INVALID_REQUEST")
    assert calls == []


def test_seasonal_invalid_period_message_lists_allowed_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_power(monkeypatch, "temperature", {1990: 1.0})
    _install_climate_data(monkeypatch)

    response = client.get("/api/seasonal", params=_params(period="monsoon"))
    error = _assert_error(response, 422, "INVALID_REQUEST")
    for allowed in ("winter", "autumn", "all", "m01", "m12"):
        assert allowed in error["message"]
    assert calls == []


def test_seasonal_missing_query_parameter_uses_error_envelope(
    client: TestClient,
) -> None:
    params = _params(period="all")
    params.pop("variable")
    response = client.get("/api/seasonal", params=params)
    error = _assert_error(response, 422, "INVALID_REQUEST")
    assert "variable" in error["message"]


def test_seasonal_requires_get(client: TestClient) -> None:
    response = client.post("/api/seasonal", params=_params(period="all"))
    _assert_error(response, 405, "METHOD_NOT_ALLOWED")


def test_seasonal_power_failure_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_climate_data(monkeypatch)

    def failing_load_daily(**_kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("synthetic seasonal outage")

    monkeypatch.setattr(main, "load_daily", failing_load_daily)
    response = client.get("/api/seasonal", params=_params(period="all"))
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "synthetic seasonal outage" in error["message"]


def test_seasonal_malformed_period_aggregates_map_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BadAggregates:
        def anomaly_series(self, *_args: object, **_kwargs: object):
            return [], None, 0

        def aggregate_period(self, *_args: object, **_kwargs: object):
            # Wrong year set: the contract requires every requested year.
            return [
                analysis.AnnualAggregate(
                    year=1991, value=1.0, valid_days=92, expected_days=92
                )
            ]

    _install_power(monkeypatch, "temperature", {1990: 1.0})
    monkeypatch.setattr(climate, "_climate_data", lambda: _BadAggregates())

    response = client.get("/api/seasonal", params=_params(period="summer"))
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "validation" in error["message"]


def test_seasonal_integrates_with_the_real_climate_data_module(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end against ``backend/app/climate_data.py`` (no stub)."""

    annual = {year: 5.0 + 0.1 * (year - 1981) for year in range(1981, 1985)}
    calls = _install_power(monkeypatch, "temperature", annual)

    response = client.get(
        "/api/seasonal",
        params=_params(start_year=1981, end_year=1984, period="winter"),
    )
    assert response.status_code == 200
    payload = response.json()

    series = payload["periods"][0]["series"]
    assert series[0]["year"] == 1981
    assert series[0]["value"] is None
    assert series[0]["valid_days"] == 59  # January and February only
    assert series[0]["expected_days"] == 90  # full DJF window
    assert series[1]["value"] is not None
    # 1980 is outside coverage and is never requested.
    assert calls == [_expected_call("temperature", 1981, 1984)]


# ---------------------------------------------------------------------------
# Response-model invariants
# ---------------------------------------------------------------------------


def _source_info(variable: str = "temperature") -> SourceInfo:
    return SourceInfo.model_validate(_source(variable))


def test_anomaly_response_rejects_anomaly_without_baseline_mean() -> None:
    with pytest.raises(ValidationError, match="anomaly must be present"):
        AnomalyResponse(
            latitude=0.0,
            longitude=0.0,
            variable="temperature",
            start_year=1990,
            end_year=1990,
            baseline_start_year=1991,
            baseline_end_year=1995,
            unit="°C",
            aggregation="mean",
            baseline_mean=None,
            baseline_complete_years=4,
            series=[
                AnomalyPoint(
                    year=1990,
                    value=1.0,
                    anomaly=0.5,
                    valid_days=365,
                    expected_days=365,
                )
            ],
            source=_source_info(),
            warnings=[],
        )


def test_anomaly_response_rejects_mean_when_baseline_is_incomplete() -> None:
    with pytest.raises(ValidationError, match="every baseline year to be complete"):
        AnomalyResponse(
            latitude=0.0,
            longitude=0.0,
            variable="temperature",
            start_year=1990,
            end_year=1990,
            baseline_start_year=1991,
            baseline_end_year=1995,
            unit="°C",
            aggregation="mean",
            baseline_mean=1.0,
            baseline_complete_years=4,
            series=[
                AnomalyPoint(
                    year=1990,
                    value=1.0,
                    anomaly=0.0,
                    valid_days=365,
                    expected_days=365,
                )
            ],
            source=_source_info(),
            warnings=[],
        )


def _period_entry(period: str, year: int = 1990, value: float | None = 1.0):
    return SeasonalPeriodEntry(
        period=period,
        series=[
            SeriesPoint(
                year=year,
                value=value,
                valid_days=90,
                expected_days=90,
                fitted=None,
            )
        ],
        trend=None,
    )


def test_seasonal_response_rejects_wrong_period_expansion() -> None:
    with pytest.raises(ValidationError, match="periods must match the requested period"):
        SeasonalResponse(
            latitude=0.0,
            longitude=0.0,
            variable="temperature",
            start_year=1990,
            end_year=1990,
            period="all",
            unit="°C",
            aggregation="mean",
            periods=[_period_entry("winter")],
            source=_source_info(),
            warnings=[],
        )


def test_seasonal_response_rejects_series_that_skips_a_year() -> None:
    with pytest.raises(ValidationError, match="series must contain every requested year"):
        SeasonalResponse(
            latitude=0.0,
            longitude=0.0,
            variable="temperature",
            start_year=1990,
            end_year=1991,
            period="summer",
            unit="°C",
            aggregation="mean",
            periods=[_period_entry("summer", year=1990)],
            source=_source_info(),
            warnings=[],
        )


def test_trend_stats_cannot_be_attached_to_a_single_complete_year() -> None:
    with pytest.raises(ValidationError, match="fitted values require a trend"):
        SeasonalResponse(
            latitude=0.0,
            longitude=0.0,
            variable="temperature",
            start_year=1990,
            end_year=1990,
            period="summer",
            unit="°C",
            aggregation="mean",
            periods=[
                SeasonalPeriodEntry(
                    period="summer",
                    series=[
                        SeriesPoint(
                            year=1990,
                            value=1.0,
                            valid_days=92,
                            expected_days=92,
                            fitted=1.0,
                        )
                    ],
                    trend=None,
                )
            ],
            source=_source_info(),
            warnings=[],
        )


def test_trend_stats_must_match_complete_years() -> None:
    stats = TrendStats(
        years_used=5,
        per_decade=0.1,
        ci95_low=0.0,
        ci95_high=0.2,
        p_value=0.5,
        significant=False,
        r_squared=0.1,
    )
    with pytest.raises(ValidationError, match="years_used"):
        SeasonalResponse(
            latitude=0.0,
            longitude=0.0,
            variable="temperature",
            start_year=1990,
            end_year=1995,
            period="summer",
            unit="°C",
            aggregation="mean",
            periods=[
                SeasonalPeriodEntry(
                    period="summer",
                    series=[
                        SeriesPoint(
                            year=year,
                            value=1.0,
                            valid_days=92,
                            expected_days=92,
                            fitted=1.0,
                        )
                        for year in range(1990, 1996)
                    ],
                    trend=stats,
                )
            ],
            source=_source_info(),
            warnings=[],
        )
