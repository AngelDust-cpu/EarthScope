"""Tests for backend/app/regional_data.py.

All upstream responses are deterministic SYNTHETIC GeoJSON fixtures injected
through ``httpx.MockTransport``; no test touches the network. Cache paths are
isolated to temporary directories via ``EARTHSCOPE_CACHE_DIR``. The fixture
shape mirrors the live NASA POWER Monthly/Annual Regional sample probed on
2026-11-14 (docs/REGIONAL-MAP-API.md): a ``FeatureCollection`` whose header
reports ``MERRA2``/``v2.10.0``/``time_standard=LST``/``fill_value=-999`` and
YYYYMMDD coverage stamps, and whose features are Point geometries with
``[longitude, latitude, elevation]`` coordinates and per-cell ``YYYYMM`` /
``YYYY13`` time maps under ``properties.parameter``. The fixtures are clearly
labelled synthetic data and are never used at runtime.
"""

import json
import re
import threading

import httpx
import pytest

from app import regional_data
from app.regional_data import (
    INTER_TILE_DELAY_SECONDS,
    RegionalError,
    load_regional_cells,
)

ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
FILL = -999.0

DEFAULT_UNITS = {"T2M": "C", "PRECTOTCORR": "mm/day"}

# Small deterministic synthetic grid: 3 latitude rows x 6 longitude columns
# after the two tiles merge (the 71.875 column is shared at the tile edge).
LATS = (39.0, 39.5, 40.0)
TILE1_LONS = (69.375, 70.0, 70.625, 71.25, 71.875)
TILE2_LONS = (71.875, 72.5)
MERGED_LONS = (69.375, 70.0, 70.625, 71.25, 71.875, 72.5)

# The documented full-extent synthetic grid: 11 rows x (10 + 10 - 1) = 209.
FULL_LATS = tuple(39.0 + 0.5 * index for index in range(11))
FULL_TILE1_LONS = tuple(69.375 + 0.625 * index for index in range(10))
FULL_TILE2_LONS = tuple(75.0 + 0.625 * index for index in range(10))


def value_for(latitude, longitude, year, month):
    """Deterministic synthetic value, identical in fixtures and assertions."""
    return round(
        5.0 + 0.01 * latitude + 0.01 * longitude + 0.1 * (year - 1990) + 0.1 * month,
        4,
    )


def make_feature(
    latitude,
    longitude,
    elevation=3000.0,
    parameter="T2M",
    years=range(1990, 2026),
    fill_keys=(),
    overrides=None,
):
    """Build one synthetic regional feature (all keys per year 01..13)."""
    values = {}
    for year in years:
        for month in range(1, 14):
            key = f"{year}{month:02d}"
            if key in fill_keys:
                values[key] = FILL
            elif overrides and key in overrides:
                values[key] = overrides[key]
            else:
                values[key] = value_for(latitude, longitude, year, month)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude, elevation]},
        "properties": {"parameter": {parameter: values}},
    }


def make_tile(
    latitudes,
    longitudes,
    parameter="T2M",
    elevation=3000.0,
    years=range(1990, 2026),
    fill_keys=None,
    overrides=None,
    skip_centers=(),
):
    fill_keys = fill_keys or {}
    overrides = overrides or {}
    return [
        make_feature(
            latitude,
            longitude,
            elevation=elevation,
            parameter=parameter,
            years=years,
            fill_keys=fill_keys.get((latitude, longitude), ()),
            overrides=overrides.get((latitude, longitude)),
        )
        for latitude in latitudes
        for longitude in longitudes
        if (latitude, longitude) not in skip_centers
    ]


def make_payload(
    features,
    *,
    parameter="T2M",
    unit=None,
    fill_value=FILL,
    sources=None,
    time_standard="LST",
    api_version="v2.10.0",
    start="19900101",
    end="20251231",
    response_type="FeatureCollection",
    drop_header=False,
    drop_units=False,
):
    payload = {
        "type": response_type,
        "header": {
            "title": "NASA/POWER Source Native Resolution Monthly and Annual",
            "api": {"version": api_version, "name": "POWER Monthly and Annual API"},
            "sources": ["MERRA2"] if sources is None else sources,
            "fill_value": fill_value,
            "time_standard": time_standard,
            "start": start,
            "end": end,
        },
        "parameters": {
            parameter: {
                "units": unit or DEFAULT_UNITS[parameter],
                "longname": "synthetic fixture longname",
            }
        },
        "features": features,
        "messages": [],
        "times": [],
    }
    if drop_header:
        del payload["header"]
    if drop_units:
        del payload["parameters"]
    return payload


