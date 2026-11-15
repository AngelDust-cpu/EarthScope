"""Contract tests for the EarthScope HTTP API.

All POWER responses in this module are synthetic, deterministic fixtures.
They are injected by monkeypatching ``app.main.load_daily``; no test contacts
NASA POWER and no synthetic data enters the runtime application path.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Iterator, Mapping
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from scipy import stats as scipy_stats

import numpy as np

import app.main as main
from app.power import PowerError
from app.schemas import MIN_TREND_YEARS

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URL = "https://example.invalid/earthscope-synthetic-test"
RETRIEVED_AT = "2026-11-14T12:34:56Z"

TREND_KEYS = {
    "latitude",
    "longitude",
    "variable",
    "start_year",
    "end_year",
    "unit",
    "aggregation",
    "series",
    "trend",
    "source",
    "warnings",
}
SERIES_KEYS = {"year", "value", "valid_days", "expected_days", "fitted"}
STAT_KEYS = {
    "years_used",
    "per_decade",
    "ci95_low",
    "ci95_high",
    "p_value",
    "significant",
    "r_squared",
}
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
COMPARISON_KEYS = {
    "method",
    "years_used",
    "slope_difference_per_decade",
    "ci95_low",
    "ci95_high",
    "p_value",
    "inferential",
    "note",
}

POINT_A = {"latitude": 42.87, "longitude": 74.60}
POINT_B = {"latitude": 64.147, "longitude": -21.942}
SOURCE_URL_A = "https://example.invalid/earthscope-synthetic-test-a"
SOURCE_URL_B = "https://example.invalid/earthscope-synthetic-test-b"


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
    missing: tuple[str, ...] = (),
    overrides: Mapping[str, float] | None = None,
) -> dict[str, float]:
    missing_keys = set(missing)
    daily: dict[str, float] = {}
    for year, annual_target in annual_values.items():
        days = list(_year_dates(year))
        daily_value = (
            annual_target
            if variable == "temperature"
            else annual_target / len(days)
        )
        for day in days:
            key = f"{year:04d}{day.month:02d}{day.day:02d}"
            if key not in missing_keys:
                daily[key] = daily_value
    if overrides:
        daily.update(overrides)
    return daily


def _install_power(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    annual_values: Mapping[int, float],
    *,
    missing: tuple[str, ...] = (),
    overrides: Mapping[str, float] | None = None,
    source: dict[str, object] | None = None,
    cached: bool = False,
) -> list[dict[str, object]]:
    daily = _build_daily(
        variable, annual_values, missing=missing, overrides=overrides
    )
    source_payload = source if source is not None else _source(variable, cached)
    calls: list[dict[str, object]] = []

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        return daily, source_payload

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    return calls


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


def _assert_error(
    response: object, status_code: int, code: str
) -> dict[str, str]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message"}
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    return payload["error"]


def _compare_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "point_a": dict(POINT_A),
        "point_b": dict(POINT_B),
        "variable": "temperature",
        "start_year": 1990,
        "end_year": 1994,
    }
    body.update(overrides)
    return body


def _expected_call(
    point: Mapping[str, float], variable: str, start_year: int, end_year: int
) -> dict[str, object]:
    return {
        "latitude": point["latitude"],
        "longitude": point["longitude"],
        "variable": variable,
        "start_year": start_year,
        "end_year": end_year,
    }


def _install_compare_power(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    annual_a: Mapping[int, float],
    annual_b: Mapping[int, float],
    *,
    missing_a: tuple[str, ...] = (),
    missing_b: tuple[str, ...] = (),
    cached_a: bool = False,
    cached_b: bool = False,
) -> list[dict[str, object]]:
    """Serve distinct synthetic series per point and record every load call."""

    daily_a = _build_daily(variable, annual_a, missing=missing_a)
    daily_b = _build_daily(variable, annual_b, missing=missing_b)
    source_a = _source(variable, cached_a) | {"request_url": SOURCE_URL_A}
    source_b = _source(variable, cached_b) | {"request_url": SOURCE_URL_B}
    calls: list[dict[str, object]] = []

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        point = {"latitude": kwargs.get("latitude"), "longitude": kwargs.get("longitude")}
        if point == POINT_A:
            return daily_a, source_a
        if point == POINT_B:
            return daily_b, source_b
        raise AssertionError(f"unexpected comparison point: {point!r}")

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    return calls


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_coverage(client: TestClient) -> None:
    response = client.get("/api/coverage")
    assert response.status_code == 200
    assert response.json() == {
        "min_year": 1981,
        "max_year": 2025,
        "min_years": MIN_TREND_YEARS,
    }


def test_temperature_trend_is_linear_and_exact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(year - 1990 + 10) for year in range(1990, 1995)}
    calls = _install_power(monkeypatch, "temperature", annual)

    response = client.get("/api/trend", params=_params())
    assert response.status_code == 200
    payload = response.json()

    assert set(payload) == TREND_KEYS
    assert set(payload["source"]) == SOURCE_KEYS
    assert set(payload["trend"]) == STAT_KEYS
    assert all(set(point) == SERIES_KEYS for point in payload["series"])

    assert payload["variable"] == "temperature"
    assert payload["unit"] == "°C"
    assert payload["aggregation"] == "mean"
    assert [point["year"] for point in payload["series"]] == [1990, 1991, 1992, 1993, 1994]

    for point, (year, expected) in zip(
        payload["series"], annual.items(), strict=True
    ):
        expected_days = _days_in_year(year)
        assert point["value"] == pytest.approx(expected)
        assert point["fitted"] == pytest.approx(expected)
        assert point["valid_days"] == expected_days
        assert point["expected_days"] == expected_days

    trend = payload["trend"]
    assert trend["years_used"] == 5
    assert trend["per_decade"] == pytest.approx(10.0)
    assert trend["ci95_low"] == pytest.approx(10.0)
    assert trend["ci95_high"] == pytest.approx(10.0)
    assert trend["p_value"] == 0.0
    assert trend["significant"] is True
    assert trend["r_squared"] == pytest.approx(1.0)
    assert payload["warnings"] == []

    assert payload["source"]["parameter"] == "T2M"
    assert payload["source"]["daily_unit"] == "C"
    assert payload["source"]["retrieved_at"] == RETRIEVED_AT
    assert payload["source"]["cached"] is False

    assert calls == [
        {
            "latitude": 42.87,
            "longitude": 74.60,
            "variable": "temperature",
            "start_year": 1990,
            "end_year": 1994,
        }
    ]


def test_precipitation_uses_annual_sums(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(100 + 10 * (year - 1990)) for year in range(1990, 1996)}
    _install_power(monkeypatch, "precipitation", annual)

    response = client.get(
        "/api/trend",
        params=_params(variable="precipitation", end_year=1995),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["unit"] == "mm"
    assert payload["aggregation"] == "sum"
    assert payload["source"]["parameter"] == "PRECTOTCORR"
    assert payload["source"]["daily_unit"] == "mm/day"
    for point in payload["series"]:
        assert point["value"] == pytest.approx(annual[point["year"]], abs=1e-9)
        assert point["valid_days"] == point["expected_days"]

    assert payload["trend"]["per_decade"] == pytest.approx(100.0)
    assert payload["trend"]["significant"] is True
    assert payload["trend"]["p_value"] == 0.0


def test_student_t_statistics_match_reference_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = [1.0, 2.0, 2.0, 4.0, 5.0]
    annual = dict(zip(range(1990, 1995), values, strict=True))
    _install_power(monkeypatch, "temperature", annual)

    response = client.get("/api/trend", params=_params())
    assert response.status_code == 200
    trend = response.json()["trend"]

    # Independent closed-form values for this deterministic five-point series.
    residual_variance = 0.8 / 3.0
    standard_error = math.sqrt(residual_variance / 10.0)
    t_critical = float(scipy_stats.t.ppf(0.975, 3))
    expected_p = float(2.0 * scipy_stats.t.sf(1.0 / standard_error, 3))

    assert trend["per_decade"] == pytest.approx(10.0)
    assert trend["ci95_low"] == pytest.approx(
        (1.0 - t_critical * standard_error) * 10.0
    )
    assert trend["ci95_high"] == pytest.approx(
        (1.0 + t_critical * standard_error) * 10.0
    )
    assert trend["p_value"] == pytest.approx(expected_p)
    assert trend["significant"] is (expected_p < 0.05)
    assert trend["r_squared"] == pytest.approx(1.0 - 0.8 / 10.8)


def test_constant_series_reports_zero_slope_and_p_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 7.5 for year in range(1990, 1995)}
    _install_power(monkeypatch, "temperature", annual)

    response = client.get("/api/trend", params=_params())
    assert response.status_code == 200
    payload = response.json()
    assert payload["trend"] == {
        "years_used": 5,
        "per_decade": 0.0,
        "ci95_low": 0.0,
        "ci95_high": 0.0,
        "p_value": 1.0,
        "significant": False,
        "r_squared": 0.0,
    }
    assert all(point["fitted"] == pytest.approx(7.5) for point in payload["series"])


def test_incomplete_year_is_null_and_excluded_from_fit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(year - 1990) for year in range(1990, 1996)}
    _install_power(
        monkeypatch,
        "temperature",
        annual,
        missing=("19930615",),
    )

    response = client.get("/api/trend", params=_params(end_year=1995))
    assert response.status_code == 200
    payload = response.json()

    assert [point["year"] for point in payload["series"]] == list(range(1990, 1996))
    incomplete = payload["series"][3]
    assert incomplete["year"] == 1993
    assert incomplete["value"] is None
    assert incomplete["fitted"] is None
    assert incomplete["valid_days"] == 364
    assert incomplete["expected_days"] == 365

    assert payload["trend"]["years_used"] == 5
    assert payload["trend"]["per_decade"] == pytest.approx(10.0)
    assert any("1993" in warning for warning in payload["warnings"])
    assert all(
        point["fitted"] is not None
        for point in payload["series"]
        if point["year"] != 1993
    )


def test_insufficient_complete_years_returns_null_trend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {1990: 1.0, 1991: 2.0}
    _install_power(monkeypatch, "temperature", annual)

    response = client.get("/api/trend", params=_params(end_year=1993))
    assert response.status_code == 200
    payload = response.json()

    assert payload["trend"] is None
    assert [point["year"] for point in payload["series"]] == [1990, 1991, 1992, 1993]
    assert all(point["fitted"] is None for point in payload["series"])
    assert any(
        f"at least {MIN_TREND_YEARS}" in warning for warning in payload["warnings"]
    )
    assert any("only 2" in warning for warning in payload["warnings"])


def test_requested_years_are_ascending_and_cover_supplied_range(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {
        1989: 1000.0,
        1990: 10.0,
        1991: 11.0,
        1992: 12.0,
        1993: 13.0,
        1994: 14.0,
        1995: 15.0,
        1996: 1000.0,
    }
    _install_power(monkeypatch, "temperature", annual)

    response = client.get("/api/trend", params=_params())
    assert response.status_code == 200
    payload = response.json()
    assert [point["year"] for point in payload["series"]] == [1990, 1991, 1992, 1993, 1994]
    assert payload["trend"]["per_decade"] == pytest.approx(10.0)


def test_negative_precipitation_day_invalidates_the_year(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {1990: 100.0}
    _install_power(
        monkeypatch,
        "precipitation",
        annual,
        overrides={"19900301": -1.0},
    )

    response = client.get(
        "/api/trend",
        params=_params(variable="precipitation", end_year=1990),
    )
    assert response.status_code == 200
    point = response.json()["series"][0]
    assert point["value"] is None
    assert point["valid_days"] == 364
    assert point["expected_days"] == 365
    assert response.json()["trend"] is None


def test_nonfinite_daily_values_are_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {1990: 10.0}
    _install_power(
        monkeypatch,
        "temperature",
        annual,
        overrides={"19900701": float("nan"), "19900702": float("inf")},
    )

    response = client.get("/api/trend", params=_params(end_year=1990))
    assert response.status_code == 200
    point = response.json()["series"][0]
    assert point["value"] is None
    assert point["valid_days"] == 363


def test_source_metadata_and_cache_status_are_passed_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 + year for year in range(1990, 1995)},
        cached=True,
    )
    response = client.get("/api/trend", params=_params())
    assert response.status_code == 200
    source = response.json()["source"]
    assert source["name"] == "NASA POWER"
    assert source["dataset"] == SYNTHETIC_DATASET
    assert source["request_url"] == SYNTHETIC_URL
    assert source["time_standard"] == "UTC"
    assert source["cached"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": 90.0001},
        {"latitude": -90.0001},
        {"latitude": "nan"},
        {"latitude": "inf"},
        {"longitude": 180.0001},
        {"longitude": -180.0001},
        {"longitude": "nan"},
        {"start_year": 1980},
        {"end_year": 2026},
        {"start_year": 2001, "end_year": 2000},
        {"variable": "humidity"},
        {"start_year": "not-a-year"},
    ],
)
def test_invalid_inputs_are_rejected_before_power(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    calls = _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 for year in range(1990, 1995)},
    )
    response = client.get("/api/trend", params=_params(**overrides))
    _assert_error(response, 422, "INVALID_REQUEST")
    assert calls == []


def test_missing_query_parameter_uses_error_envelope(client: TestClient) -> None:
    params = _params()
    params.pop("latitude")
    response = client.get("/api/trend", params=params)
    error = _assert_error(response, 422, "INVALID_REQUEST")
    assert "latitude" in error["message"]


@pytest.mark.parametrize(
    ("method", "path", "status_code", "code"),
    [
        ("get", "/api/not-a-route", 404, "NOT_FOUND"),
        ("post", "/api/health", 405, "METHOD_NOT_ALLOWED"),
    ],
)
def test_http_errors_use_the_error_envelope(
    client: TestClient,
    method: str,
    path: str,
    status_code: int,
    code: str,
) -> None:
    response = client.request(method, path)
    _assert_error(response, status_code, code)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(90.0, 180.0), (-90.0, -180.0), (0.0, 0.0)],
)
def test_coordinate_boundaries_are_accepted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    latitude: float,
    longitude: float,
) -> None:
    _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 for year in range(1990, 1995)},
    )
    response = client.get(
        "/api/trend",
        params=_params(latitude=latitude, longitude=longitude),
    )
    assert response.status_code == 200
    assert response.json()["latitude"] == latitude
    assert response.json()["longitude"] == longitude


def test_power_error_maps_to_502_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_load_daily(**_kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("synthetic upstream outage")

    monkeypatch.setattr(main, "load_daily", failing_load_daily)
    response = client.get("/api/trend", params=_params())
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "synthetic upstream outage" in error["message"]


def test_power_invalid_request_error_maps_to_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_load_daily(**_kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("synthetic boundary rejection", code="INVALID_REQUEST")

    monkeypatch.setattr(main, "load_daily", failing_load_daily)
    response = client.get("/api/trend", params=_params())
    error = _assert_error(response, 422, "INVALID_REQUEST")
    assert "synthetic boundary rejection" in error["message"]


def test_malformed_source_metadata_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_source = _source("temperature")
    bad_source["daily_unit"] = "mm/day"  # deliberately wrong for T2M
    _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 for year in range(1990, 1995)},
        source=bad_source,
    )
    response = client.get("/api/trend", params=_params())
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "validation" in error["message"]


def test_missing_source_field_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_source = _source("temperature")
    bad_source.pop("dataset")
    _install_power(
        monkeypatch,
        "temperature",
        {year: 1.0 for year in range(1990, 1995)},
        source=bad_source,
    )
    response = client.get("/api/trend", params=_params())
    _assert_error(response, 502, "POWER_UNAVAILABLE")


def test_cors_allows_vite_dev_origins(client: TestClient) -> None:
    for origin in ("http://localhost:5173", "http://127.0.0.1:5199"):
        preflight = client.options(
            "/api/trend",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin

        health = client.get("/api/health", headers={"Origin": origin})
        assert health.headers["access-control-allow-origin"] == origin

    denied = client.get(
        "/api/health", headers={"Origin": "https://not-allowed.example"}
    )
    assert "access-control-allow-origin" not in denied.headers


def test_cors_env_origin_list_supports_static_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "EARTHSCOPE_ALLOWED_ORIGINS",
        "https://earthscope.example/ , https://www.earthscope.example:443",
    )
    application = main.create_app()
    with TestClient(application) as env_client:
        for origin in (
            "https://earthscope.example",
            "https://www.earthscope.example",
        ):
            response = env_client.options(
                "/api/trend",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin

        denied = env_client.get(
            "/api/health", headers={"Origin": "https://other.example"}
        )
        assert "access-control-allow-origin" not in denied.headers


def test_invalid_cors_origin_configuration_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "EARTHSCOPE_ALLOWED_ORIGINS", "https://earthscope.example/static/app"
    )
    with pytest.raises(RuntimeError, match="Invalid CORS origin"):
        main.create_app()


def test_compare_returns_both_trends_and_descriptive_difference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual_a = {year: float(year - 1990 + 10) for year in range(1990, 1995)}
    annual_b = {year: 10.0 + 0.5 * (year - 1990) for year in range(1990, 1995)}
    calls = _install_compare_power(monkeypatch, "temperature", annual_a, annual_b)

    response = client.post("/api/compare", json=_compare_body())
    assert response.status_code == 200
    payload = response.json()

    assert set(payload) == {"point_a", "point_b", "comparison"}
    assert set(payload["comparison"]) == COMPARISON_KEYS
    for name, annual in (("point_a", annual_a), ("point_b", annual_b)):
        point = payload[name]
        assert set(point) == TREND_KEYS
        assert set(point["source"]) == SOURCE_KEYS
        assert set(point["trend"]) == STAT_KEYS
        assert point["variable"] == "temperature"
        assert point["unit"] == "°C"
        assert [item["year"] for item in point["series"]] == list(range(1990, 1995))
        for item in point["series"]:
            assert item["value"] == pytest.approx(annual[item["year"]])
        # Each point keeps its own complete-year trend fit.
        assert point["trend"]["years_used"] == 5

    assert payload["point_a"]["latitude"] == POINT_A["latitude"]
    assert payload["point_a"]["longitude"] == POINT_A["longitude"]
    assert payload["point_b"]["latitude"] == POINT_B["latitude"]
    assert payload["point_b"]["longitude"] == POINT_B["longitude"]
    assert payload["point_a"]["trend"]["per_decade"] == pytest.approx(10.0)
    assert payload["point_b"]["trend"]["per_decade"] == pytest.approx(5.0)

    comparison = payload["comparison"]
    assert comparison["method"] == "paired_annual_difference_descriptive"
    assert comparison["years_used"] == 5
    assert comparison["slope_difference_per_decade"] == pytest.approx(5.0)
    assert comparison["ci95_low"] is None
    assert comparison["ci95_high"] is None
    assert comparison["p_value"] is None
    assert comparison["inferential"] is False
    assert "Descriptive" in comparison["note"]
    assert "p-value" in comparison["note"]
    assert "significance claim" in comparison["note"]

    assert calls == [
        _expected_call(POINT_A, "temperature", 1990, 1994),
        _expected_call(POINT_B, "temperature", 1990, 1994),
    ]


def test_compare_difference_slope_matches_independent_least_squares(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    values_a = [1.0, 3.5, 2.0, 6.0, 4.5, 8.0]
    values_b = [0.5, 2.0, 3.0, 3.0, 5.5, 5.0]
    years = list(range(1990, 1996))
    annual_a = dict(zip(years, values_a, strict=True))
    annual_b = dict(zip(years, values_b, strict=True))
    _install_compare_power(monkeypatch, "temperature", annual_a, annual_b)

    response = client.post("/api/compare", json=_compare_body(end_year=1995))
    assert response.status_code == 200
    comparison = response.json()["comparison"]

    differences = np.asarray(values_a) - np.asarray(values_b)
    expected_slope = float(
        np.polyfit(np.asarray(years, dtype=float), differences, 1)[0] * 10.0
    )
    assert comparison["years_used"] == 6
    assert comparison["slope_difference_per_decade"] == pytest.approx(expected_slope)
    assert comparison["ci95_low"] is None
    assert comparison["ci95_high"] is None
    assert comparison["p_value"] is None
    assert comparison["inferential"] is False


def test_compare_excludes_differing_missing_years_pairwise(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    years = list(range(1990, 1998))
    annual_a = {year: float(year - 1990) for year in years}
    annual_b = {year: 0.4 * (year - 1990) for year in years}
    calls = _install_compare_power(
        monkeypatch,
        "temperature",
        annual_a,
        annual_b,
        missing_a=("19920615",),
        missing_b=("19940615",),
    )

    response = client.post("/api/compare", json=_compare_body(end_year=1997))
    assert response.status_code == 200
    payload = response.json()

    # Each point trend uses its own seven complete years.
    assert payload["point_a"]["trend"]["years_used"] == 7
    assert payload["point_b"]["trend"]["years_used"] == 7
    assert [item["year"] for item in payload["point_a"]["series"]] == years
    assert payload["point_a"]["series"][2]["value"] is None  # 1992
    assert payload["point_b"]["series"][4]["value"] is None  # 1994
    assert any("1992" in warning for warning in payload["point_a"]["warnings"])
    assert any("1994" in warning for warning in payload["point_b"]["warnings"])

    # The difference is fitted on the six common years only.
    common = [year for year in years if year not in (1992, 1994)]
    comparison = payload["comparison"]
    assert comparison["years_used"] == len(common) == 6
    expected_slope = float(
        np.polyfit(
            np.asarray(common, dtype=float),
            np.asarray([annual_a[year] - annual_b[year] for year in common]),
            1,
        )[0]
        * 10.0
    )
    assert comparison["slope_difference_per_decade"] == pytest.approx(expected_slope)
    assert comparison["slope_difference_per_decade"] == pytest.approx(6.0)
    assert comparison["ci95_low"] is None
    assert comparison["p_value"] is None
    assert calls == [
        _expected_call(POINT_A, "temperature", 1990, 1997),
        _expected_call(POINT_B, "temperature", 1990, 1997),
    ]


def test_compare_insufficient_common_years_returns_null_slope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(year - 1990) for year in range(1990, 1995)}
    _install_compare_power(
        monkeypatch,
        "temperature",
        annual,
        annual,
        missing_a=("19920615",),
        missing_b=("19940615",),
    )

    response = client.post("/api/compare", json=_compare_body())
    assert response.status_code == 200
    payload = response.json()

    # Four own complete years per point: not enough for either point's trend.
    assert payload["point_a"]["trend"] is None
    assert payload["point_b"]["trend"] is None
    assert all(item["fitted"] is None for item in payload["point_a"]["series"])

    comparison = payload["comparison"]
    assert comparison["years_used"] == 3
    assert comparison["slope_difference_per_decade"] is None
    assert comparison["ci95_low"] is None
    assert comparison["ci95_high"] is None
    assert comparison["p_value"] is None
    assert comparison["inferential"] is False
    assert "3 common complete" in comparison["note"]
    assert f"at least {MIN_TREND_YEARS}" in comparison["note"]


def test_compare_no_common_years_reports_zero_years(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each point is complete in a disjoint single year: no shared year exists.
    _install_compare_power(
        monkeypatch,
        "temperature",
        {1990: 1.0},
        {1991: 2.0},
    )

    response = client.post("/api/compare", json=_compare_body())
    assert response.status_code == 200
    payload = response.json()

    assert payload["point_a"]["trend"] is None
    assert payload["point_b"]["trend"] is None
    comparison = payload["comparison"]
    assert comparison["years_used"] == 0
    assert comparison["slope_difference_per_decade"] is None
    assert "0 common complete" in comparison["note"]


def test_compare_identical_points_report_zero_difference_slope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 5.0 + 0.25 * (year - 1990) for year in range(1990, 1995)}
    calls = _install_power(monkeypatch, "temperature", annual)

    response = client.post(
        "/api/compare", json=_compare_body(point_b=dict(POINT_A))
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["comparison"]["years_used"] == 5
    assert payload["comparison"]["slope_difference_per_decade"] == 0.0
    assert payload["point_a"]["trend"] == payload["point_b"]["trend"]
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_compare_cache_status_is_independent_per_point(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual_a = {year: float(year - 1990) for year in range(1990, 1995)}
    annual_b = {year: 2.0 + 0.5 * (year - 1990) for year in range(1990, 1995)}
    _install_compare_power(
        monkeypatch,
        "temperature",
        annual_a,
        annual_b,
        cached_a=True,
        cached_b=False,
    )

    response = client.post("/api/compare", json=_compare_body())
    assert response.status_code == 200
    payload = response.json()

    # Each point carries its own cache status and request URL: A and B are
    # separate cache keys, so one hit never implies the other.
    assert payload["point_a"]["source"]["cached"] is True
    assert payload["point_b"]["source"]["cached"] is False
    assert payload["point_a"]["source"]["request_url"] == SOURCE_URL_A
    assert payload["point_b"]["source"]["request_url"] == SOURCE_URL_B
    assert payload["point_a"]["source"]["retrieved_at"] == RETRIEVED_AT
    assert payload["point_b"]["source"]["retrieved_at"] == RETRIEVED_AT


def test_compare_precipitation_uses_shared_variable_units(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual_a = {year: 100.0 + 10.0 * (year - 1990) for year in range(1990, 1996)}
    annual_b = {year: 200.0 + 4.0 * (year - 1990) for year in range(1990, 1996)}
    _install_compare_power(monkeypatch, "precipitation", annual_a, annual_b)

    response = client.post(
        "/api/compare",
        json=_compare_body(variable="precipitation", end_year=1995),
    )
    assert response.status_code == 200
    payload = response.json()

    for name, annual in (("point_a", annual_a), ("point_b", annual_b)):
        point = payload[name]
        assert point["unit"] == "mm"
        assert point["aggregation"] == "sum"
        assert point["source"]["parameter"] == "PRECTOTCORR"
        assert point["source"]["daily_unit"] == "mm/day"
        for item in point["series"]:
            assert item["value"] == pytest.approx(annual[item["year"]], abs=1e-9)

    assert payload["point_a"]["trend"]["per_decade"] == pytest.approx(100.0)
    assert payload["point_b"]["trend"]["per_decade"] == pytest.approx(40.0)
    # Same units by construction: the difference stays in mm per decade.
    assert payload["comparison"]["slope_difference_per_decade"] == pytest.approx(60.0)
    assert payload["comparison"]["p_value"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"point_a": {"latitude": 90.0001, "longitude": 0.0}},
        {"point_a": {"latitude": -90.0001, "longitude": 0.0}},
        {"point_b": {"latitude": 0.0, "longitude": 180.0001}},
        {"point_b": {"latitude": 0.0, "longitude": -180.0001}},
        {"point_a": {"latitude": "nan", "longitude": 0.0}},
        {"point_b": {"latitude": 0.0, "longitude": "inf"}},
        {"point_a": {"longitude": 0.0}},
        {"point_b": [1, 2]},
        {"point_a": None},
        {"variable": "humidity"},
        {"start_year": 1980},
        {"end_year": 2026},
        {"start_year": 2001, "end_year": 2000},
        {"start_year": "not-a-year"},
    ],
)
def test_compare_invalid_bodies_are_rejected_before_power(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    annual = {year: 1.0 for year in range(1990, 1995)}
    calls = _install_compare_power(monkeypatch, "temperature", annual, annual)

    response = client.post("/api/compare", json=_compare_body(**overrides))
    _assert_error(response, 422, "INVALID_REQUEST")
    assert calls == []


def test_compare_missing_field_uses_error_envelope(client: TestClient) -> None:
    body = _compare_body()
    body.pop("point_b")
    response = client.post("/api/compare", json=body)
    error = _assert_error(response, 422, "INVALID_REQUEST")
    assert "point_b" in error["message"]


@pytest.mark.parametrize("failing_point", ["point_a", "point_b"])
def test_compare_upstream_failure_is_explicit_without_partial_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    failing_point: str,
) -> None:
    annual = {year: 1.0 + (year - 1990) for year in range(1990, 1995)}
    daily = _build_daily("temperature", annual)
    target = POINT_A if failing_point == "point_a" else POINT_B
    calls: list[dict[str, object]] = []

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        if kwargs.get("latitude") == target["latitude"]:
            raise PowerError("synthetic compare outage")
        return daily, _source("temperature")

    monkeypatch.setattr(main, "load_daily", fake_load_daily)

    response = client.post("/api/compare", json=_compare_body())
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "synthetic compare outage" in error["message"]
    # Fail fast: point_b is never requested once point_a fails, and no
    # partial point is returned in either order.
    assert len(calls) == (1 if failing_point == "point_a" else 2)
    assert "point_a" not in response.json()


def test_compare_malformed_source_in_either_point_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 1.0 + (year - 1990) for year in range(1990, 1995)}
    daily = _build_daily("temperature", annual)
    good_source = _source("temperature")
    bad_source = _source("temperature") | {"daily_unit": "mm/day"}

    def fake_load_daily(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        is_point_b = kwargs.get("longitude") == POINT_B["longitude"]
        return daily, bad_source if is_point_b else good_source

    monkeypatch.setattr(main, "load_daily", fake_load_daily)

    response = client.post("/api/compare", json=_compare_body())
    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "validation" in error["message"]


def test_compare_requires_post(client: TestClient) -> None:
    response = client.get("/api/compare")
    _assert_error(response, 405, "METHOD_NOT_ALLOWED")


def test_cors_allows_compare_post_preflight(client: TestClient) -> None:
    preflight = client.options(
        "/api/compare",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed = preflight.headers["access-control-allow-methods"].split(", ")
    assert "POST" in allowed
