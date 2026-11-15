"""Tests for backend/app/power.py.

All upstream responses are synthetic and injected through ``httpx.MockTransport``;
no test touches the network. Cache paths are isolated to temporary directories via
``EARTHSCOPE_CACHE_DIR``. The payload shape mirrors the verified live NASA POWER
Daily Point sample (see repository ``.tools/power-probe.json`` and docs/PROGRESS.md):
top-level ``header`` (sources, fill_value, time_standard, start, end) and
top-level ``parameters`` with per-parameter units.
"""

import json
import os
import re
from datetime import datetime

import httpx
import pytest

from app.power import PARAMETER_UNITS, PowerError, VARIABLE_TO_PARAMETER, load_daily

ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def make_payload(
    parameter="T2M",
    start="19900101",
    end="19901231",
    days=None,
    units=None,
    fill_value=-999.0,
    sources=None,
    time_standard="UTC",
    drop_header=False,
    drop_units=False,
    drop_data=False,
):
    days = {"19900101": 1.5, "19900102": 2.25, "19900103": -0.75} if days is None else days
    default_units = {"T2M": "C", "PRECTOTCORR": "mm/day"}
    payload = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [74.6, 42.87]},
        "properties": {"parameter": {parameter: days}},
        "header": {
            "title": "NASA/POWER Source Native Resolution Daily Data",
            "api": {"version": "v2.10.3", "name": "POWER Daily API"},
            "sources": ["MERRA2"] if sources is None else sources,
            "fill_value": fill_value,
            "time_standard": time_standard,
            "start": start,
            "end": end,
        },
        "parameters": {
            "T2M": {"units": "C", "longname": "Temperature at 2 Meters"},
            "T2M_MAX": {"units": "C", "longname": "Temperature at 2 Meters Maximum"},
            "T2M_MIN": {"units": "C", "longname": "Temperature at 2 Meters Minimum"},
            "PRECTOTCORR": {"units": "mm/day", "longname": "Precipitation Corrected"},
        },
        "messages": [],
    }
    if drop_header:
        del payload["header"]
    if drop_units:
        del payload["parameters"]
    elif units is not None:
        payload["parameters"][parameter] = {"units": units, "longname": "x"}
    if drop_data:
        payload["properties"]["parameter"] = {}
    return payload


