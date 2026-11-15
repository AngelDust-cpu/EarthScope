"""Deterministic contract tests for ``GET /api/extremes``.

Every daily series in this module is a synthetic fixture.  ``app.main.load_daily``
is monkeypatched before any request can run, so no test contacts NASA POWER and
no synthetic data enters the runtime ingestion path.

``backend/app/extremes_stats.py`` is owned by Codex.  The tests substitute the
agreed ``fit_extremes(aggregates) -> (trend, fitted_by_year)`` contract with
small deterministic doubles, so the aggregation, response and error-envelope
behaviour under test never depends on an unfinished statistics module.
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
from app import extremes
from app.power import PowerError
from app.schemas import (
    EXTREME_EVENT_OPERATORS,
    EXTREME_EVENT_UNITS,
    MIN_TREND_YEARS,
    ExtremeSeriesPoint,
    ExtremeTrend,
    ExtremesResponse,
    SourceInfo,
)

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URL = "https://example.invalid/earthscope-extremes-synthetic-test"
RETRIEVED_AT = "2026-11-14T12:34:56Z"

RESPONSE_KEYS = {
    "latitude",
    "longitude",
    "start_year",
    "end_year",
    "event",
    "threshold",
    "threshold_unit",
    "operator",
    "source",
    "warnings",
    "series",
    "trend",
}
SERIES_KEYS = {
    "year",
    "events",
    "fraction",
    "valid_days",
    "expected_days",
    "excluded_days",
    "fitted",
}
TREND_KEYS = {
    "years_used",
    "per_decade",
    "count_per_decade",
    "ci95_low",
    "ci95_high",
    "p_value",
    "significant",
    "residual_lag1",
    "sensitivity",
}
SENSITIVITY_KEYS = {"method", "lags", "ci95_low", "ci95_high", "p_value"}
SOURCE_KEYS = {
    "name",
    "parameter",
    "daily_unit",
    "dataset",
    "time_standard",
    "request_url",
    "retrieved_at",
    "cached",
}

PARAMETER_BY_EVENT = {
    "hot_days": "T2M_MAX",
    "cold_nights": "T2M_MIN",
    "heavy_rain": "PRECTOTCORR",
}
VARIABLE_BY_EVENT = {
    "hot_days": "temperature_max",
    "cold_nights": "temperature_min",
    "heavy_rain": "precipitation",
}
DAILY_UNIT_BY_EVENT = {
    "hot_days": "C",
    "cold_nights": "C",
    "heavy_rain": "mm/day",
}


# ---------------------------------------------------------------------------
# Synthetic fixtures and deterministic fit doubles
# ---------------------------------------------------------------------------


def _source(event: str, cached: bool = False) -> dict[str, object]:
    return {
        "name": "NASA POWER",
        "parameter": PARAMETER_BY_EVENT[event],
        "daily_unit": DAILY_UNIT_BY_EVENT[event],
        "dataset": SYNTHETIC_DATASET,
        "time_standard": "UTC",
        "request_url": SYNTHETIC_URL,
        "retrieved_at": RETRIEVED_AT,
        "cached": cached,
    }


def _year_dates(year: int) -> Iterator[date]:
    current = date(year, 1, 1)
    while current.year == year:
        yield current
        current += timedelta(days=1)


def _key(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}{day.day:02d}"


def _event_value(event: str, threshold: float, is_event: bool) -> float:
    if not is_event:
        # Equality is never counted; keeping these days exactly at the
        # threshold exercises that rule and keeps precipitation non-negative.
        return threshold
    return (
        threshold + 1.0
        if EXTREME_EVENT_OPERATORS[event] == ">"
        else threshold - 1.0
    )


def _build_daily(
    event: str,
    counts: Mapping[int, int],
    threshold: float,
    *,
    missing: Sequence[str] = (),
) -> dict[str, float]:
    """Build a complete daily series with exactly ``counts[year]`` events."""

    missing_keys = set(missing)
    daily: dict[str, float] = {}
    for year, count in counts.items():
        days = list(_year_dates(year))
        if not 0 <= count <= len(days):
            raise ValueError("count must fit within the calendar year")
        for index, day in enumerate(days):
            key = _key(day)
            if key in missing_keys:
                continue
            daily[key] = _event_value(event, threshold, index < count)
    return daily


def _custom_daily(
    year: int, overrides: Mapping[str, float], *, default: float
) -> dict[str, float]:
    daily = {_key(day): default for day in _year_dates(year)}
    daily.update(overrides)
    return daily


def _ols(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0.0:
        raise ValueError("years must contain at least two distinct values")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope /= denominator
    return slope, mean_y - slope * mean_x


def _descriptive_fit(
    aggregates: Sequence[Any],
) -> tuple[dict[str, Any] | None, dict[int, float]]:
    complete = [item for item in aggregates if item.value is not None]
    if len(complete) < MIN_TREND_YEARS:
        return None, {}

    years = [float(item.year) for item in complete]
    rates = [365.0 * float(item.value) / float(item.valid_days) for item in complete]
    counts = [float(item.value) for item in complete]
    rate_slope, rate_intercept = _ols(years, rates)
    count_slope, _ = _ols(years, counts)
    fitted = {
        item.year: rate_intercept + rate_slope * float(item.year)
        for item in complete
    }
    return (
        {
            "years_used": len(complete),
            "per_decade": rate_slope * 10.0,
            "count_per_decade": count_slope * 10.0,
            "ci95_low": None,
            "ci95_high": None,
            "p_value": None,
            "significant": None,
            "residual_lag1": None,
            "sensitivity": [],
        },
        fitted,
    )


class RecordingFit:
    """Deterministic descriptive fit that records every aggregate list."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def __call__(
        self, aggregates: Sequence[Any]
    ) -> tuple[dict[str, Any] | None, dict[int, float]]:
        self.calls.append(list(aggregates))
        return _descriptive_fit(aggregates)


