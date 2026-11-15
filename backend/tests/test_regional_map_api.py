"""Contract tests for ``GET /api/maps/regions`` and ``GET /api/maps/trends``.

Every regional payload in this module is a synthetic, deterministic fixture.
``app.regional_map._regional_data`` is replaced by :class:`StubRegionalData`
before any request runs, so no test contacts NASA POWER and no synthetic data
enters the runtime ingestion path.  The fixtures are labelled as synthetic in
``source.dataset`` so a stray log or screenshot cannot be mistaken for an
observation.

The tests pin down the contract in ``docs/REGIONAL-MAP-API.md``: bounded
GeoJSON with real cell centres and clipped display rectangles, the temperature
and precipitation product rules, no imputation, descriptive-only slopes when
inference is not estimable, HAC(3) inference against an independent matrix
reference, and one Benjamini-Yekutieli family per response.
"""

from __future__ import annotations

import calendar
import copy
import math
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scipy import stats

import app.main as main
from app import analysis, regional_map
from app.power import PowerError
from app.regional_schemas import (
    FDR_Q,
    MIN_INFERENCE_YEARS,
    REGIONAL_MAX_CELLS,
    RegionalMapResponse,
)

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URLS = [
    "https://example.invalid/earthscope-regional-synthetic-tile-69-75",
    "https://example.invalid/earthscope-regional-synthetic-tile-75-81",
]
RETRIEVED_AT = "2026-11-14T12:34:56Z"

LATITUDES = (39.0, 39.5, 40.0)
LONGITUDES = (69.0, 69.625, 70.25)

FEATURE_KEYS = {"type", "id", "geometry", "properties"}
PROPERTY_KEYS = {
    "cell_id",
    "latitude",
    "longitude",
    "elevation_m",
    "quality",
    "valid_years",
    "expected_years",
    "max_gap_years",
    "annual",
    "trend",
}
ANNUAL_KEYS = {"year", "value", "valid_months"}
TREND_KEYS = {
    "per_decade",
    "ci95_low",
    "ci95_high",
    "p_value",
    "q_value_by",
    "significant_by",
    "hac_sensitivity",
}
SOURCE_KEYS = {
    "name",
    "dataset",
    "api_version",
    "time_standard",
    "community",
    "request_urls",
    "retrieved_at",
    "cached",
    "analysis_cached",
}


# ---------------------------------------------------------------------------
# Synthetic fixtures (never NASA observations)
# ---------------------------------------------------------------------------


def temperature_annual(year: int, i: int, j: int) -> float:
    """A smooth warming signal plus a deterministic irregular residual."""

    return (
        6.0
        + 0.045 * (year - 1990)
        + 0.30 * math.sin(0.9 * (year - 1990) + 0.7 * i + 0.4 * j)
        + 0.02 * i
        - 0.03 * j
    )


def temperature_months(year: int, i: int, j: int) -> dict[int, float]:
    annual = temperature_annual(year, i, j)
    return {
        month: annual
        - 3.0 * math.cos(2.0 * math.pi * (month - 0.5) / 12.0)
        + 0.2 * math.sin(month + i)
        for month in range(1, 13)
    }


def precipitation_months(year: int, i: int, j: int) -> dict[int, float]:
    """Monthly rates in mm/day; always positive."""

    return {
        month: 1.4
        + 0.5 * math.sin(2.0 * math.pi * (month - 1) / 12.0 + 0.3 * j)
        + 0.008 * (year - 1990)
        + 0.02 * i
        + 0.05 * math.sin(0.6 * (year - 1990) + month)
        for month in range(1, 13)
    }


def precipitation_total(months: dict[int, float], year: int) -> float:
    return math.fsum(
        months[month] * calendar.monthrange(year, month)[1]
        for month in range(1, 13)
    )


