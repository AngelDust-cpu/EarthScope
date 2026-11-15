"""Contract tests for the EarthScope 2.8 climate profile backend.

Covered behaviour (``docs/CLIMATE-PROFILE-CONTRACT.md``):

* **Four-call bound and reuse.** One profile performs exactly four
  ``load_daily`` calls (T2M, T2M_MAX, T2M_MIN, PRECTOTCORR) over one shared
  expanded span, and the annual, anomaly, seasonal and extreme sections reuse
  those mappings instead of fetching again.
* **Partial failures.** A failing parameter or a failing section yields
  ``status="error"`` for the affected sections only, keeps HTTP 200, and never
  imputes a value.  If every network section fails the route answers HTTP 503.
* **Validation.** Coordinates, years, baseline years and thresholds are
  rejected before any POWER request, in the existing error envelope.
* **Nulls and coverage.** An incomplete year keeps its day diagnostics with a
  null value and is excluded from means, trends and anomalies.
* **CSV.** ``GET /api/climate-profile.csv`` exposes units, thresholds and
  provenance and copies the validated values only.
* **PDF.** ``Accept: application/pdf`` returns one report; the merged
  pypdf composition and the ReportLab-only fallback are both exercised.

Every daily series in this module is a deterministic synthetic fixture labelled
``SYNTHETIC TEST FIXTURE - not NASA data``.  ``app.profile.load_daily`` is
replaced by a local fake (and an autouse guard fails any test that forgets it),
so no test contacts NASA POWER and no synthetic data enters the runtime path.
"""

from __future__ import annotations

import copy
import csv
import io
import math
import re
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
    raise ModuleNotFoundError(
        "pypdf is required for backend/tests/test_profile.py; ask the project "
        "owner to add it to the dev dependency group."
    ) from exc

import app.main as main
from app import profile, profile_pdf, profile_schemas
from app.power import PowerError
from app.profile_schemas import (
    PROFILE_SECTIONS,
    ClimateProfileResponse,
)

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URL = "https://example.invalid/earthscope-profile-synthetic-test"
RETRIEVED_AT = "2026-11-15T00:00:00Z"

PARAMETER_BY_VARIABLE = {
    "temperature": "T2M",
    "temperature_max": "T2M_MAX",
    "temperature_min": "T2M_MIN",
    "precipitation": "PRECTOTCORR",
}
UNIT_BY_PARAMETER = {
    "T2M": "C",
    "T2M_MAX": "C",
    "T2M_MIN": "C",
    "PRECTOTCORR": "mm/day",
}

#: 1998-2005 display period, 1999-2004 baseline.  The shared span is
#: 1997-2005: 1997 supplies the December of the first winter and the baseline
#: stays inside the display period, so the span is driven by both rules.
DEFAULT_PARAMS: dict[str, Any] = {
    "latitude": 42.8746,
    "longitude": 74.5698,
    "start_year": 1998,
    "end_year": 2005,
    "baseline_start_year": 1999,
    "baseline_end_year": 2004,
    "hot_threshold": 30,
    "cold_threshold": 0,
    "rain_threshold": 20,
}

TOP_LEVEL_KEYS = {
    "latitude",
    "longitude",
    "start_year",
    "end_year",
    "baseline_start_year",
    "baseline_end_year",
    "thresholds",
    "sections",
    "summary",
    "limitations",
}
SECTION_KEYS = {"status", "data", "message"}


# --- Synthetic fixture ----------------------------------------------------
def _day_value(variable: str, day: date) -> float:
    doy = day.timetuple().tm_yday
    season = math.sin(2.0 * math.pi * (doy - 105) / 365.25)
    base = 14.0 + 9.0 * season + 0.05 * (day.year - 2000)
    if variable == "temperature":
        value = base
    elif variable == "temperature_max":
        value = base + 9.5
    elif variable == "temperature_min":
        value = base - 9.5
    else:
        value = max(
            0.0,
            12.0
            + 18.0 * math.sin(2.0 * math.pi * (doy - 20) / 365.25)
            + 5.0 * math.sin(doy * 0.7),
        )
    return round(value, 3)