def mock_transport(payload, status=200, content=None, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        body = content if content is not None else json.dumps(payload)
        return httpx.Response(
            status,
            content=body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    return httpx.MockTransport(handler)


def load(transport, *, latitude=42.87, longitude=74.6, variable="temperature",
         start_year=1990, end_year=1990):
    return load_daily(latitude, longitude, variable, start_year, end_year,
                      transport=transport)


@pytest.fixture()
def no_cache(monkeypatch):
    monkeypatch.delenv("EARTHSCOPE_CACHE_DIR", raising=False)


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    path = tmp_path / "cache"
    monkeypatch.setenv("EARTHSCOPE_CACHE_DIR", str(path))
    return path


def test_temperature_happy_path_and_request_url(no_cache):
    capture = []
    daily, source = load(mock_transport(make_payload(), capture=capture))

    assert daily == {"19900101": 1.5, "19900102": 2.25, "19900103": -0.75}
    url = str(capture[0].url)
    assert source["request_url"] == url
    assert "community=AG" in url
    assert "parameters=T2M" in url
    assert "start=19900101" in url
    assert "end=19901231" in url
    assert "format=JSON" in url
    assert "time-standard=UTC" in url
    assert "user=EARTHSCOPE" in url
    assert "latitude=42.87" in url and "longitude=74.6" in url


def test_source_metadata_contract(no_cache):
    _, source = load(mock_transport(make_payload()))

    assert source == {
        "name": "NASA POWER",
        "parameter": "T2M",
        "daily_unit": "C",
        "dataset": "MERRA2",
        "time_standard": "UTC",
        "request_url": source["request_url"],
        "retrieved_at": source["retrieved_at"],
        "cached": False,
    }
    assert ISO_UTC_RE.match(source["retrieved_at"])
    assert datetime.strptime(source["retrieved_at"], "%Y-%m-%dT%H:%M:%SZ")


def test_precipitation_fill_nan_and_negative_days_omitted(no_cache):
    days = {
        "19900101": 3.59,
        "19900102": -999.0,        # fill value -> omitted
        "19900103": -0.5,          # negative precipitation -> omitted
        "19900104": None,          # null -> omitted
        "19900105": "0.4",         # non-numeric type -> omitted
        "19900106": True,          # non-numeric type -> omitted
        "19900108": 0.0,
        "19901399": 1.0,           # invalid calendar date key -> omitted
        "1990099": 2.0,            # not 8 digits -> omitted
        "not-a-date": 1.0,         # malformed key -> omitted
        "19900109": 0.01,
    }
    daily, source = load(
        mock_transport(make_payload(parameter="PRECTOTCORR", days=days)),
        variable="precipitation",
    )

    assert daily == {
        "19900101": 3.59,
        "19900108": 0.0,
        "19900109": 0.01,
    }
    assert source["parameter"] == "PRECTOTCORR"
    assert source["daily_unit"] == "mm/day"
    assert all(re.match(r"^\d{8}$", key) for key in daily)


def test_string_daily_values_are_omitted(no_cache):
    days = {"19900101": 1.0, "19900102": "2.0", "19900103": [1.0]}
    daily, _ = load(mock_transport(make_payload(days=days)))
    assert daily == {"19900101": 1.0}


def test_all_values_invalid_raises(no_cache):
    days = {"19900101": -999.0, "19900102": None, "19900103": "x"}
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(days=days)))
    assert "no valid daily values" in str(excinfo.value)


def test_wrong_units_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(units="K")))
    assert "unexpected unit 'K'" in str(excinfo.value)


def test_missing_header_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(drop_header=True)))
    assert "header" in str(excinfo.value)


def test_non_utc_time_standard_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(time_standard="LST")))
    assert "UTC" in str(excinfo.value)


def test_missing_source_attribution_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(sources=[])))
    assert "attribution" in str(excinfo.value)


def test_truncated_header_coverage_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(end="19900105")))
    assert "coverage" in str(excinfo.value)


def test_non_numeric_fill_value_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(fill_value="-999")))
    assert "fill_value" in str(excinfo.value)


def test_missing_units_metadata_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(drop_units=True)))
    assert "units" in str(excinfo.value)


def test_missing_parameter_data_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(drop_data=True)))
    assert "missing daily data" in str(excinfo.value)


def test_http_error_status_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(), status=500))
    assert "HTTP 500" in str(excinfo.value)


def test_non_json_body_rejected(no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(mock_transport(make_payload(), content="<html>error</html>"))
    assert "non-JSON" in str(excinfo.value)


def test_transport_failure_rejected(no_cache):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PowerError) as excinfo:
        load(httpx.MockTransport(handler))
    assert "NASA POWER request failed" in str(excinfo.value)


def test_invalid_request_coordinates_and_years(no_cache):
    cases = [
        dict(latitude=95.0),
        dict(latitude=-90.5),
        dict(longitude=200.0),
        dict(variable="humidity"),
        dict(start_year=1991, end_year=1990),
        dict(start_year="1990"),
        dict(end_year=1990.0),
        dict(latitude="42.87"),
    ]
    for kwargs in cases:
        with pytest.raises(PowerError) as excinfo:
            load(mock_transport(make_payload()), **kwargs)
        assert excinfo.value.code == "INVALID_REQUEST"


def test_coordinate_boundaries_accepted(no_cache):
    for kwargs in (dict(latitude=90.0), dict(latitude=-90.0),
                   dict(longitude=180.0), dict(longitude=-180.0)):
        daily, _ = load(mock_transport(make_payload()), **kwargs)
        assert daily