class InferenceFit(RecordingFit):
    """Descriptive fit plus a deliberate, fully populated inference block."""

    def __call__(
        self, aggregates: Sequence[Any]
    ) -> tuple[dict[str, Any] | None, dict[int, float]]:
        trend, fitted = super().__call__(aggregates)
        if trend is None:
            return None, {}
        trend.update(
            {
                "ci95_low": 0.5,
                "ci95_high": 1.5,
                "p_value": 0.01,
                "significant": True,
                "residual_lag1": 0.1,
                "sensitivity": [
                    {
                        "method": "ols",
                        "lags": None,
                        "ci95_low": 0.5,
                        "ci95_high": 1.5,
                        "p_value": 0.01,
                    },
                    {
                        "method": "hac",
                        "lags": 1,
                        "ci95_low": 0.4,
                        "ci95_high": 1.6,
                        "p_value": 0.02,
                    },
                    {
                        "method": "hac",
                        "lags": 3,
                        "ci95_low": 0.3,
                        "ci95_high": 1.7,
                        "p_value": 0.03,
                    },
                    {
                        "method": "hac",
                        "lags": 5,
                        "ci95_low": 0.2,
                        "ci95_high": 1.8,
                        "p_value": 0.04,
                    },
                ],
            }
        )
        return trend, fitted


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any accidental upstream call instead of contacting NASA."""

    def forbidden(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise AssertionError(f"unexpected load_daily call: {kwargs!r}")

    monkeypatch.setattr(main, "load_daily", forbidden)


@pytest.fixture(autouse=True)
def fit_stub(monkeypatch: pytest.MonkeyPatch) -> RecordingFit:
    """Replace the Codex-owned statistics helper with the agreed contract."""

    stub = RecordingFit()
    monkeypatch.setattr(extremes, "fit_extremes", stub)
    return stub


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def _install_power(
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    counts: Mapping[int, int],
    threshold: float,
    *,
    missing: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    daily = _build_daily(event, counts, threshold, missing=missing)
    payload = dict(source) if source is not None else _source(event)
    calls: list[dict[str, object]] = []

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        return dict(daily), dict(payload)

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    return calls


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "latitude": 42.87,
        "longitude": 74.60,
        "start_year": 1990,
        "end_year": 1994,
        "event": "hot_days",
        "threshold": 30,
    }
    params.update(overrides)
    return params


def _assert_error(response: Any, status_code: int, code: str) -> dict[str, str]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message"}
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    return payload["error"]


# ---------------------------------------------------------------------------
# Year aggregation: strict boundaries, completeness, leap years and zero
# ---------------------------------------------------------------------------


def test_hot_days_counts_only_strictly_above_threshold() -> None:
    daily = _custom_daily(
        1990,
        {"19900101": 29.999, "19900102": 30.0, "19900103": 30.001},
        default=20.0,
    )

    (row,) = extremes.aggregate_extremes(daily, "hot_days", 30.0, 1990, 1990)

    assert row.value == 1.0
    assert row.valid_days == 365
    assert row.expected_days == 365


def test_cold_nights_counts_only_strictly_below_threshold() -> None:
    daily = _custom_daily(
        1990,
        {"19900101": 0.001, "19900102": 0.0, "19900103": -0.001},
        default=5.0,
    )

    (row,) = extremes.aggregate_extremes(daily, "cold_nights", 0.0, 1990, 1990)

    assert row.value == 1.0


def test_heavy_rain_counts_only_strictly_above_threshold() -> None:
    daily = _custom_daily(
        1990,
        {"19900101": 19.999, "19900102": 20.0, "19900103": 20.001},
        default=0.0,
    )

    (row,) = extremes.aggregate_extremes(daily, "heavy_rain", 20.0, 1990, 1990)

    assert row.value == 1.0
    assert row.valid_days == 365


def test_missing_day_makes_the_year_incomplete_not_filled() -> None:
    daily = _build_daily("hot_days", {2020: 10}, 30.0)
    del daily["20200229"]

    (row,) = extremes.aggregate_extremes(daily, "hot_days", 30.0, 2020, 2020)

    assert row.value is None
    assert row.valid_days == 365
    assert row.expected_days == 366


def test_leap_year_expects_366_days_and_nonleap_365() -> None:
    daily = _build_daily("hot_days", {2020: 2, 2021: 3}, 30.0)

    rows = extremes.aggregate_extremes(daily, "hot_days", 30.0, 2020, 2021)

    assert [row.year for row in rows] == [2020, 2021]
    assert [row.expected_days for row in rows] == [366, 365]
    assert [row.valid_days for row in rows] == [366, 365]
    assert [row.value for row in rows] == [2.0, 3.0]


def test_zero_events_in_a_complete_year_is_a_real_zero() -> None:
    daily = _build_daily("hot_days", {1990: 0}, 30.0)

    (row,) = extremes.aggregate_extremes(daily, "hot_days", 30.0, 1990, 1990)

    assert row.value == 0.0
    assert row.valid_days == row.expected_days == 365


def test_negative_precipitation_day_is_invalid_and_not_filled() -> None:
    daily = _build_daily("heavy_rain", {1990: 1}, 20.0)
    daily["19900701"] = -0.1

    (row,) = extremes.aggregate_extremes(daily, "heavy_rain", 20.0, 1990, 1990)

    assert row.value is None
    assert row.valid_days == 364
    assert row.expected_days == 365


def test_duplicate_and_invalid_date_keys_do_not_inflate_the_count() -> None:
    daily = _build_daily("hot_days", {1990: 1}, 30.0)
    daily["19900230"] = 99.0  # impossible calendar date -> ignored
    daily["not-a-date"] = 99.0
    daily["19900701"] = 30.001  # overrides the existing threshold day

    (row,) = extremes.aggregate_extremes(daily, "hot_days", 30.0, 1990, 1990)

    assert row.valid_days == 365
    assert row.value == 2.0


# ---------------------------------------------------------------------------
# HTTP contract and shared ingestion
# ---------------------------------------------------------------------------


def test_hot_days_response_contract_and_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fit_stub: RecordingFit,
) -> None:
    counts = {1990: 0, 1991: 1, 1992: 2, 1993: 3, 1994: 4}
    _install_power(monkeypatch, "hot_days", counts, 30.0)

    response = client.get("/api/extremes", params=_params())

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == RESPONSE_KEYS
    assert set(payload["source"]) == SOURCE_KEYS
    assert payload["event"] == "hot_days"
    assert payload["threshold"] == 30.0
    assert payload["threshold_unit"] == "°C"
    assert payload["operator"] == ">"
    assert payload["source"]["parameter"] == "T2M_MAX"
    assert payload["source"]["daily_unit"] == "C"
    assert payload["source"]["cached"] is False

    assert [point["year"] for point in payload["series"]] == list(range(1990, 1995))
    assert all(set(point) == SERIES_KEYS for point in payload["series"])
    assert [point["events"] for point in payload["series"]] == [0, 1, 2, 3, 4]
    assert payload["series"][0]["fraction"] == 0.0
    assert payload["series"][1]["fraction"] == pytest.approx(1 / 365)
    assert payload["series"][2]["expected_days"] == 366  # 1992 is a leap year

    trend = payload["trend"]
    assert trend is not None
    assert set(trend) == TREND_KEYS
    assert trend["years_used"] == 5
    assert math.isfinite(trend["per_decade"])
    assert math.isfinite(trend["count_per_decade"])
    assert trend["ci95_low"] is None and trend["ci95_high"] is None
    assert trend["p_value"] is None and trend["significant"] is None
    assert trend["residual_lag1"] is None
    assert trend["sensitivity"] == []
    assert all(point["fitted"] is not None for point in payload["series"])

    assert len(fit_stub.calls) == 1
    assert [item.value for item in fit_stub.calls[0]] == [0.0, 1.0, 2.0, 3.0, 4.0]


@pytest.mark.parametrize(
    ("event", "variable", "parameter", "operator", "unit", "threshold"),
    [
        ("hot_days", "temperature_max", "T2M_MAX", ">", "°C", 30.0),
        ("cold_nights", "temperature_min", "T2M_MIN", "<", "°C", 0.0),
        ("heavy_rain", "precipitation", "PRECTOTCORR", ">", "mm/day", 20.0),
    ],
)
def test_shared_ingestion_uses_one_event_variable_and_one_call(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    variable: str,
    parameter: str,
    operator: str,
    unit: str,
    threshold: float,
) -> None:
    counts = {1990: 1, 1991: 2, 1992: 0, 1993: 3, 1994: 4}
    calls = _install_power(monkeypatch, event, counts, threshold)

    response = client.get(
        "/api/extremes",
        params=_params(event=event, threshold=threshold),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator"] == operator
    assert payload["threshold_unit"] == unit
    assert payload["source"]["parameter"] == parameter
    assert payload["source"]["daily_unit"] == DAILY_UNIT_BY_EVENT[event]

    assert calls == [
        {
            "latitude": 42.87,
            "longitude": 74.60,
            "variable": variable,
            "start_year": 1990,
            "end_year": 1994,
        }
    ]


@pytest.mark.parametrize(
    ("event", "threshold"),
    [
        ("hot_days", -100.0),
        ("hot_days", 100.0),
        ("cold_nights", -100.0),
        ("cold_nights", 100.0),
        ("heavy_rain", 0.0),
        ("heavy_rain", 2000.0),
    ],
)
def test_event_specific_threshold_bounds_are_inclusive(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    threshold: float,
) -> None:
    _install_power(monkeypatch, event, {1990: 0}, threshold)

    response = client.get(
        "/api/extremes",
        params=_params(event=event, threshold=threshold),
    )

    assert response.status_code == 200
    assert response.json()["threshold"] == threshold


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "tornado"},
        {"threshold": "nan"},
        {"threshold": "inf"},
        {"event": "hot_days", "threshold": -100.5},
        {"event": "hot_days", "threshold": 100.5},
        {"event": "cold_nights", "threshold": -100.5},
        {"event": "heavy_rain", "threshold": -0.1},
        {"event": "heavy_rain", "threshold": 2000.1},
        {"start_year": 1980},
        {"end_year": 2026},
        {"start_year": 1995, "end_year": 1994},
        {"latitude": 90.1},
        {"longitude": -180.1},
    ],
)
def test_invalid_requests_return_the_error_envelope_before_ingestion(
    client: TestClient,
    overrides: dict[str, object],
) -> None:
    response = client.get("/api/extremes", params=_params(**overrides))

    _assert_error(response, 422, "INVALID_REQUEST")


def test_incomplete_year_keeps_diagnostics_and_has_no_fitted_value(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fit_stub: RecordingFit,
) -> None:
    counts = {year: 2 for year in range(1990, 1996)}
    _install_power(monkeypatch, "hot_days", counts, 30.0, missing=("19930815",))

    response = client.get(
        "/api/extremes",
        params=_params(start_year=1990, end_year=1995),
    )

    assert response.status_code == 200
    payload = response.json()
    by_year = {point["year"]: point for point in payload["series"]}
    incomplete = by_year[1993]
    assert incomplete["events"] is None
    assert incomplete["fraction"] is None
    assert incomplete["fitted"] is None
    assert incomplete["valid_days"] == 364
    assert incomplete["expected_days"] == 365
    assert incomplete["excluded_days"] == 1
    assert all(
        by_year[year]["fitted"] is not None for year in (1990, 1991, 1992, 1994, 1995)
    )
    assert payload["trend"]["years_used"] == 5

    assert len(fit_stub.calls) == 1
    aggregates = {item.year: item for item in fit_stub.calls[0]}
    assert aggregates[1993].value is None
    assert aggregates[1993].valid_days == 364
    assert aggregates[1993].expected_days == 365


def test_fewer_than_five_complete_years_has_no_trend(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {1990: 1, 1991: 2, 1992: 3, 1993: 4}
    _install_power(monkeypatch, "hot_days", counts, 30.0)

    response = client.get(
        "/api/extremes",
        params=_params(start_year=1990, end_year=1993),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trend"] is None
    assert all(point["fitted"] is None for point in payload["series"])
    assert any("at least 5" in warning for warning in payload["warnings"])


def test_zero_event_year_is_not_dropped_from_the_fit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {1990: 0, 1991: 0, 1992: 3, 1993: 2, 1994: 1}
    _install_power(monkeypatch, "hot_days", counts, 30.0)

    response = client.get("/api/extremes", params=_params())

    assert response.status_code == 200
    payload = response.json()
    first = payload["series"][0]
    assert first["year"] == 1990
    assert first["events"] == 0
    assert first["fraction"] == 0.0
    assert first["fitted"] is not None
    assert payload["trend"]["years_used"] == 5


def test_inference_fields_and_sensitivity_are_passed_through(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extremes, "fit_extremes", InferenceFit())
    counts = {year: (year - 1990) % 4 for year in range(1990, 2010)}
    _install_power(monkeypatch, "hot_days", counts, 30.0)

    response = client.get(
        "/api/extremes",
        params=_params(start_year=1990, end_year=2009),
    )

    assert response.status_code == 200
    trend = response.json()["trend"]
    assert trend["years_used"] == 20
    assert trend["ci95_low"] == 0.5
    assert trend["ci95_high"] == 1.5
    assert trend["p_value"] == 0.01
    assert trend["significant"] is True
    assert trend["residual_lag1"] == 0.1
    assert all(set(item) == SENSITIVITY_KEYS for item in trend["sensitivity"])
    assert {(item["method"], item["lags"]) for item in trend["sensitivity"]} == {
        ("ols", None),
        ("hac", 1),
        ("hac", 3),
        ("hac", 5),
    }


# ---------------------------------------------------------------------------
# Error mapping and data-contract guards
# ---------------------------------------------------------------------------


def test_power_failure_uses_the_existing_error_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("NASA POWER is temporarily unavailable.")

    monkeypatch.setattr(main, "load_daily", fail)

    response = client.get("/api/extremes", params=_params())

    _assert_error(response, 502, "POWER_UNAVAILABLE")


def test_source_parameter_mismatch_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_power(
        monkeypatch,
        "hot_days",
        {year: 1 for year in range(1990, 1995)},
        30.0,
        source=_source("cold_nights"),
    )

    response = client.get("/api/extremes", params=_params())

    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "source.parameter" in error["message"]


def test_fitted_values_without_a_trend_are_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_fit(
        aggregates: Sequence[Any],
    ) -> tuple[dict[str, Any] | None, dict[int, float]]:
        return None, {
            item.year: 1.0 for item in aggregates if item.value is not None
        }

    monkeypatch.setattr(extremes, "fit_extremes", bad_fit)
    _install_power(
        monkeypatch,
        "hot_days",
        {year: 1 for year in range(1990, 1995)},
        30.0,
    )

    response = client.get("/api/extremes", params=_params())

    _assert_error(response, 502, "POWER_UNAVAILABLE")


def test_missing_fitted_year_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_fit(
        aggregates: Sequence[Any],
    ) -> tuple[dict[str, Any] | None, dict[int, float]]:
        trend, fitted = _descriptive_fit(aggregates)
        fitted.pop(min(fitted))
        return trend, fitted

    monkeypatch.setattr(extremes, "fit_extremes", bad_fit)
    _install_power(
        monkeypatch,
        "hot_days",
        {year: 1 for year in range(1990, 1995)},
        30.0,
    )

    response = client.get("/api/extremes", params=_params())

    _assert_error(response, 502, "POWER_UNAVAILABLE")


# ---------------------------------------------------------------------------
# Response-model invariants (defence in depth at the HTTP boundary)
# ---------------------------------------------------------------------------


def _point(**overrides: object) -> dict[str, object]:
    point: dict[str, object] = {
        "year": 1990,
        "events": 1,
        "fraction": 1 / 365,
        "valid_days": 365,
        "expected_days": 365,
        "excluded_days": 0,
        "fitted": None,
    }
    point.update(overrides)
    return point


@pytest.mark.parametrize(
    "overrides",
    [
        {"year": 1990, "expected_days": 366},
        {"year": 1992, "expected_days": 365, "valid_days": 365, "excluded_days": 1},
        {"excluded_days": 1},
        {"events": None, "fraction": None},
        {"events": 1, "valid_days": 364, "excluded_days": 1},
        {"fraction": 0.5},
        {"events": 400},
    ],
)
def test_series_point_rejects_incoherent_counts(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExtremeSeriesPoint(**_point(**overrides))


def _sensitivity() -> list[dict[str, object]]:
    return [
        {
            "method": "ols",
            "lags": None,
            "ci95_low": 0.5,
            "ci95_high": 1.5,
            "p_value": 0.01,
        },
        {
            "method": "hac",
            "lags": 1,
            "ci95_low": 0.4,
            "ci95_high": 1.6,
            "p_value": 0.02,
        },
        {
            "method": "hac",
            "lags": 3,
            "ci95_low": 0.3,
            "ci95_high": 1.7,
            "p_value": 0.03,
        },
        {
            "method": "hac",
            "lags": 5,
            "ci95_low": 0.2,
            "ci95_high": 1.8,
            "p_value": 0.04,
        },
    ]


def _trend(**overrides: object) -> dict[str, object]:
    trend: dict[str, object] = {
        "years_used": 20,
        "per_decade": 1.0,
        "count_per_decade": 0.5,
        "ci95_low": 0.5,
        "ci95_high": 1.5,
        "p_value": 0.01,
        "significant": True,
        "residual_lag1": 0.1,
        "sensitivity": _sensitivity(),
    }
    trend.update(overrides)
    return trend


def _descriptive_trend(years_used: int = 5) -> dict[str, object]:
    """A valid trend with the fit guards failed, as short fits report."""

    return _trend(
        years_used=years_used,
        ci95_low=None,
        ci95_high=None,
        p_value=None,
        significant=None,
        residual_lag1=None,
        sensitivity=[],
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"p_value": None},
        {"ci95_low": None},
        {"significant": False},
        {"ci95_low": 2.0, "ci95_high": 1.0},
        {"sensitivity": _sensitivity()[:3]},
        {"sensitivity": []},
        {"per_decade": float("nan")},
        {"p_value": 0.5, "significant": True},
    ],
)
def test_trend_rejects_incoherent_inference(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExtremeTrend(**_trend(**overrides))


def _response_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "latitude": 42.87,
        "longitude": 74.6,
        "start_year": 1990,
        "end_year": 1994,
        "event": "hot_days",
        "threshold": 30.0,
        "threshold_unit": "°C",
        "operator": ">",
        "source": _source("hot_days"),
        "warnings": [],
        "series": [_point(year=year) for year in range(1990, 1995)],
        "trend": None,
    }
    payload.update(overrides)
    return payload


def test_response_rejects_null_trend_for_five_complete_years() -> None:
    with pytest.raises(ValidationError):
        ExtremesResponse(**_response_payload())


def test_response_requires_fitted_values_exactly_at_complete_years() -> None:
    series = [_point(year=year) for year in range(1990, 1995)]
    for point in series:
        point["fitted"] = 1.0
    series[2]["fitted"] = None

    with pytest.raises(ValidationError):
        ExtremesResponse(
            **_response_payload(
                series=series,
                trend=_descriptive_trend(5),
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"threshold_unit": "mm/day"},
        {"operator": "<"},
        {"threshold": 100.1},
    ],
)
def test_response_rejects_event_metadata_mismatch(
    overrides: dict[str, object],
) -> None:
    series = [_point(year=year) for year in range(1990, 1995)]
    for point in series:
        point["fitted"] = 1.0
    payload = _response_payload(
        series=series,
        trend=_descriptive_trend(5),
    )
    payload.update(overrides)

    with pytest.raises(ValidationError):
        ExtremesResponse(**payload)


def test_source_info_accepts_extreme_parameters() -> None:
    for event in ("hot_days", "cold_nights", "heavy_rain"):
        info = SourceInfo.model_validate(_source(event))
        assert info.parameter == PARAMETER_BY_EVENT[event]
        assert info.daily_unit == DAILY_UNIT_BY_EVENT[event]