def default_tiles(parameter="T2M", **kwargs):
    return {
        "69.0": make_tile(LATS, TILE1_LONS, parameter=parameter, **kwargs),
        "75.0": make_tile(LATS, TILE2_LONS, parameter=parameter, **kwargs),
    }


def regional_transport(tiles_by_lon_min, capture=None, events=None, status=200, content=None):
    """MockTransport dispatching fixture payloads by the requested tile."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        lon_min = request.url.params.get("longitude-min")
        fixture_key = lon_min if lon_min in tiles_by_lon_min else f"{float(lon_min):.1f}"
        if events is not None:
            events.append(("request", fixture_key))
        payload = tiles_by_lon_min.get(fixture_key)
        if payload is None:
            return httpx.Response(500, content=b"unexpected tile requested")
        # The request table stores a tile's feature list; wrap it in NASA's
        # normal FeatureCollection envelope unless a test supplied a custom
        # complete payload to exercise malformed metadata.
        if isinstance(payload, list):
            parameter = request.url.params.get("parameters", "T2M")
            payload = make_payload(payload, parameter=parameter)
        body = content if content is not None else json.dumps(payload)
        return httpx.Response(
            status, content=body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    return httpx.MockTransport(handler)


def load(transport, *, region_id="kg-surroundings", parameter="T2M",
         start_year=1990, end_year=2025):
    return load_regional_cells(
        region_id, parameter, start_year, end_year, transport=transport
    )


def cell_map(cells):
    return {
        (cell.latitude, cell.longitude): (cell.elevation_m, cell.values)
        for cell in cells
    }


@pytest.fixture()
def no_cache(monkeypatch):
    monkeypatch.delenv("EARTHSCOPE_CACHE_DIR", raising=False)


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    path = tmp_path / "cache"
    monkeypatch.setenv("EARTHSCOPE_CACHE_DIR", str(path))
    return path


# ---------------------------------------------------------------------------
# Happy path, request shape and metadata
# ---------------------------------------------------------------------------


def test_happy_path_cells_and_source_metadata(no_cache):
    capture = []
    transport = regional_transport(default_tiles(), capture=capture)
    cells, source = load(transport)

    assert len(cells) == len(LATS) * len(MERGED_LONS) == 18
    assert [(cell.latitude, cell.longitude) for cell in cells] == sorted(
        (lat, lon) for lat in LATS for lon in MERGED_LONS
    )
    assert len({(cell.latitude, cell.longitude) for cell in cells}) == len(cells)

    merged = cell_map(cells)
    elevation, values = merged[(39.5, 71.875)]
    assert elevation == 3000.0
    assert len(values) == 36 * 13
    assert values["199001"] == value_for(39.5, 71.875, 1990, 1)
    assert values["202513"] == value_for(39.5, 71.875, 2025, 13)

    assert source["name"] == "NASA POWER"
    assert source["service"] == "monthly-regional"
    assert source["parameter"] == "T2M"
    assert source["native_unit"] == "C"
    assert source["dataset"] == "MERRA2"
    assert source["api_version"] == "v2.10.0"
    assert source["time_standard"] == "LST"
    assert source["community"] == "AG"
    assert source["region_id"] == "kg-surroundings"
    assert source["bounds"] == {"south": 39.0, "north": 44.0, "west": 69.0, "east": 81.0}
    assert source["coverage_start_year"] == 1990
    assert source["coverage_end_year"] == 2025
    assert source["start_year"] == 1990 and source["end_year"] == 2025
    assert source["cached"] is False
    assert source["tile_cache_states"] == [False, False]
    assert source["grid"] == {
        "latitude_degrees": 0.5,
        "longitude_degrees": 0.625,
        "rows": 3,
        "columns": 6,
        "cells": 18,
    }
    assert ISO_UTC_RE.match(source["retrieved_at"])
    assert len(source["request_urls"]) == 2
    assert source["request_urls"] == [str(request.url) for request in capture]


def test_request_urls_use_two_tiles_and_fixed_coverage(no_cache):
    capture = []
    load(regional_transport(default_tiles(), capture=capture))

    assert len(capture) == 2
    first, second = (str(request.url) for request in capture)
    for url in (first, second):
        assert "monthly/regional" in url
        assert "latitude-min=39" in url and "latitude-max=44" in url
        assert "parameters=T2M" in url
        assert "community=AG" in url
        assert "start=1990" in url and "end=2025" in url
        assert "format=JSON" in url
        assert "user=EARTHSCOPE" in url
    assert "longitude-min=69" in first and "longitude-max=75" in first
    assert "longitude-min=75" in second and "longitude-max=81" in second


def test_documented_full_grid_deduplicates_to_209_cells(no_cache):
    """Synthetic replica of the documented 110 + 110 -> 209 tile merge."""
    tiles = {
        "69.0": make_tile(FULL_LATS, FULL_TILE1_LONS, years=range(1990, 1992)),
        "75.0": make_tile(FULL_LATS, FULL_TILE2_LONS, years=range(1990, 1992)),
    }
    cells, source = load(regional_transport(tiles))

    assert len(cells) == 209
    grid = source["grid"]
    assert grid["rows"] == 11
    assert grid["columns"] == 19
    assert grid["cells"] == 209
    assert grid["latitude_degrees"] == pytest.approx(0.5)
    assert grid["longitude_degrees"] == pytest.approx(0.625)
    lats = sorted({cell.latitude for cell in cells})
    lons = sorted({cell.longitude for cell in cells})
    assert len(lats) == 11 and len(lons) == 19
    assert 75.0 in lons  # shared boundary column kept exactly once


def test_precipitation_parameter_happy_path(no_cache):
    transport = regional_transport(default_tiles(parameter="PRECTOTCORR"))
    cells, source = load(transport, parameter="PRECTOTCORR")

    assert len(cells) == 18
    assert source["parameter"] == "PRECTOTCORR"
    assert source["native_unit"] == "mm/day"


def test_year_span_filters_time_maps_but_not_raw_coverage(no_cache):
    transport = regional_transport(default_tiles())
    cells, source = load(transport, start_year=2000, end_year=2001)

    assert source["start_year"] == 2000 and source["end_year"] == 2001
    merged = cell_map(cells)
    for (latitude, longitude), (_elevation, values) in merged.items():
        assert set(values) == {
            f"{year}{month:02d}" for year in (2000, 2001) for month in range(1, 14)
        }
        assert values["200001"] == value_for(latitude, longitude, 2000, 1)


def test_elevation_may_be_missing(no_cache):
    tiles = default_tiles()
    tiles["69.0"][0]["geometry"]["coordinates"] = [69.375, 39.0]
    tiles["75.0"][1]["geometry"]["coordinates"] = [72.5, 39.0, None]

    cells, _source = load(regional_transport(tiles))
    merged = cell_map(cells)
    assert merged[(39.0, 69.375)][0] is None
    assert merged[(39.0, 72.5)][0] is None
    assert merged[(39.5, 70.0)][0] == 3000.0


def test_retrieved_at_is_iso_utc(no_cache):
    _cells, source = load(regional_transport(default_tiles()))
    assert ISO_UTC_RE.match(source["retrieved_at"])


# ---------------------------------------------------------------------------
# Grid inference and deduplication edges
# ---------------------------------------------------------------------------


def test_grid_spacing_inferred_not_assumed(no_cache):
    """A different native spacing must be inferred and accepted."""
    latitudes = (39.5, 40.5, 41.5)
    tile1_lons = (69.5, 70.5, 71.5, 72.5, 73.5, 74.5)
    tile2_lons = (75.5, 76.5, 77.5, 78.5, 79.5, 80.5)
    tiles = {
        "69.0": make_tile(latitudes, tile1_lons, years=range(1990, 1992)),
        "75.0": make_tile(latitudes, tile2_lons, years=range(1990, 1992)),
    }
    cells, source = load(regional_transport(tiles))

    assert source["grid"]["latitude_degrees"] == pytest.approx(1.0)
    assert source["grid"]["longitude_degrees"] == pytest.approx(1.0)
    assert source["grid"]["rows"] == 3
    assert source["grid"]["columns"] == 12
    assert len(cells) == 36


def value_map_from(latitude, longitude):
    return {
        f"{year}{month:02d}": value_for(latitude, longitude, year, month)
        for year in range(1990, 2026)
        for month in range(1, 14)
    }


def test_boundary_duplicate_with_equal_values_merges(no_cache):
    transport = regional_transport(default_tiles())
    cells, _source = load(transport)

    merged = cell_map(cells)
    assert (39.5, 71.875) in merged  # exactly-equal boundary center merged
    elevation, values = merged[(39.5, 71.875)]
    assert values == value_map_from(39.5, 71.875)


def test_near_duplicate_centers_are_not_merged(no_cache):
    """Only exactly equal centers deduplicate; a 1e-9 offset stays distinct."""
    offset = 71.875 + 1e-9
    tiles = {
        "69.0": make_tile(LATS, TILE1_LONS, years=range(1990, 1992)),
        "75.0": make_tile(LATS, (offset, 72.5), years=range(1990, 1992)),
    }
    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert excinfo.value.code == "POWER_UNAVAILABLE"
    assert "spacing" in str(excinfo.value)


def test_duplicate_center_value_mismatch_rejected(no_cache):
    tiles = default_tiles(parameter="T2M", years=range(1990, 1992))
    conflicting = make_tile(LATS, (71.875, 72.5), years=range(1990, 1992))
    feature = conflicting[0]  # the shared boundary center in tile 2
    feature["properties"]["parameter"]["T2M"]["199001"] = 42.0
    tiles["75.0"] = conflicting

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert excinfo.value.code == "POWER_UNAVAILABLE"
    assert "disagrees" in str(excinfo.value)


def test_duplicate_center_key_set_mismatch_rejected(no_cache):
    tiles = default_tiles(years=range(1990, 1992))
    conflicting = make_tile(LATS, (71.875, 72.5), years=range(1990, 1991))
    tiles["75.0"] = conflicting

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert "different set of time keys" in str(excinfo.value)


def test_duplicate_center_elevation_mismatch_rejected(no_cache):
    tiles = default_tiles(years=range(1990, 1992))
    tiles["75.0"] = make_tile(
        LATS, (71.875, 72.5), years=range(1990, 1992), elevation=1234.5
    )

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert "different elevation" in str(excinfo.value)


def test_missing_interior_cell_breaks_regular_grid(no_cache):
    tiles = {
        "69.0": make_tile(LATS, TILE1_LONS, years=range(1990, 1992),
                          skip_centers={(39.5, 70.625)}),
        "75.0": make_tile(LATS, TILE2_LONS, years=range(1990, 1992)),
    }
    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert "irregular regional grid" in str(excinfo.value)


def test_irregular_spacing_rejected(no_cache):
    tiles = {
        "69.0": make_tile(LATS, TILE1_LONS, years=range(1990, 1992)),
        "75.0": make_tile(LATS, (71.875, 72.6), years=range(1990, 1992)),
    }
    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert "irregular regional longitude spacing" in str(excinfo.value)


def test_single_latitude_row_rejected(no_cache):
    tiles = {
        "69.0": make_tile((39.0,), TILE1_LONS, years=range(1990, 1992)),
        "75.0": make_tile((39.0,), TILE2_LONS, years=range(1990, 1992)),
    }
    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert "at least two latitude rows" in str(excinfo.value)


def test_center_outside_region_bounds_rejected(no_cache):
    tiles = default_tiles(years=range(1990, 1992))
    tiles["75.0"][0]["geometry"]["coordinates"] = [72.5, 38.5, 3000.0]

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert "outside the declared rectangle" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Values: fill, non-finite, invalid entries and keys
# ---------------------------------------------------------------------------


def test_fill_values_omitted_and_all_fill_cell_kept(no_cache):
    all_fill = {
        f"{year}{month:02d}": FILL
        for year in range(1990, 1992)
        for month in range(1, 14)
    }
    tiles = default_tiles(
        years=range(1990, 1992),
        fill_keys={(39.0, 69.375): ("199001", "199002", "199013")},
        overrides={(39.0, 72.5): all_fill},  # one cell entirely filled
    )

    cells, _source = load(regional_transport(tiles))
    merged = cell_map(cells)
    _elevation, values = merged[(39.0, 69.375)]
    assert "199001" not in values and "199002" not in values
    assert "199013" not in values
    assert "199101" in values
    _elevation, boundary_values = merged[(39.0, 72.5)]
    assert boundary_values == {}  # cell kept in the grid, no valid values


def test_whole_response_all_fill_rejected(no_cache):
    tiles = default_tiles(
        fill_keys={
            (lat, lon): tuple(f"{year}{month:02d}"
                              for year in range(1990, 1992)
                              for month in range(1, 14))
            for lat in LATS for lon in TILE1_LONS
        },
        years=range(1990, 1992),
    )
    tiles["75.0"] = make_tile(LATS, TILE2_LONS, years=range(1990, 1992),
                              fill_keys={
                                  (lat, lon): tuple(f"{year}{month:02d}"
                                                    for year in range(1990, 1992)
                                                    for month in range(1, 14))
                                  for lat in LATS for lon in TILE2_LONS
                              })

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(tiles))
    assert excinfo.value.code == "POWER_UNAVAILABLE"


def test_invalid_value_types_and_keys_ignored(no_cache):
    tiles = default_tiles(years=range(1990, 1992))
    feature = tiles["69.0"][0]
    values = feature["properties"]["parameter"]["T2M"]
    values["199001"] = "3.2"          # string -> omitted
    values["199002"] = True           # boolean -> omitted
    values["199003"] = float("nan")   # non-finite -> omitted
    values["202514"] = 1.0            # month 14 -> ignored
    values["199000"] = 2.0            # month 00 -> ignored
    values["abcd01"] = 3.0            # not YYYYMM -> ignored
    values["1991"] = 4.0              # not six digits -> ignored

    cells, _source = load(regional_transport(tiles))
    merged = cell_map(cells)
    _elevation, first_values = merged[(39.0, 69.375)]
    for key in ("199001", "199002", "199003", "202514", "199000", "abcd01", "1991"):
        assert key not in first_values
    assert "199101" in first_values


def test_coverage_outside_1990_2025_ignored(no_cache):
    tiles = default_tiles(years=range(1990, 1992))
    feature = tiles["69.0"][0]
    values = feature["properties"]["parameter"]["T2M"]
    values["198912"] = 1.0
    values["202601"] = 2.0

    cells, _source = load(regional_transport(tiles))
    merged = cell_map(cells)
    _elevation, first_values = merged[(39.0, 69.375)]
    assert "198912" not in first_values
    assert "202601" not in first_values


# ---------------------------------------------------------------------------
# Header, units and payload structure validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload_kwargs, expected_fragment",
    [
        ({"time_standard": "UTC"}, "time_standard"),
        ({"unit": "K"}, "unexpected unit"),
        ({"fill_value": -9999.0}, "fill_value"),
        ({"start": "19890101"}, "coverage"),
        ({"end": "20241231"}, "coverage"),
        ({"api_version": ""}, "api version"),
        ({"sources": []}, "dataset source attribution"),
        ({"drop_header": True}, "header"),
        ({"drop_units": True}, "units metadata"),
        ({"response_type": "Feature"}, "FeatureCollection"),
    ],
)
def test_header_and_payload_validation_rejects(no_cache, payload_kwargs, expected_fragment):
    tiles = default_tiles(years=range(1990, 1992))
    tiles["75.0"] = make_tile(LATS, TILE2_LONS, years=range(1990, 1992))
    broken = make_payload(tiles["75.0"], **payload_kwargs)

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport({"69.0": make_payload(tiles["69.0"]), "75.0": broken}))
    assert excinfo.value.code == "POWER_UNAVAILABLE"
    assert expected_fragment in str(excinfo.value)


def test_empty_features_rejected(no_cache):
    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(
            {"69.0": make_payload([]), "75.0": make_payload([])}
        ))
    assert "no features" in str(excinfo.value)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda feature: feature.update(type="Point"),
        lambda feature: feature.update(geometry={"type": "Polygon", "coordinates": []}),
        lambda feature: feature.update(
            geometry={"type": "Point", "coordinates": [69.375]}
        ),
        lambda feature: feature["geometry"].update(
            coordinates=[69.375, "not-a-number", 3000.0]
        ),
        lambda feature: feature.update(properties={}),
        lambda feature: feature["properties"].update(parameter={}),
    ],
)
def test_malformed_feature_rejected(no_cache, mangle):
    tiles = default_tiles(years=range(1990, 1992))
    mangle(tiles["75.0"][0])

    with pytest.raises(RegionalError) as excinfo:
        load(regional_transport(
            {
                "69.0": make_payload(tiles["69.0"]),
                "75.0": make_payload(tiles["75.0"]),
            }
        ))
    assert excinfo.value.code == "POWER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Transport errors
# ---------------------------------------------------------------------------


def test_http_error_status_rejected(no_cache):
    tiles = default_tiles(years=range(1990, 1992))
    transport = regional_transport(
        {"69.0": tiles["69.0"], "75.0": tiles["75.0"]}, status=503
    )
    with pytest.raises(RegionalError) as excinfo:
        load(transport)
    assert "HTTP 503" in str(excinfo.value)


def test_non_json_content_rejected(no_cache):
    transport = regional_transport(default_tiles(years=range(1990, 1992)),
                                   content="<html>gateway error</html>")
    with pytest.raises(RegionalError) as excinfo:
        load(transport)
    assert "non-JSON" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expected_code",
    [
        ({"region_id": "france"}, "INVALID_REQUEST"),
        ({"region_id": ""}, "INVALID_REQUEST"),
        ({"region_id": None}, "INVALID_REQUEST"),
        ({"parameter": "T2M_MAX"}, "INVALID_REQUEST"),
        ({"parameter": "t2m"}, "INVALID_REQUEST"),
        ({"parameter": None}, "INVALID_REQUEST"),
        ({"start_year": 1989}, "INVALID_REQUEST"),
        ({"end_year": 2026}, "INVALID_REQUEST"),
        ({"start_year": 2000.0}, "INVALID_REQUEST"),
        ({"start_year": True}, "INVALID_REQUEST"),
        ({"end_year": "2020"}, "INVALID_REQUEST"),
        ({"start_year": 2001, "end_year": 2000}, "INVALID_REQUEST"),
    ],
)
def test_invalid_request_input_rejected(no_cache, kwargs, expected_code):
    call_kwargs = {"region_id": "kg-surroundings", "parameter": "T2M",
                   "start_year": 1990, "end_year": 2025}
    call_kwargs.update(kwargs)
    with pytest.raises(RegionalError) as excinfo:
        load_regional_cells(transport=regional_transport({}), **call_kwargs)
    assert excinfo.value.code == expected_code


# ---------------------------------------------------------------------------
# Raw cache behaviour
# ---------------------------------------------------------------------------


def test_cache_roundtrip_reuses_raw_files(cache_dir):
    first_capture = []
    transport = regional_transport(default_tiles(), capture=first_capture)
    cells_first, source_first = load(transport)

    assert len(first_capture) == 2
    files = sorted(cache_dir.glob("earthscope-power-monthly-regional-v1-*.json"))
    assert len(files) == 2
    entries = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert all(
        entry["kind"] == "earthscope-power-monthly-regional"
        and entry["schema_version"] == 1
        and entry["parameter"] == "T2M"
        and entry["request_url"] in source_first["request_urls"]
        and isinstance(entry["retrieved_at"], str)
        and isinstance(entry["features"], list)
        for entry in entries
    )

    second_capture = []
    cells_second, source_second = load(
        regional_transport(default_tiles(), capture=second_capture)
    )

    assert second_capture == []  # no network at all
    assert source_second["cached"] is True
    assert source_second["tile_cache_states"] == [True, True]
    assert source_second["retrieved_at"] == source_first["retrieved_at"]
    assert cells_second == cells_first


def test_year_range_change_reuses_raw_cache(cache_dir):
    first_capture = []
    load(regional_transport(default_tiles(), capture=first_capture))
    assert len(first_capture) == 2

    second_capture = []
    cells, source = load(
        regional_transport(default_tiles(), capture=second_capture),
        start_year=1995,
        end_year=2000,
    )

    assert second_capture == []  # fixed coverage keeps the raw cache valid
    assert source["cached"] is True
    assert source["start_year"] == 1995 and source["end_year"] == 2000
    merged = cell_map(cells)
    for _elevation, values in merged.values():
        assert set(values) == {
            f"{year}{month:02d}" for year in range(1995, 2001)
            for month in range(1, 14)
        }


def test_unusable_or_corrupt_cache_entry_falls_back_to_network(cache_dir):
    load(regional_transport(default_tiles()))
    for path in cache_dir.glob("*.json"):
        path.write_text("{not json", encoding="utf-8")

    capture = []
    cells, source = load(regional_transport(default_tiles(), capture=capture))

    assert len(capture) == 2  # refetched over the network
    assert source["cached"] is False
    assert len(cells) == 18


def test_stale_schema_cache_entry_falls_back_to_network(cache_dir):
    load(regional_transport(default_tiles()))
    for path in cache_dir.glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["schema_version"] = 99
        path.write_text(json.dumps(entry), encoding="utf-8")

    capture = []
    _cells, source = load(regional_transport(default_tiles(), capture=capture))
    assert len(capture) == 2
    assert source["cached"] is False


def test_expired_raw_cache_refetches_source(cache_dir):
    load(regional_transport(default_tiles()))
    for path in cache_dir.glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["retrieved_at"] = "2020-01-01T00:00:00Z"
        path.write_text(json.dumps(entry), encoding="utf-8")
    capture = []
    _cells, source = load(regional_transport(default_tiles(), capture=capture))
    assert len(capture) == 2
    assert source["cached"] is False


def test_cache_write_is_atomic_and_leaves_no_temp_files(cache_dir):
    load(regional_transport(default_tiles()))

    files = list(cache_dir.glob("*.json"))
    assert len(files) == 2
    assert list(cache_dir.glob("*.tmp")) == []
    for path in files:
        assert json.loads(path.read_text(encoding="utf-8"))["kind"] == (
            "earthscope-power-monthly-regional"
        )


def test_no_cache_dir_means_no_disk_cache(no_cache):
    capture = []
    load(regional_transport(default_tiles(), capture=capture))
    capture.clear()

    cells, source = load(regional_transport(default_tiles(), capture=capture))
    assert len(capture) == 2  # second build refetches without a cache dir
    assert source["cached"] is False
    assert source["tile_cache_states"] == [False, False]


# ---------------------------------------------------------------------------
# Rate limit gap between uncached tile fetches
# ---------------------------------------------------------------------------


def test_gap_only_between_two_uncached_fetches(no_cache, monkeypatch):
    events = []
    monkeypatch.setattr(regional_data, "_sleep",
                        lambda seconds: events.append(("sleep", seconds)))
    transport = regional_transport(default_tiles(), events=events)

    load(transport)

    assert events == [
        ("request", "69.0"),
        ("sleep", INTER_TILE_DELAY_SECONDS),
        ("request", "75.0"),
    ]


def test_no_gap_when_second_tile_is_cached(cache_dir, monkeypatch):
    sleeps = []
    monkeypatch.setattr(regional_data, "_sleep", lambda seconds: sleeps.append(seconds))
    load(regional_transport(default_tiles()))
    assert sleeps == [INTER_TILE_DELAY_SECONDS]  # both tiles uncached initially

    sleeps.clear()
    load(regional_transport(default_tiles()))
    assert sleeps == []  # everything cached: no NASA call, no gap


def test_no_gap_when_first_tile_is_cached(cache_dir, monkeypatch):
    load(regional_transport(default_tiles()))
    # Remove only the second tile's cache entry so the next build fetches it.
    for path in cache_dir.glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        if "longitude-min=75" in entry["request_url"]:
            path.unlink()

    sleeps = []
    monkeypatch.setattr(regional_data, "_sleep", lambda seconds: sleeps.append(seconds))
    capture = []
    _cells, source = load(regional_transport(default_tiles(), capture=capture))

    assert len(capture) == 1
    assert "longitude-min=75" in str(capture[0].url)
    assert sleeps == []  # previous tile was a cache hit: no uncached pair
    assert source["tile_cache_states"] == [True, False]
    assert source["cached"] is False


# ---------------------------------------------------------------------------
# In-process per-key request deduplication
# ---------------------------------------------------------------------------


def test_concurrent_builds_share_the_uncached_fetch(no_cache):
    entered = threading.Semaphore(0)
    permission = threading.Semaphore(0)
    lock = threading.Lock()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(f"{float(request.url.params.get('longitude-min')):.1f}")
        entered.release()
        assert permission.acquire(timeout=10), "handler never released"
        features = default_tiles()[f"{float(request.url.params.get('longitude-min')):.1f}"]
        payload = make_payload(features)
        return httpx.Response(
            200, content=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    results = {}

    def worker(key):
        try:
            if key == "a":
                results[key] = load(httpx.MockTransport(handler))
            else:
                results[key] = load(httpx.MockTransport(handler), start_year=2000, end_year=2001)
        except BaseException as exc:  # pragma: no cover - surfaced below
            results[key] = exc

    thread_a = threading.Thread(target=worker, args=("a",))
    thread_a.start()
    assert entered.acquire(timeout=10), "first tile request never started"

    # Thread B must join the in-flight fetch instead of issuing its own.
    thread_b = threading.Thread(target=worker, args=("b",))
    thread_b.start()

    permission.release()  # unblock the first tile fetch
    assert entered.acquire(timeout=10), "second tile request never started"
    permission.release()  # unblock the second tile fetch

    thread_a.join(timeout=30)
    thread_b.join(timeout=30)
    assert not thread_a.is_alive() and not thread_b.is_alive()

    assert calls == ["69.0", "75.0"]  # exactly one fetch per tile
    cells_a, source_a = results["a"]
    cells_b, source_b = results["b"]
    assert [(cell.latitude, cell.longitude) for cell in cells_a] == [
        (cell.latitude, cell.longitude) for cell in cells_b
    ]
    assert all(len(cell.values) == 26 for cell in cells_b)
    assert (source_b["start_year"], source_b["end_year"]) == (2000, 2001)
    assert source_a["cached"] is False and source_b["cached"] is False
    assert source_a["retrieved_at"] == source_b["retrieved_at"]


def test_concurrent_waiter_receives_shared_error(no_cache):
    entered = threading.Semaphore(0)
    permission = threading.Semaphore(0)
    calls = []
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(f"{float(request.url.params.get('longitude-min')):.1f}")
        entered.release()
        assert permission.acquire(timeout=10), "handler never released"
        return httpx.Response(503, content=b"unavailable")

    results = {}

    def worker(key):
        try:
            results[key] = load(httpx.MockTransport(handler))
        except RegionalError as exc:
            results[key] = exc

    thread_a = threading.Thread(target=worker, args=("a",))
    thread_a.start()
    assert entered.acquire(timeout=10)

    thread_b = threading.Thread(target=worker, args=("b",))
    thread_b.start()
    permission.release()  # the fetch fails while B waits on the same key

    thread_a.join(timeout=30)
    thread_b.join(timeout=30)
    assert not thread_a.is_alive() and not thread_b.is_alive()

    assert calls == ["69.0"]  # only one underlying attempt for the shared key
    for result in results.values():
        assert isinstance(result, RegionalError)
        assert result.code == "POWER_UNAVAILABLE"
        assert result.retryable is True
        assert "HTTP 503" in str(result)