def test_cache_roundtrip(cache_dir):
    capture = []
    transport = mock_transport(make_payload(), capture=capture)

    daily_first, source_first = load(transport)
    assert source_first["cached"] is False
    assert capture
    cached_files = list(cache_dir.glob("*.json"))
    assert len(cached_files) == 1

    daily_second, source_second = load(transport)

    assert daily_second == daily_first
    assert source_second["cached"] is True
    assert source_second["retrieved_at"] == source_first["retrieved_at"]
    assert source_second["request_url"] == source_first["request_url"]
    assert len(capture) == 1  # second call served from cache, no second fetch


def test_corrupt_cache_entry_falls_back_to_network(cache_dir):
    _, source_first = load(mock_transport(make_payload()))
    (cache_file,) = cache_dir.glob("*.json")
    cache_file.write_text("{not json", encoding="utf-8")

    capture = []
    daily, source = load(mock_transport(make_payload(), capture=capture))

    assert daily == {"19900101": 1.5, "19900102": 2.25, "19900103": -0.75}
    assert source["cached"] is False
    assert source["request_url"] == source_first["request_url"]
    assert len(capture) == 1


def test_cache_schema_mismatch_falls_back_to_network(cache_dir):
    load(mock_transport(make_payload()))
    (cache_file,) = cache_dir.glob("*.json")
    entry = json.loads(cache_file.read_text(encoding="utf-8"))
    entry["schema_version"] = 999
    cache_file.write_text(json.dumps(entry), encoding="utf-8")

    capture = []
    _, source = load(mock_transport(make_payload(), capture=capture))

    assert source["cached"] is False
    assert len(capture) == 1


def test_cache_write_failure_is_tolerated(cache_dir, monkeypatch):
    def broken_replace(src, dst, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)

    daily, source = load(mock_transport(make_payload()))

    assert daily == {"19900101": 1.5, "19900102": 2.25, "19900103": -0.75}
    assert source["cached"] is False
    assert not list(cache_dir.glob("*.tmp*"))  # temp file cleaned up


def test_cache_directory_is_created(cache_dir):
    assert not cache_dir.exists()
    _, source = load(mock_transport(make_payload()))
    assert cache_dir.is_dir()
    assert source["cached"] is False