def make_cell(
    latitude: float,
    longitude: float,
    i: int,
    j: int,
    variable: str,
    start_year: int = 1990,
    end_year: int = 2025,
    *,
    missing_months: Sequence[tuple[int, int]] = (),
    annual_override: dict[int, Any] | None = None,
    dropped_years: Sequence[int] = (),
    elevation_m: float | None = None,
) -> dict[str, Any]:
    annual: dict[int, Any] = {}
    monthly: dict[int, dict[int, float]] = {}
    dropped = set(dropped_years)
    for year in range(start_year, end_year + 1):
        if year in dropped:
            continue
        if variable == "temperature":
            months = temperature_months(year, i, j)
            annual_value: Any = temperature_annual(year, i, j)
        else:
            months = precipitation_months(year, i, j)
            # Deliberately unusable: precipitation totals must ignore it.
            annual_value = 9999.0
        for target_year, month in missing_months:
            if target_year == year:
                months.pop(month, None)
        if annual_override and year in annual_override:
            annual_value = annual_override[year]
        monthly[year] = months
        annual[year] = annual_value
    return {
        "latitude": latitude,
        "longitude": longitude,
        "elevation_m": (
            900.0 + 40.0 * i - 15.0 * j if elevation_m is None else elevation_m
        ),
        "annual": annual,
        "monthly": monthly,
    }


def base_cells(
    variable: str = "temperature",
    start_year: int = 1990,
    end_year: int = 2025,
) -> list[dict[str, Any]]:
    return [
        make_cell(latitude, longitude, i, j, variable, start_year, end_year)
        for i, latitude in enumerate(LATITUDES)
        for j, longitude in enumerate(LONGITUDES)
    ]


def make_payload(
    variable: str = "temperature",
    start_year: int = 1990,
    end_year: int = 2025,
    cells: list[dict[str, Any]] | None = None,
    *,
    analysis_cached: bool = True,
) -> dict[str, Any]:
    parameter = "T2M" if variable == "temperature" else "PRECTOTCORR"
    return {
        "region_id": "kg-surroundings",
        "parameter": parameter,
        "start_year": start_year,
        "end_year": end_year,
        "resolution": {"latitude_degrees": 0.5, "longitude_degrees": 0.625},
        "cells": base_cells(variable, start_year, end_year)
        if cells is None
        else cells,
        "source": {
            "name": "NASA POWER",
            "dataset": SYNTHETIC_DATASET,
            "api_version": "v2.10.0",
            "time_standard": "LST",
            "community": "AG",
            "request_urls": list(SYNTHETIC_URLS),
            "retrieved_at": RETRIEVED_AT,
            "cached": False,
            "analysis_cached": analysis_cached,
        },
    }