def _daily(
    variable: str,
    start_year: int,
    end_year: int,
    drop: set[str] | None = None,
) -> dict[str, float]:
    values: dict[str, float] = {}
    day = date(start_year, 1, 1)
    last = date(end_year, 12, 31)
    while day <= last:
        key = day.strftime("%Y%m%d")
        if drop is None or key not in drop:
            values[key] = _day_value(variable, day)
        day += timedelta(days=1)
    return values


def _source(variable: str) -> dict[str, Any]:
    parameter = PARAMETER_BY_VARIABLE[variable]
    return {
        "name": "NASA POWER (synthetic profile fixture)",
        "parameter": parameter,
        "daily_unit": UNIT_BY_PARAMETER[parameter],
        "dataset": SYNTHETIC_DATASET,
        "time_standard": "UTC",
        "request_url": f"{SYNTHETIC_URL}/{parameter}",
        "retrieved_at": RETRIEVED_AT,
        "cached": False,
    }


class FakePower:
    """Deterministic ``load_daily`` double that records every call."""

    def __init__(
        self,
        *,
        fail: set[str] | None = None,
        drop: dict[str, set[str]] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = set(fail or ())
        self.drop = dict(drop or {})

    def __call__(
        self,
        *,
        latitude: float,
        longitude: float,
        variable: str,
        start_year: int,
        end_year: int,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        self.calls.append(
            {
                "latitude": latitude,
                "longitude": longitude,
                "variable": variable,
                "start_year": start_year,
                "end_year": end_year,
            }
        )
        if variable in self.fail:
            raise PowerError(
                f"synthetic POWER failure for {variable}", "POWER_UNAVAILABLE"
            )
        return (
            _daily(variable, start_year, end_year, self.drop.get(variable)),
            _source(variable),
        )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly instead of reaching NASA POWER if a test forgets the fake."""

    def guard(**kwargs: Any) -> None:
        raise AssertionError(f"unexpected NASA POWER call: {sorted(kwargs)}")

    monkeypatch.setattr(profile, "load_daily", guard)


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakePower) -> FakePower:
    monkeypatch.setattr(profile, "load_daily", fake)
    return fake


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def _params(**overrides: Any) -> dict[str, Any]:
    values = dict(DEFAULT_PARAMS)
    values.update(overrides)
    return values


def _profile(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    params: dict[str, Any] | None = None,
    fake: FakePower | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[Any, FakePower]:
    power = _install(monkeypatch, fake or FakePower())
    response = client.get(
        profile.PROFILE_PATH,
        params=params or _params(),
        headers=headers or {},
    )
    return response, power


def _error(response: Any) -> dict[str, str]:
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message"}
    assert isinstance(error["message"], str) and error["message"]
    return error


def _pdf_text(content: bytes) -> tuple[int, str]:
    reader = PdfReader(io.BytesIO(content))
    raw = " ".join((page.extract_text() or "") for page in reader.pages)
    return len(reader.pages), re.sub(r"\s+", " ", raw)


# --- Registration and shape ----------------------------------------------
def test_profile_routes_are_registered_on_the_application() -> None:
    paths = main.app.openapi()["paths"]
    assert profile.PROFILE_PATH in paths
    assert profile.PROFILE_CSV_PATH in paths
    assert set(paths[profile.PROFILE_PATH]) == {"get"}
    assert set(paths[profile.PROFILE_CSV_PATH]) == {"get"}


def test_profile_response_matches_the_contract(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, power = _profile(client, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == TOP_LEVEL_KEYS
    assert set(payload["sections"]) == set(PROFILE_SECTIONS)
    assert payload["thresholds"] == {
        "hot_days": 30.0,
        "cold_nights": 0.0,
        "heavy_rain": 20.0,
    }

    for name in PROFILE_SECTIONS[:-1]:
        section = payload["sections"][name]
        assert set(section) == SECTION_KEYS
        assert section["status"] == "ok", (name, section["message"])
        assert section["message"] is None
        data = section["data"]
        assert data["latitude"] == pytest.approx(DEFAULT_PARAMS["latitude"])
        assert data["longitude"] == pytest.approx(DEFAULT_PARAMS["longitude"])
        assert data["start_year"] == DEFAULT_PARAMS["start_year"]
        assert data["end_year"] == DEFAULT_PARAMS["end_year"]
        assert data["source"]["name"].startswith("NASA POWER")
        assert data["source"]["dataset"] == SYNTHETIC_DATASET
        assert isinstance(data["warnings"], list)

    assert payload["sections"]["temperature"]["data"]["variable"] == "temperature"
    assert payload["sections"]["precipitation"]["data"]["variable"] == "precipitation"
    anomalies = payload["sections"]["anomalies"]["data"]
    assert anomalies["variable"] == "temperature"
    assert anomalies["baseline_start_year"] == DEFAULT_PARAMS["baseline_start_year"]
    assert anomalies["baseline_end_year"] == DEFAULT_PARAMS["baseline_end_year"]
    assert payload["sections"]["seasonal"]["data"]["period"] == "all"
    assert payload["sections"]["hot_days"]["data"]["event"] == "hot_days"
    assert payload["sections"]["cold_nights"]["data"]["event"] == "cold_nights"
    assert payload["sections"]["heavy_rain"]["data"]["event"] == "heavy_rain"

    heatwaves = payload["sections"]["heatwaves"]
    assert heatwaves["status"] == "unavailable"
    assert heatwaves["data"] is None
    assert "heatwaves" in heatwaves["message"]

    assert payload["summary"]
    assert payload["limitations"]
    assert isinstance(payload["limits"] if "limits" in payload else [], list)

    # The round trip proves the DTO accepts exactly what the route emits.
    ClimateProfileResponse.model_validate(payload)

    assert len(power.calls) == 4


# --- Four-call bound and reuse -------------------------------------------
def test_profile_uses_four_loads_over_one_shared_span(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # end_year 2001 while the baseline ends in 2004 and start_year 1998 needs
    # the December of 1997, so the shared span proves both expansion rules.
    params = _params(start_year=1998, end_year=2001)
    response, power = _profile(client, monkeypatch, params=params)

    assert response.status_code == 200
    assert len(power.calls) == 4
    assert [call["variable"] for call in power.calls] == list(profile.PROFILE_VARIABLES)
    assert {call["variable"] for call in power.calls} == set(
        PARAMETER_BY_VARIABLE
    )
    assert {(call["start_year"], call["end_year"]) for call in power.calls} == {
        (1997, 2004)
    }
    for call in power.calls:
        assert call["latitude"] == pytest.approx(params["latitude"])
        assert call["longitude"] == pytest.approx(params["longitude"])

    sections = response.json()["sections"]
    assert (
        sections["temperature"]["data"]["source"]["request_url"]
        == sections["anomalies"]["data"]["source"]["request_url"]
        == sections["seasonal"]["data"]["source"]["request_url"]
    ), "T2M must be loaded once and reused by annual, anomaly and seasonal sections"
    assert (
        sections["precipitation"]["data"]["source"]["request_url"]
        == sections["heavy_rain"]["data"]["source"]["request_url"]
    ), "PRECTOTCORR must be loaded once and reused by the rain sections"


def test_profile_span_includes_baseline_before_the_display_period(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = _params(
        start_year=1998,
        end_year=2000,
        baseline_start_year=1990,
        baseline_end_year=1999,
    )
    response, power = _profile(client, monkeypatch, params=params)

    assert response.status_code == 200
    assert {(call["start_year"], call["end_year"]) for call in power.calls} == {
        (1990, 2000)
    }


# --- Partial failures ----------------------------------------------------
def test_profile_partial_failure_is_isolated_to_its_sections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, power = _profile(
        client, monkeypatch, fake=FakePower(fail={"temperature_max"})
    )

    assert response.status_code == 200
    payload = response.json()
    hot = payload["sections"]["hot_days"]
    assert hot["status"] == "error"
    assert hot["data"] is None
    assert "temperature_max" in hot["message"]

    for name in ("temperature", "precipitation", "anomalies", "seasonal",
                 "cold_nights", "heavy_rain"):
        assert payload["sections"][name]["status"] == "ok", name
    assert payload["sections"]["heatwaves"]["status"] == "unavailable"

    # No retry, no extra fetch: the budget stays at four calls.
    assert len(power.calls) == 4

    assert any("Жаркие дни" in line for line in payload["summary"])
    assert any(
        "Ответ частичный" in line and "жаркие дни" in line
        for line in payload["limitations"]
    )
    # Only completed checks are reported as numbers.
    assert not any(
        "Жаркие дни" in line and "дн./год" in line for line in payload["summary"]
    )


def test_profile_all_network_sections_failed_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, power = _profile(
        client,
        monkeypatch,
        fake=FakePower(fail=set(PARAMETER_BY_VARIABLE)),
    )

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "POWER_UNAVAILABLE"
    assert "synthetic POWER failure" in error["message"]
    assert len(power.calls) == 4


def test_profile_section_contract_error_stays_a_section_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import analysis

    def broken_build(**kwargs: Any) -> None:
        raise analysis.DataContractError("synthetic contract violation")

    monkeypatch.setattr(analysis, "build_trend_response", broken_build)
    response, _power = _profile(client, monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    for name in ("temperature", "precipitation"):
        section = payload["sections"][name]
        assert section["status"] == "error"
        assert section["data"] is None
        assert "synthetic contract violation" in section["message"]
    assert payload["sections"]["anomalies"]["status"] == "ok"


# --- Validation ----------------------------------------------------------
@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"latitude": 91.0}, "latitude"),
        ({"latitude": float("nan")}, "latitude"),
        ({"longitude": float("inf")}, "longitude"),
        ({"start_year": 1980}, "start_year"),
        ({"end_year": 2026}, "end_year"),
        ({"start_year": 2005, "end_year": 2000}, "start_year must be less than"),
        ({"baseline_start_year": 2005, "baseline_end_year": 2000}, "baseline"),
        ({"baseline_start_year": 2030}, "baseline_start_year"),
        ({"hot_threshold": 150}, "hot_threshold"),
        ({"cold_threshold": float("inf")}, "cold_threshold"),
        ({"rain_threshold": -1}, "rain_threshold"),
    ],
)
def test_profile_validation_rejects_bad_input_before_any_fetch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    fragment: str,
) -> None:
    power = _install(monkeypatch, FakePower())
    response = client.get(profile.PROFILE_PATH, params=_params(**overrides))

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "INVALID_REQUEST"
    assert fragment in error["message"]
    assert power.calls == []


def test_profile_csv_route_uses_the_same_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    power = _install(monkeypatch, FakePower())
    response = client.get(
        profile.PROFILE_CSV_PATH, params=_params(start_year=2020, end_year=2010)
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "INVALID_REQUEST"
    assert power.calls == []


# --- Nulls and coverage --------------------------------------------------
def test_profile_incomplete_year_keeps_nulls_and_diagnostics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = {"19980615"}
    fake = FakePower(drop={"temperature": missing})
    response, _power = _profile(client, monkeypatch, fake=fake)

    assert response.status_code == 200
    payload = response.json()
    temperature = payload["sections"]["temperature"]["data"]
    by_year = {point["year"]: point for point in temperature["series"]}
    assert by_year[1998]["value"] is None
    assert by_year[1998]["valid_days"] == 364
    assert by_year[1998]["expected_days"] == 365
    assert by_year[1998]["fitted"] is None
    assert sum(1 for point in temperature["series"] if point["value"] is None) == 1
    assert temperature["trend"]["years_used"] == len(temperature["series"]) - 1

    anomalies = payload["sections"]["anomalies"]["data"]
    assert anomalies["baseline_mean"] is not None
    anomaly_by_year = {point["year"]: point for point in anomalies["series"]}
    assert anomaly_by_year[1998]["anomaly"] is None
    assert anomaly_by_year[1999]["anomaly"] is not None

    seasonal = payload["sections"]["seasonal"]["data"]
    summer = next(entry for entry in seasonal["periods"] if entry["period"] == "summer")
    assert next(p for p in summer["series"] if p["year"] == 1998)["value"] is None
    spring = next(entry for entry in seasonal["periods"] if entry["period"] == "spring")
    assert next(p for p in spring["series"] if p["year"] == 1998)["value"] is not None

    assert any("Неполное покрытие" in line for line in payload["limitations"])


def test_profile_incomplete_baseline_suppresses_anomalies(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePower(drop={"temperature": {"20020801"}})
    response, _power = _profile(client, monkeypatch, fake=fake)

    assert response.status_code == 200
    anomalies = response.json()["sections"]["anomalies"]
    assert anomalies["status"] == "ok"
    assert anomalies["data"]["baseline_mean"] is None
    assert all(point["anomaly"] is None for point in anomalies["data"]["series"])
    summary = response.json()["summary"]
    assert any("неполная" in line for line in summary)


# --- CSV -----------------------------------------------------------------
def _csv_rows(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    assert lines and all(not line.startswith(",") for line in lines[:3])
    data = [line for line in lines if not line.startswith("#")]
    return list(csv.DictReader(data))


def test_profile_csv_exposes_units_provenance_and_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_response, _power = _profile(client, monkeypatch)
    assert json_response.status_code == 200
    payload = json_response.json()

    response = client.get(profile.PROFILE_CSV_PATH, params=_params())
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "text/csv"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert disposition.endswith('.csv"')

    text = response.content.decode("utf-8-sig")
    header_block = [line for line in text.splitlines() if line.startswith("#")]
    assert header_block[0].startswith("# EarthScope climate profile CSV")
    joined = " ".join(header_block)
    assert "latitude=42.8746" in joined
    assert "baseline=1999-2004" in joined
    assert "°C" in joined and "mm/day" in joined
    assert "units:" in joined
    for parameter in ("T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"):
        assert f"parameter={parameter}" in joined
    assert "retrieved_at=2026-11-15T00:00:00Z" in joined
    assert "request_url=" in joined

    rows = _csv_rows(text)
    assert list(rows[0]) == list(profile.CSV_COLUMNS)
    assert {row["section"] for row in rows} == set(PROFILE_SECTIONS)

    status_rows = {
        row["section"]: row for row in rows if row["statistic"] == "section_status"
    }
    assert set(status_rows) == set(PROFILE_SECTIONS)
    assert status_rows["heatwaves"]["status"] == "unavailable"
    assert status_rows["heatwaves"]["note"]
    assert status_rows["temperature"]["status"] == "ok"

    temperature = [
        row
        for row in rows
        if row["section"] == "temperature" and row["statistic"] == "annual_mean"
    ]
    assert [int(row["year"]) for row in temperature] == list(
        range(DEFAULT_PARAMS["start_year"], DEFAULT_PARAMS["end_year"] + 1)
    )
    assert {row["unit"] for row in temperature} == {"°C"}
    sample = next(row for row in temperature if row["year"] == "2000")
    reported = next(
        point
        for point in payload["sections"]["temperature"]["data"]["series"]
        if point["year"] == 2000
    )
    assert float(sample["value"]) == pytest.approx(reported["value"], rel=1e-8)
    assert int(sample["valid_days"]) == reported["valid_days"]

    precipitation = [
        row
        for row in rows
        if row["section"] == "precipitation" and row["statistic"] == "annual_sum"
    ]
    assert {row["unit"] for row in precipitation} == {"mm"}

    anomaly_rows = [row for row in rows if row["section"] == "anomalies"]
    baseline = next(
        row for row in anomaly_rows if row["statistic"] == "baseline_mean"
    )
    assert baseline["year"] == "1999-2004"
    assert baseline["unit"] == "°C"
    assert float(baseline["value"]) == pytest.approx(
        payload["sections"]["anomalies"]["data"]["baseline_mean"], rel=1e-8
    )
    assert {row["unit"] for row in anomaly_rows if row["statistic"] == "anomaly"} == {
        "°C"
    }

    seasonal_rows = [row for row in rows if row["section"] == "seasonal" and row["statistic"] != "section_status"]
    assert {row["series"] for row in seasonal_rows} == {
        "winter",
        "spring",
        "summer",
        "autumn",
    }

    extremes = [row for row in rows if row["section"] == "cold_nights"]
    assert {row["unit"] for row in extremes if row["statistic"] == "event_days"} == {
        "days"
    }
    assert {row["threshold"] for row in extremes if row["statistic"] == "event_days"} == {
        "0"
    }
    assert any(
        row["note"].startswith("T2M_MIN <") for row in extremes if row["statistic"] == "event_days"
    )


def test_profile_csv_reports_partial_failure_without_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _power = _profile(
        client, monkeypatch, fake=FakePower(fail={"temperature_min"})
    )
    assert response.status_code == 200

    csv_response = client.get(profile.PROFILE_CSV_PATH, params=_params())
    assert csv_response.status_code == 200
    rows = _csv_rows(csv_response.content.decode("utf-8-sig"))

    cold = [row for row in rows if row["section"] == "cold_nights"]
    assert len(cold) == 1
    assert cold[0]["statistic"] == "section_status"
    assert cold[0]["status"] == "error"
    assert "temperature_min" in cold[0]["note"]
    assert not any(
        row["section"] == "hot_days" and row["status"] == "error" for row in rows
    )


def test_profile_csv_is_unavailable_when_the_profile_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakePower(fail=set(PARAMETER_BY_VARIABLE)))
    response = client.get(profile.PROFILE_CSV_PATH, params=_params())

    assert response.status_code == 503
    assert _error(response)["code"] == "POWER_UNAVAILABLE"


# --- PDF -----------------------------------------------------------------
def test_profile_pdf_is_one_merged_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, power = _profile(
        client, monkeypatch, headers={"Accept": "application/pdf"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('.pdf"')
    assert response.content.startswith(b"%PDF-")
    assert len(power.calls) == 4

    pages, text = _pdf_text(response.content)
    assert pages >= 2, "the merged profile must contain cover plus section reports"
    assert "Климатический профиль" in text
    assert "Ограничения и допущения" in text
    assert "T2M_MAX > 30" in text
    assert "42.8746" in text
    assert "pypdf" in text
    assert "NASA POWER" in text
    assert "Волны жары" in text


def test_profile_pdf_falls_back_to_one_reportlab_document(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_pdf, "_pypdf_module", lambda: None)
    response, _power = _profile(
        client, monkeypatch, headers={"Accept": "application/pdf"}
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    pages, text = _pdf_text(response.content)
    assert pages >= 1
    assert "не установлен" in text
    assert "Детали разделов" in text
    assert "Климатический профиль" in text
    assert "T2M_MAX > 30" in text


def test_profile_pdf_names_the_heatwave_dependency(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_pdf, "_pypdf_module", lambda: None)
    response, _power = _profile(
        client, monkeypatch, headers={"Accept": "application/pdf"}
    )

    _pages, text = _pdf_text(response.content)
    assert "Волны жары не рассчитываются" in text
    assert "heatwaves" in text


def test_profile_pdf_failure_uses_the_error_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_profile: ClimateProfileResponse) -> bytes:
        raise profile_pdf.ReportEngineUnavailable("синтетический сбой рендера")

    monkeypatch.setattr(profile_pdf, "render_profile_pdf", broken)
    response, _power = _profile(
        client, monkeypatch, headers={"Accept": "application/pdf"}
    )

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "PDF_UNAVAILABLE"
    assert "синтетический сбой рендера" in error["message"]

    # The JSON route still works: the PDF branch is isolated.
    monkeypatch.undo()
    json_response, _power = _profile(client, monkeypatch)
    assert json_response.status_code == 200


def test_profile_pdf_without_section_reports_is_still_delivered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_report(kind: str, payload: dict) -> bytes:
        raise RuntimeError("synthetic render_report failure")

    monkeypatch.setattr(main.report_pdf if hasattr(main, "report_pdf") else profile_pdf, "_pypdf_module", lambda: _NoPypdf())

    from app import report_pdf

    monkeypatch.setattr(report_pdf, "render_report", broken_report)
    response, _power = _profile(
        client, monkeypatch, headers={"Accept": "application/pdf"}
    )

    assert response.status_code == 200
    pages, text = _pdf_text(response.content)
    assert pages >= 1
    assert "Отчёты разделов не удалось сформировать" in text
    assert "Детали разделов" in text


class _NoPypdf:
    """Never used: the monkeypatch above simply removes the merge path."""


# --- Schema invariants ---------------------------------------------------
@pytest.fixture(scope="module")
def profile_payload() -> dict[str, Any]:
    """One validated payload shared by the read-only schema checks."""

    fake = FakePower()
    original = profile.load_daily
    profile.load_daily = fake
    try:
        with TestClient(main.app) as test_client:
            response = test_client.get(profile.PROFILE_PATH, params=_params())
    finally:
        profile.load_daily = original
    assert response.status_code == 200
    return response.json()


def test_profile_payload_satisfies_its_own_model(
    profile_payload: dict[str, Any],
) -> None:
    model = ClimateProfileResponse.model_validate(profile_payload)
    assert model.sections.ok_network_count == len(PROFILE_SECTIONS) - 1
    assert not model.sections.all_network_failed
    assert model.model_dump(mode="json")["thresholds"] == profile_payload["thresholds"]


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (
            lambda p: p["sections"]["temperature"].update(status="ok", data=None),
            "requires data",
        ),
        (
            lambda p: p["sections"]["temperature"].update(message="ok but noisy"),
            "must not carry a message",
        ),
        (
            lambda p: p["sections"]["hot_days"].update(
                status="error", data=None, message=None
            ),
            "requires a message",
        ),
        (
            lambda p: p["sections"]["heatwaves"].update(
                status="error", message="synthetic"
            ),
            "heatwaves must be 'unavailable'",
        ),
        (
            lambda p: p["sections"]["hot_days"]["data"].update(threshold=99.0),
            "must use the requested threshold",
        ),
        (
            lambda p: p["sections"]["temperature"]["data"].update(latitude=10.0),
            "must use the requested point",
        ),
        (
            lambda p: p["sections"]["anomalies"]["data"].update(baseline_end_year=1990),
            "baseline",
        ),
        (
            lambda p: p["thresholds"].update(heavy_rain=-5),
            "heavy_rain must be between",
        ),
        (
            lambda p: p.update(summary=[""]),
            "must be non-empty",
        ),
        (
            lambda p: p["sections"].pop("heavy_rain"),
            "heavy_rain",
        ),
    ],
)
def test_profile_model_rejects_incoherent_payloads(
    profile_payload: dict[str, Any],
    mutate: Callable[[dict[str, Any]], Any],
    fragment: str,
) -> None:
    payload = copy.deepcopy(profile_payload)
    mutate(payload)

    with pytest.raises(ValidationError) as excinfo:
        ClimateProfileResponse.model_validate(payload)
    assert fragment in str(excinfo.value)


def test_profile_model_rejects_a_profile_without_successful_sections(
    profile_payload: dict[str, Any],
) -> None:
    payload = copy.deepcopy(profile_payload)
    for name in PROFILE_SECTIONS[:-1]:
        payload["sections"][name] = {
            "status": "error",
            "data": None,
            "message": "synthetic",
        }

    with pytest.raises(ValidationError, match="at least one successful"):
        ClimateProfileResponse.model_validate(payload)


def test_profile_schema_defaults_match_the_documented_thresholds() -> None:
    thresholds = profile_schemas.ProfileThresholds()
    assert (thresholds.hot_days, thresholds.cold_nights, thresholds.heavy_rain) == (
        30.0,
        0.0,
        20.0,
    )