def test_cache_dir_pointing_at_a_file_is_tolerated(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("EARTHSCOPE_CACHE_DIR", str(blocker))

    daily, source = load(mock_transport(make_payload()))

    assert daily
    assert source["cached"] is False


def test_no_cache_env_means_no_disk_cache(no_cache, tmp_path):
    capture = []
    transport = mock_transport(make_payload(), capture=capture)

    _, first = load(transport)
    _, second = load(transport)

    assert first["cached"] is False and second["cached"] is False
    assert len(capture) == 2  # both calls went to the transport


# --- Extreme-weather variables (docs/EXTREMES-API.md data role) -------------
#
# The live NASA POWER Daily Point payload for the 1990-2025 window at both
# probe points (.tools/extreme-probes/{bishkek,reykjavik}.json) carries
# T2M_MAX/T2M_MIN exactly like T2M: header units "C", fill_value -999.0,
# time_standard UTC, 13149 nonfill daily values per parameter, and no
# explicit grid-spacing field on the point payload. The synthetic payloads
# below mirror that shape; every test is deterministic and offline.


@pytest.mark.parametrize(
    "variable,parameter",
    [("temperature_max", "T2M_MAX"), ("temperature_min", "T2M_MIN")],
)
def test_extrema_variable_mapping(variable, parameter):
    # The documented data-role contract: existing precipitation is reused,
    # T2M_MAX/T2M_MIN are added, T2M stays as-is.
    assert VARIABLE_TO_PARAMETER == {
        "temperature": "T2M",
        "temperature_max": "T2M_MAX",
        "temperature_min": "T2M_MIN",
        "precipitation": "PRECTOTCORR",
    }
    assert PARAMETER_UNITS[parameter] == "C"
    assert PARAMETER_UNITS["PRECTOTCORR"] == "mm/day"


@pytest.mark.parametrize(
    "variable,parameter",
    [("temperature_max", "T2M_MAX"), ("temperature_min", "T2M_MIN")],
)
def test_extrema_happy_path_and_request_url(variable, parameter, no_cache):
    days = {"19900101": 31.4, "19900102": -12.05, "19900103": 0.0}
    capture = []
    daily, source = load(
        mock_transport(make_payload(parameter=parameter, days=days), capture=capture),
        variable=variable,
    )

    assert daily == days  # negative values are valid temperatures, kept
    url = str(capture[0].url)
    assert source["request_url"] == url
    assert f"parameters={parameter}" in url
    assert "community=AG" in url and "time-standard=UTC" in url
    assert source["parameter"] == parameter
    assert source["daily_unit"] == "C"
    assert source["name"] == "NASA POWER"


@pytest.mark.parametrize(
    "variable,parameter",
    [("temperature_max", "T2M_MAX"), ("temperature_min", "T2M_MIN")],
)
def test_extrema_units_are_validated(variable, parameter, no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(
            mock_transport(make_payload(parameter=parameter, units="K")),
            variable=variable,
        )
    assert f"unexpected unit 'K' for {parameter}" in str(excinfo.value)


@pytest.mark.parametrize(
    "variable,parameter",
    [("temperature_max", "T2M_MAX"), ("temperature_min", "T2M_MIN")],
)
def test_extrema_missing_units_metadata_rejected(variable, parameter, no_cache):
    with pytest.raises(PowerError) as excinfo:
        load(
            mock_transport(make_payload(parameter=parameter, drop_units=True)),
            variable=variable,
        )
    assert "units" in str(excinfo.value)


def test_temperature_max_fill_and_invalid_days_omitted(no_cache):
    # Mirrors the live fill_value -999.0 from .tools/extreme-probes.
    days = {
        "19900101": 30.05,
        "19900102": -999.0,   # header fill value -> omitted
        "19900103": None,     # null -> omitted
        "19900104": "5.0",    # non-numeric type -> omitted
        "19900105": True,     # non-numeric type -> omitted
        "19900106": -5.0,     # negative temperature is valid, kept
        "19901399": 1.0,      # invalid calendar date key -> omitted
        "19900107": 0.0,
    }
    daily, source = load(
        mock_transport(make_payload(parameter="T2M_MAX", days=days)),
        variable="temperature_max",
    )

    assert daily == {"19900101": 30.05, "19900106": -5.0, "19900107": 0.0}
    assert source["parameter"] == "T2M_MAX"
    assert source["daily_unit"] == "C"


@pytest.mark.parametrize(
    "variable,parameter",
    [("temperature_max", "T2M_MAX"), ("temperature_min", "T2M_MIN")],
)
def test_extrema_cache_roundtrip(variable, parameter, cache_dir):
    capture = []
    transport = mock_transport(
        make_payload(parameter=parameter, days={"19900101": 21.5}), capture=capture
    )

    daily_first, source_first = load(transport, variable=variable)
    assert source_first["cached"] is False
    assert source_first["parameter"] == parameter
    assert source_first["daily_unit"] == "C"
    assert len(capture) == 1

    daily_second, source_second = load(transport, variable=variable)

    assert daily_second == daily_first
    assert source_second["cached"] is True
    assert source_second["retrieved_at"] == source_first["retrieved_at"]
    assert source_second["parameter"] == parameter
    assert source_second["daily_unit"] == "C"
    assert len(capture) == 1  # cache hit: no second network fetch


def test_extrema_cache_entry_with_wrong_units_falls_back_to_network(cache_dir):
    # A cache entry whose stored header units no longer match the contract
    # must be rejected and refetched, never trusted.
    _, _ = load(
        mock_transport(make_payload(parameter="T2M_MAX", days={"19900101": 21.5})),
        variable="temperature_max",
    )
    (cache_file,) = cache_dir.glob("*.json")
    entry = json.loads(cache_file.read_text(encoding="utf-8"))
    entry["parameters"]["T2M_MAX"]["units"] = "K"
    cache_file.write_text(json.dumps(entry), encoding="utf-8")

    capture = []
    daily, source = load(
        mock_transport(
            make_payload(parameter="T2M_MAX", days={"19900101": 21.5}), capture=capture
        ),
        variable="temperature_max",
    )

    assert daily == {"19900101": 21.5}
    assert source["cached"] is False
    assert source["daily_unit"] == "C"
    assert len(capture) == 1  # refetched after rejecting the poisoned entry


def test_extrema_cache_hit_omits_fill_days(cache_dir):
    # Cache hits run the same fill-value/units validation as network reads:
    # a stored raw -999 day must not surface as a data point.
    _, _ = load(
        mock_transport(
            make_payload(parameter="T2M_MIN", days={"19900101": -999.0, "19900102": 2.0}),
        ),
        variable="temperature_min",
    )
    (cache_file,) = cache_dir.glob("*.json")
    entry = json.loads(cache_file.read_text(encoding="utf-8"))
    assert entry["daily"]["19900101"] == -999.0  # raw fill preserved on disk

    daily, source = load(
        mock_transport(make_payload(parameter="T2M_MIN", days={"19900101": -999.0, "19900102": 2.0})),
        variable="temperature_min",
    )

    assert daily == {"19900102": 2.0}  # fill day omitted on the cache hit
    assert source["cached"] is True
    assert source["daily_unit"] == "C"


def test_extrema_cache_entry_with_missing_daily_falls_back_to_network(cache_dir):
    _, _ = load(
        mock_transport(make_payload(parameter="T2M_MAX", days={"19900101": 1.0})),
        variable="temperature_max",
    )
    (cache_file,) = cache_dir.glob("*.json")
    entry = json.loads(cache_file.read_text(encoding="utf-8"))
    entry["daily"] = {}
    cache_file.write_text(json.dumps(entry), encoding="utf-8")

    capture = []
    daily, source = load(
        mock_transport(make_payload(parameter="T2M_MAX", days={"19900101": 1.0}), capture=capture),
        variable="temperature_max",
    )

    assert daily == {"19900101": 1.0}
    assert source["cached"] is False
    assert len(capture) == 1


def test_precipitation_contract_is_unchanged(no_cache):
    # Reused event: negative precipitation stays omitted while negative
    # temperatures stay kept; the URL/units contract is untouched.
    capture = []
    daily, source = load(
        mock_transport(
            make_payload(
                parameter="PRECTOTCORR", days={"19900101": 20.5, "19900102": -0.1}
            ),
            capture=capture,
        ),
        variable="precipitation",
    )

    assert daily == {"19900101": 20.5}
    assert "parameters=PRECTOTCORR" in str(capture[0].url)
    assert source["parameter"] == "PRECTOTCORR"
    assert source["daily_unit"] == "mm/day"


def test_non_utf8_cache_entry_is_refetched_and_repaired(cache_dir):
    load(mock_transport(make_payload(days={"19900101": 21.5})))
    (cache_file,) = cache_dir.glob("*.json")
    cache_file.write_bytes(b"\xff\xfe")
    capture = []
    daily, source = load(mock_transport(make_payload(days={"19900101": 23.0}), capture=capture))
    assert daily == {"19900101": 23.0}
    assert source["cached"] is False
    assert len(capture) == 1
    _, cached = load(mock_transport(make_payload(days={"19900101": 24.0}), capture=capture))
    assert cached["cached"] is True
    assert len(capture) == 1