class StubRegionalData:
    """Test double for the Data-owned ``load_regional_cells`` helper."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str, int, int]] = []

    def load_regional_cells(
        self,
        region_id: str,
        parameter: str,
        start_year: int = 1990,
        end_year: int = 2025,
    ) -> dict[str, Any]:
        self.calls.append((region_id, parameter, start_year, end_year))
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload


def install_stub(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> StubRegionalData:
    stub = StubRegionalData(payload, error)
    monkeypatch.setattr(regional_map, "_regional_data", lambda: stub)
    return stub


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[TestClient]:
    # Each test gets an isolated processed-cache directory; synthetic payloads
    # must never be read by another test or confused with NASA observations.
    monkeypatch.setenv("EARTHSCOPE_CACHE_DIR", str(tmp_path))
    with TestClient(main.app) as test_client:
        yield test_client


def assert_error(response: Any, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


def cell_properties(features: Sequence[dict], latitude: float, longitude: float) -> dict:
    cell_id = f"kg-surroundings:{latitude:.4f}:{longitude:.4f}"
    for feature in features:
        if feature["id"] == cell_id:
            return feature["properties"]
    raise AssertionError(f"missing synthetic cell {cell_id}")


# ---------------------------------------------------------------------------
# /api/maps/regions
# ---------------------------------------------------------------------------


def test_regions_lists_the_bounded_region_without_loading_data(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> Any:
        raise AssertionError("GET /api/maps/regions must not load regional data")

    monkeypatch.setattr(regional_map, "_regional_data", forbidden)

    response = client.get("/api/maps/regions")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"regions"}
    assert len(body["regions"]) == 1

    region = body["regions"][0]
    assert region["region_id"] == "kg-surroundings"
    assert region["region_label"] == "Kyrgyzstan and neighboring areas"
    assert region["bounds"] == {
        "south": 39.0,
        "north": 44.0,
        "west": 69.0,
        "east": 81.0,
    }
    assert (region["start_year"], region["end_year"]) == (1990, 2025)
    assert region["max_cells"] == REGIONAL_MAX_CELLS
    variables = {item["variable"]: item for item in region["variables"]}
    assert variables["temperature"] == {
        "variable": "temperature",
        "parameter": "T2M",
        "unit": "°C",
        "aggregation": "annual_mean",
    }
    assert variables["precipitation"] == {
        "variable": "precipitation",
        "parameter": "PRECTOTCORR",
        "unit": "mm",
        "aggregation": "sum_monthly_daily_rate_times_days",
    }


def test_trends_never_uses_the_daily_point_ingestion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = install_stub(monkeypatch, make_payload())

    def forbidden(**_: Any) -> Any:
        raise AssertionError("regional maps must not call the Daily Point API")

    monkeypatch.setattr(main, "load_daily", forbidden)

    response = client.get("/api/maps/trends?variable=temperature")
    assert response.status_code == 200
    assert stub.calls == [("kg-surroundings", "T2M", 1990, 2025)]


def test_grid_spacing_is_inferred_when_no_resolution_is_declared(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = make_payload()
    payload.pop("resolution")
    install_stub(monkeypatch, payload)

    body = client.get("/api/maps/trends?variable=temperature").json()
    assert body["metadata"]["resolution"] == {
        "latitude_degrees": 0.5,
        "longitude_degrees": 0.625,
    }


def test_flat_yyyymm_values_map_is_normalised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    source_cell = cells[1]
    values: dict[str, Any] = {}
    for year, annual_value in source_cell["annual"].items():
        values[f"{year}13"] = annual_value
        for month, value in source_cell["monthly"][year].items():
            values[f"{year}{month:02d}"] = value
    cells[1] = {
        "latitude": source_cell["latitude"],
        "longitude": source_cell["longitude"],
        "elevation_m": None,
        "values": values,
    }
    install_stub(monkeypatch, make_payload(cells=cells))

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[1],
    )
    annual = {item["year"]: item["value"] for item in properties["annual"]}
    assert annual[1990] == pytest.approx(temperature_annual(1990, 0, 1), rel=1e-12)
    assert properties["quality"] == "complete"


# ---------------------------------------------------------------------------
# /api/maps/trends: shape, grid and provenance
# ---------------------------------------------------------------------------


def test_trends_returns_bounded_geojson_with_real_centres(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = install_stub(monkeypatch, make_payload())

    response = client.get(
        "/api/maps/trends?region=kg-surroundings&variable=temperature"
        "&start_year=1990&end_year=2025"
    )
    assert response.status_code == 200
    body = response.json()

    assert stub.calls == [("kg-surroundings", "T2M", 1990, 2025)]
    assert body["type"] == "FeatureCollection"
    metadata = body["metadata"]
    assert metadata["region_id"] == "kg-surroundings"
    assert metadata["region_label"] == "Kyrgyzstan and neighboring areas"
    assert metadata["variable"] == "temperature"
    assert metadata["parameter"] == "T2M"
    assert metadata["unit"] == "°C"
    assert metadata["aggregation"] == "annual_mean"
    assert (metadata["start_year"], metadata["end_year"]) == (1990, 2025)
    assert metadata["bounds"] == {
        "south": 39.0,
        "north": 44.0,
        "west": 69.0,
        "east": 81.0,
    }
    assert metadata["resolution"] == {
        "latitude_degrees": 0.5,
        "longitude_degrees": 0.625,
    }
    assert set(metadata["source"]) == SOURCE_KEYS
    assert metadata["source"]["dataset"] == SYNTHETIC_DATASET
    assert metadata["source"]["time_standard"] == "LST"
    assert metadata["source"]["community"] == "AG"
    assert metadata["source"]["request_urls"] == SYNTHETIC_URLS
    assert metadata["source"]["cached"] is False
    assert metadata["source"]["analysis_cached"] is True
    assert metadata["inference"] == {
        "method": "ols_slope_hac_bartlett",
        "hac_lags": 3,
        "fdr": "BY",
        "fdr_q": FDR_Q,
        "tested_cells": len(base_cells()),
    }
    assert metadata["summary"] == {
        "total_cells": 9,
        "trend_cells": 9,
        "fdr_cells": 9,
    }

    features = body["features"]
    assert len(features) == 9
    assert len(features) <= REGIONAL_MAX_CELLS
    for feature in features:
        assert set(feature) == FEATURE_KEYS
        assert feature["type"] == "Feature"
        assert feature["id"] == feature["properties"]["cell_id"]
        assert set(feature["properties"]) == PROPERTY_KEYS
        properties = feature["properties"]
        assert properties["latitude"] in LATITUDES
        assert properties["longitude"] in LONGITUDES
        assert properties["elevation_m"] is not None
        assert properties["quality"] == "complete"
        assert properties["valid_years"] == properties["expected_years"] == 36
        assert properties["max_gap_years"] == 0
        assert [item["year"] for item in properties["annual"]] == list(
            range(1990, 2026)
        )
        assert all(
            set(item) == ANNUAL_KEYS and item["valid_months"] == 12
            for item in properties["annual"]
        )
        assert all(
            isinstance(item["value"], float) for item in properties["annual"]
        )
        assert set(properties["trend"]) == TREND_KEYS


def test_processed_layer_cache_avoids_rebuilding_synthetic_fixture(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = install_stub(monkeypatch, make_payload(analysis_cached=False))
    uri = "/api/maps/trends?variable=temperature&start_year=1990&end_year=2025"
    first = client.get(uri)
    second = client.get(uri)
    assert first.status_code == second.status_code == 200
    # Re-read the raw cache identity on each call; calculation uses processed
    # cache while ingestion sees the same provenance stamp.
    assert stub.calls == [
        ("kg-surroundings", "T2M", 1990, 2025),
        ("kg-surroundings", "T2M", 1990, 2025),
    ]
    assert first.json()["metadata"]["source"]["analysis_cached"] is False
    assert second.json()["metadata"]["source"]["analysis_cached"] is True


def test_source_retrieval_stamp_invalidates_processed_layer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = make_payload(analysis_cached=False)
    stub = install_stub(monkeypatch, payload)
    uri = "/api/maps/trends?variable=temperature&start_year=1990&end_year=2025"
    first = client.get(uri)
    assert first.status_code == 200
    payload["source"]["retrieved_at"] = "2026-11-15T00:00:00Z"
    second = client.get(uri)
    assert second.status_code == 200
    assert second.json()["metadata"]["source"]["analysis_cached"] is False
    assert stub.calls == [
        ("kg-surroundings", "T2M", 1990, 2025),
        ("kg-surroundings", "T2M", 1990, 2025),
    ]


def test_refreshing_earlier_tile_invalidates_processed_layer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = make_payload(analysis_cached=False)
    source = payload["source"]
    source["tile_retrieved_at"] = ["2026-11-13T00:00:00Z", source["retrieved_at"]]
    install_stub(monkeypatch, payload)
    uri = "/api/maps/trends?variable=temperature"
    first = client.get(uri).json()
    source["tile_retrieved_at"][0] = "2026-11-14T00:00:00Z"
    payload["cells"][0]["annual"][1990] += 1
    second = client.get(uri).json()
    assert second["metadata"]["source"]["analysis_cached"] is False
    old_value = first["features"][0]["properties"]["annual"][0]["value"]
    assert second["features"][0]["properties"]["annual"][0]["value"] == old_value + 1


def test_cell_rectangles_use_inferred_spacing_and_clip_to_bounds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload())

    features = client.get("/api/maps/trends?variable=temperature").json()["features"]

    south_west = next(
        feature
        for feature in features
        if feature["id"] == "kg-surroundings:39.0000:69.0000"
    )
    ring = south_west["geometry"]["coordinates"][0]
    assert south_west["geometry"]["type"] == "Polygon"
    assert len(ring) == 5
    assert ring[0] == ring[-1]
    assert ring == [
        [69.0, 39.0],  # clipped at the declared west/south edges
        [69.3125, 39.0],
        [69.3125, 39.25],
        [69.0, 39.25],
        [69.0, 39.0],
    ]

    for feature in features:
        for longitude, latitude in feature["geometry"]["coordinates"][0]:
            assert 69.0 <= longitude <= 81.0
            assert 39.0 <= latitude <= 44.0


def test_duplicate_tile_boundary_cells_are_deduplicated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    cells.append(copy.deepcopy(cells[0]))
    install_stub(monkeypatch, make_payload(cells=cells))

    body = client.get("/api/maps/trends?variable=temperature").json()
    assert body["metadata"]["summary"]["total_cells"] == 9
    assert len({feature["id"] for feature in body["features"]}) == 9


def test_conflicting_duplicate_cells_are_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    conflicting = copy.deepcopy(cells[0])
    conflicting["annual"][1990] = -12.5
    cells.append(conflicting)
    install_stub(monkeypatch, make_payload(cells=cells))

    response = client.get("/api/maps/trends?variable=temperature")
    assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "disagree" in response.json()["error"]["message"]


def test_more_than_209_unique_cells_is_a_contract_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = [
        make_cell(39.0 + 0.01 * index, 69.0, 0, 0, "temperature")
        for index in range(REGIONAL_MAX_CELLS + 1)
    ]
    install_stub(monkeypatch, make_payload(cells=cells))

    response = client.get("/api/maps/trends?variable=temperature")
    assert_error(response, 502, "POWER_UNAVAILABLE")
    assert str(REGIONAL_MAX_CELLS) in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Product rules: no imputation, temperature, precipitation
# ---------------------------------------------------------------------------


def test_temperature_requires_the_annual_key_and_all_twelve_months(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    cells[0] = make_cell(
        LATITUDES[0],
        LONGITUDES[0],
        0,
        0,
        "temperature",
        missing_months=[(2016, 3)],
        annual_override={2017: -999.0},
    )
    install_stub(monkeypatch, make_payload(cells=cells))

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    annual = {item["year"]: item for item in properties["annual"]}

    # One missing month invalidates 2016 even though the annual key exists.
    assert annual[2016] == {"year": 2016, "value": None, "valid_months": 11}
    # The NASA fill value in the annual key invalidates 2017 despite 12 months.
    assert annual[2017] == {"year": 2017, "value": None, "valid_months": 12}
    assert properties["quality"] == "incomplete"
    assert properties["valid_years"] == 34
    assert properties["max_gap_years"] == 2  # 2016-2017 are consecutive gaps
    assert annual[2018]["value"] is not None


def test_precipitation_sums_monthly_rates_by_gregorian_month_length(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload("precipitation"))

    body = client.get("/api/maps/trends?variable=precipitation").json()
    assert body["metadata"]["parameter"] == "PRECTOTCORR"
    assert body["metadata"]["unit"] == "mm"
    assert (
        body["metadata"]["aggregation"] == "sum_monthly_daily_rate_times_days"
    )

    properties = cell_properties(
        body["features"], LATITUDES[0], LONGITUDES[0]
    )
    annual = {item["year"]: item["value"] for item in properties["annual"]}
    for year in (1990, 2000, 2001, 2024):
        expected = precipitation_total(
            precipitation_months(year, 0, 0), year
        )
        assert annual[year] == pytest.approx(expected, rel=1e-12, abs=1e-9)

    # 2000 is a leap year, 2001 is not: the difference must survive in totals.
    assert properties["quality"] == "complete"
    assert annual[2000] != pytest.approx(annual[2001])


def test_invalid_precipitation_month_blocks_the_year_without_zero_fill(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells("precipitation")
    cells[0] = make_cell(
        LATITUDES[0],
        LONGITUDES[0],
        0,
        0,
        "precipitation",
        missing_months=[(2003, 2)],
    )
    install_stub(monkeypatch, make_payload("precipitation", cells=cells))

    properties = cell_properties(
        client.get("/api/maps/trends?variable=precipitation").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    annual = {item["year"]: item for item in properties["annual"]}
    assert annual[2003] == {"year": 2003, "value": None, "valid_months": 11}
    assert properties["valid_years"] == 35
    assert properties["quality"] == "incomplete"


def test_missing_years_stay_missing_and_are_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    cells[0] = make_cell(
        LATITUDES[0],
        LONGITUDES[0],
        0,
        0,
        "temperature",
        dropped_years=[2001],
    )
    install_stub(monkeypatch, make_payload(cells=cells))

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    annual = {item["year"]: item for item in properties["annual"]}
    assert annual[2001] == {"year": 2001, "value": None, "valid_months": 0}
    assert properties["valid_years"] == 35
    assert properties["expected_years"] == 36
    assert properties["quality"] == "incomplete"
    assert properties["max_gap_years"] == 1
    assert properties["trend"] is not None
    assert properties["trend"]["per_decade"] is not None
    # 35 complete years with a gap: descriptive only, no inference.
    assert properties["trend"]["p_value"] is None
    assert properties["trend"]["ci95_low"] is None
    assert properties["trend"]["q_value_by"] is None
    assert properties["trend"]["hac_sensitivity"] == []


def test_insufficient_years_has_no_trend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    cells[0] = make_cell(
        LATITUDES[0],
        LONGITUDES[0],
        0,
        0,
        "temperature",
        dropped_years=list(range(1994, 2026)),
    )
    install_stub(monkeypatch, make_payload(cells=cells))

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    assert properties["valid_years"] == 4
    assert properties["quality"] == "insufficient_years"
    assert properties["trend"] is None


def test_empty_cell_maps_render_as_insufficient_without_imputation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cells = base_cells()
    cells[0] = {
        "latitude": LATITUDES[0],
        "longitude": LONGITUDES[0],
        "elevation_m": None,
        "annual": {},
        "monthly": {},
    }
    install_stub(monkeypatch, make_payload(cells=cells))

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    assert properties["valid_years"] == 0
    assert properties["quality"] == "insufficient_years"
    assert properties["max_gap_years"] == 36
    assert properties["trend"] is None
    assert all(item["value"] is None for item in properties["annual"])


# ---------------------------------------------------------------------------
# Slope, HAC inference and BY adjustment
# ---------------------------------------------------------------------------


def test_descriptive_slope_matches_the_shared_ols_fit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload())

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    years = np.arange(1990, 2026, dtype=float)
    values = np.array(
        [temperature_annual(int(year), 0, 0) for year in years], dtype=float
    )
    slope_per_year = float(np.polyfit(years, values, 1)[0])

    assert properties["trend"]["per_decade"] == pytest.approx(
        10.0 * slope_per_year, rel=1e-12
    )


def reference_hac(
    years: Sequence[float], values: Sequence[float], lags: int
) -> tuple[float, float, float]:
    """Independent matrix sandwich reference for the OLS slope."""

    year_values = np.asarray(years, dtype=float)
    observed = np.asarray(values, dtype=float)
    count = year_values.size
    design = np.column_stack((np.ones(count), year_values))
    beta = np.linalg.lstsq(design, observed, rcond=None)[0]
    residuals = observed - design @ beta
    inverse = np.linalg.inv(design.T @ design)
    positions = np.arange(count)
    weights = np.maximum(
        0.0,
        1.0 - np.abs(np.subtract.outer(positions, positions)) / (lags + 1.0),
    )
    covariance = (
        inverse
        @ design.T
        @ (weights * np.outer(residuals, residuals))
        @ design
        @ inverse
        * count
        / (count - 2)
    )
    error = math.sqrt(covariance[1, 1])
    p_value = float(
        2.0 * stats.t.sf(abs(beta[1] / error), count - 2)
    )
    return float(beta[1]), error, p_value


def test_hac3_interval_p_and_sensitivity_match_a_matrix_reference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload())

    properties = cell_properties(
        client.get("/api/maps/trends?variable=temperature").json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    trend = properties["trend"]
    years = [float(year) for year in range(1990, 2026)]
    values = [temperature_annual(year, 0, 0) for year in range(1990, 2026)]

    slope, error, p_value = reference_hac(years, values, 3)
    critical = float(stats.t.ppf(0.975, len(years) - 2))

    assert trend["p_value"] == pytest.approx(p_value, rel=1e-10, abs=1e-12)
    assert trend["ci95_low"] == pytest.approx(
        10.0 * (slope - critical * error), rel=1e-10
    )
    assert trend["ci95_high"] == pytest.approx(
        10.0 * (slope + critical * error), rel=1e-10
    )

    sensitivity = {item["lags"]: item["p_value"] for item in trend["hac_sensitivity"]}
    assert sorted(sensitivity) == [1, 3, 5]
    assert sensitivity[3] == pytest.approx(trend["p_value"], rel=0, abs=0)
    for lags in (1, 3, 5):
        assert sensitivity[lags] == pytest.approx(
            reference_hac(years, values, lags)[2], rel=1e-10, abs=1e-12
        )


def test_inference_requires_27_complete_consecutive_years(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload(start_year=1990, end_year=2015))
    properties = cell_properties(
        client.get(
            "/api/maps/trends?variable=temperature&start_year=1990&end_year=2015"
        ).json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    assert properties["quality"] == "complete"
    assert properties["trend"]["per_decade"] is not None
    assert properties["trend"]["p_value"] is None
    assert properties["trend"]["ci95_high"] is None
    assert properties["trend"]["hac_sensitivity"] == []
    assert properties["expected_years"] == MIN_INFERENCE_YEARS - 1

    install_stub(monkeypatch, make_payload(start_year=1990, end_year=2016))
    properties = cell_properties(
        client.get(
            "/api/maps/trends?variable=temperature&start_year=1990&end_year=2016"
        ).json()["features"],
        LATITUDES[0],
        LONGITUDES[0],
    )
    assert properties["trend"]["p_value"] is not None
    assert sorted(
        item["lags"] for item in properties["trend"]["hac_sensitivity"]
    ) == [1, 3, 5]


def test_benjamini_yekutieli_matches_the_closed_form() -> None:
    p_values = [0.01, 0.04, 0.03, 0.2]
    harmonic = 1.0 + 1.0 / 2.0 + 1.0 / 3.0 + 1.0 / 4.0
    # Sorted p: 0.01, 0.03, 0.04, 0.2 with BY multipliers m*H_m/rank.
    # Step-up enforcement makes the 0.03 q-value inherit the 0.04 rank's
    # smaller adjusted value, so both are 0.04 * 4 * H / 3.
    highest = 0.04 * 4.0 * harmonic / 3.0
    assert regional_map.benjamini_yekutieli(p_values) == pytest.approx(
        [
            0.01 * 4.0 * harmonic,
            highest,
            highest,
            0.2 * 4.0 * harmonic / 4.0,
        ]
    )
    assert regional_map.benjamini_yekutieli([]) == []
    assert regional_map.benjamini_yekutieli([1.0]) == [1.0]


def test_api_q_values_are_by_adjusted_and_flagged_at_q_010(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload())

    features = client.get("/api/maps/trends?variable=temperature").json()["features"]
    trends = [feature["properties"]["trend"] for feature in features]
    p_values = [trend["p_value"] for trend in trends]
    q_values = [trend["q_value_by"] for trend in trends]

    assert regional_map.benjamini_yekutieli(p_values) == pytest.approx(
        q_values, rel=1e-12, abs=1e-15
    )
    for trend in trends:
        assert trend["q_value_by"] >= trend["p_value"] - 1e-12
        assert trend["significant_by"] == (trend["q_value_by"] <= FDR_Q)


# ---------------------------------------------------------------------------
# Errors, data-contract failures and schema guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "status", "code"),
    [
        ("variable=wind", 422, "INVALID_REQUEST"),
        ("variable=temperature&start_year=1989", 422, "INVALID_REQUEST"),
        ("variable=temperature&end_year=2026", 422, "INVALID_REQUEST"),
        (
            "variable=temperature&start_year=2000&end_year=1999",
            422,
            "INVALID_REQUEST",
        ),
        ("region=atlantis", 404, "NOT_FOUND"),
    ],
)
def test_request_validation_uses_the_shared_error_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    status: int,
    code: str,
) -> None:
    stub = install_stub(monkeypatch, make_payload())
    response = client.get(f"/api/maps/trends?{query}")
    assert_error(response, status, code)
    assert stub.calls == []


def test_missing_data_module_maps_to_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> Any:
        raise regional_map.RegionalDataUnavailable("not installed")

    monkeypatch.setattr(regional_map, "_regional_data", unavailable)
    response = client.get("/api/maps/trends?variable=temperature")
    assert_error(response, 503, "POWER_UNAVAILABLE")


def test_upstream_power_error_maps_to_the_existing_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(
        monkeypatch, error=PowerError("NASA POWER returned HTTP 500")
    )
    response = client.get("/api/maps/trends?variable=temperature")
    assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "HTTP 500" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda source: source.update(time_standard="UTC"),
        lambda source: source.update(community="RE"),
        lambda source: source.update(request_urls=[]),
        lambda source: source.update(retrieved_at="2026-11-14T12:34:56"),
        lambda source: source.pop("dataset"),
    ],
)
def test_source_provenance_failures_map_to_502(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
) -> None:
    payload = make_payload()
    mutate(payload["source"])
    install_stub(monkeypatch, payload)

    response = client.get("/api/maps/trends?variable=temperature")
    assert_error(response, 502, "POWER_UNAVAILABLE")


def test_response_model_rejects_a_mismatched_summary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload())
    body = client.get("/api/maps/trends?variable=temperature").json()
    body["metadata"]["summary"]["total_cells"] = 8

    with pytest.raises(ValidationError):
        RegionalMapResponse.model_validate(body)


def test_response_model_rejects_q_and_flag_disagreement(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub(monkeypatch, make_payload())
    body = client.get("/api/maps/trends?variable=temperature").json()
    body["features"][0]["properties"]["trend"]["q_value_by"] = 0.5
    body["features"][0]["properties"]["trend"]["significant_by"] = True

    with pytest.raises(ValidationError):
        RegionalMapResponse.model_validate(body)


# ---------------------------------------------------------------------------
# Shared statistical helper
# ---------------------------------------------------------------------------


def test_shared_hac_helper_matches_the_matrix_reference_and_guards_degeneracy() -> None:
    years = [float(year) for year in range(1990, 2026)]
    values = [10.0 + 0.2 * (year - 1990) + 0.4 * math.sin(year) for year in years]
    design = np.column_stack((np.ones(len(years)), years))
    beta = np.linalg.lstsq(design, np.asarray(values), rcond=None)[0]
    fitted = (design @ beta).tolist()

    for lags in (1, 3, 5):
        error = analysis.hac_slope_standard_error(years, values, fitted, lags)
        assert error == pytest.approx(
            reference_hac(years, values, lags)[1], rel=1e-12
        )

    perfect = [2.0 + 0.5 * (year - 1990) for year in years]
    fitted_perfect = perfect
    assert (
        analysis.hac_slope_standard_error(
            years, perfect, fitted_perfect, 3
        )
        is None
    )
