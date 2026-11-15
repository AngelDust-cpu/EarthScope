"""Contract tests for the PDF report rendering of the analysis endpoints.

These tests pin the content negotiation contract for the six endpoints that
accept ``Accept: application/pdf``:

* ``GET /api/trend``
* ``POST /api/compare``
* ``GET /api/anomalies``
* ``GET /api/seasonal``
* ``GET /api/extremes``
* ``GET /api/maps/world/cell``

Contract under test (implementation is owned by the backend team):

1.  A request with ``Accept: application/pdf`` returns HTTP 200 with
    ``Content-Type: application/pdf`` and a body that starts with the
    ``%PDF-`` magic and parses with ``pypdf`` to at least one page.
2.  The PDF is regenerated from the *validated server output* - the same
    response model that would be serialised as JSON - not from raw upstream
    data and not from a cached artefact. The tests therefore request each
    endpoint twice (default JSON, then PDF) from the same deterministic
    fixture and assert that tokens from the validated JSON payload (source
    metadata, years, statistic values, warnings) appear in the extracted
    PDF text.
3.  The default JSON contract is unchanged: no ``Accept`` header,
    ``Accept: application/json`` and ``Accept: */*`` all return the same
    JSON response as before. Error responses (validation, unknown route,
    upstream failure) keep the existing JSON error envelope even when
    ``Accept: application/pdf`` is sent - the PDF path never wraps an
    error in a document.

Every POWER response in this module is a synthetic, deterministic fixture
labelled ``SYNTHETIC TEST FIXTURE - not NASA data``. ``app.main.load_daily``
is monkeypatched before any request can run, and the world-cell fixture is
substituted at ``app.global_map.cell_result`` with the archive accessors
patched to fail loudly, so no test contacts NASA POWER or reads the local
world archive. No synthetic data enters the runtime application path.

Assumptions the implementation owner must confirm (see review report):

*   Error responses stay JSON (the PDF branch only renders a validated 2xx
    response model).
*   No byte-level determinism is required (PDF generators may embed a
    creation timestamp); only extracted text is asserted.
*   Layout, wording and ``Content-Disposition`` are not pinned; only the
    presence of the validated values is.
*   ``pypdf`` is required to run this module; dependencies are managed by
    the project owner.
"""

from __future__ import annotations

import calendar
import io
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
    raise ModuleNotFoundError(
        "pypdf is required for backend/tests/test_report_pdf.py; "
        "ask the project owner to add it to the dev dependency group."
    ) from exc

import app.main as main
from app import extremes
from app.power import PowerError

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URL = "https://example.invalid/earthscope-synthetic-test"
RETRIEVED_AT = "2026-11-14T12:34:56Z"

POINT_A = {"latitude": 42.87, "longitude": 74.60}
POINT_B = {"latitude": 64.147, "longitude": -21.942}
SOURCE_URL_A = "https://example.invalid/earthscope-synthetic-test-a"
SOURCE_URL_B = "https://example.invalid/earthscope-synthetic-test-b"

TREND_DEFAULT_KEYS = {
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

# ---------------------------------------------------------------------------
# Deterministic fixtures (never NASA observations)
# ---------------------------------------------------------------------------


def _source(
    variable: str, cached: bool = False, parameter: str | None = None
) -> dict[str, object]:
    parameter = parameter or ("T2M" if variable == "temperature" else "PRECTOTCORR")
    return {
        "name": "NASA POWER",
        "parameter": parameter,
        "daily_unit": "C" if parameter in ("T2M", "T2M_MAX", "T2M_MIN") else "mm/day",
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


def _build_daily(
    variable: str,
    annual_values: Mapping[int, float],
    *,
    missing: Sequence[str] = (),
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
    return daily


def _install_power(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    annual_values: Mapping[int, float],
    *,
    missing: Sequence[str] = (),
    cached: bool = False,
) -> list[dict[str, object]]:
    daily = _build_daily(variable, annual_values, missing=missing)
    source_payload = _source(variable, cached)
    calls: list[dict[str, object]] = []

    def fake_load_daily(
        **kwargs: object,
    ) -> tuple[dict[str, float], dict[str, object]]:
        calls.append(dict(kwargs))
        return dict(daily), dict(source_payload)

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    return calls


def _install_compare_power(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    annual_a: Mapping[int, float],
    annual_b: Mapping[int, float],
) -> None:
    daily_a = _build_daily(variable, annual_a)
    daily_b = _build_daily(variable, annual_b)
    source_a = _source(variable) | {"request_url": SOURCE_URL_A}
    source_b = _source(variable) | {"request_url": SOURCE_URL_B}

    def fake_load_daily(
        **kwargs: object,
    ) -> tuple[dict[str, float], dict[str, object]]:
        point = {"latitude": kwargs.get("latitude"), "longitude": kwargs.get("longitude")}
        if point == POINT_A:
            return dict(daily_a), dict(source_a)
        if point == POINT_B:
            return dict(daily_b), dict(source_b)
        raise AssertionError(f"unexpected comparison point: {point!r}")

    monkeypatch.setattr(main, "load_daily", fake_load_daily)


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


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def _assert_error(response: object, status_code: int, code: str) -> dict[str, str]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message"}
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    return payload["error"]


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------


def _pdf_text(response: Any) -> str:
    """Assert the response is a well-formed PDF and return its text."""

    content_type = response.headers.get("content-type", "").lower()
    assert content_type.startswith("application/pdf"), (
        f"expected a PDF media type, got {content_type!r}"
    )
    assert response.content.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(response.content))
    assert len(reader.pages) >= 1
    raw = " ".join((page.extract_text() or "") for page in reader.pages)
    return re.sub(r"\s+", " ", raw)


def _pdf_response(response: Any) -> tuple[Any, str]:
    assert response.status_code == 200
    return response, _pdf_text(response)


def _number_variants(value: float) -> set[str]:
    """Common reasonable renderings of one statistic for PDF text checks."""

    v = float(value)
    variants = {
        str(v),
        repr(v),
        f"{v:.1f}",
        f"{v:.2f}",
        f"{v:.3f}",
        f"{v:.4f}",
        f"{v:.2g}",
        f"{v:.4g}",
        f"{v:.6g}",
        f"{v:,.1f}",
        f"{v:,.2f}",
    }
    if v == int(v):
        variants.add(str(int(v)))
        variants.add(f"{v:,.0f}")
    return {item for item in variants if item}


def _assert_number_in_text(text: str, value: float | int | None) -> None:
    assert value is not None
    variants = _number_variants(float(value))
    # The Russian report uses decimal commas and non-breaking group spaces.
    variants |= {item.replace(",", "\u00a0").replace(".", ",") for item in variants}
    assert any(variant in text for variant in variants), (
        f"none of the renderings {sorted(variants)!r} of {value!r} "
        "appears in the PDF text"
    )


def _assert_validated_source_tokens(text: str, payload: Mapping[str, Any]) -> None:
    source = payload["source"]
    for token in (
        str(source["name"]),
        str(source["parameter"]),
        str(source["dataset"]),
        str(source["retrieved_at"]),
    ):
        assert token in text, f"source token {token!r} is missing from the PDF text"


# ---------------------------------------------------------------------------
# GET /api/trend
# ---------------------------------------------------------------------------


def test_trend_pdf_is_regenerated_from_validated_output(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(year - 1990 + 10) for year in range(1990, 1995)}

    _install_power(monkeypatch, "temperature", annual)
    json_response = client.get("/api/trend", params=_params())
    assert json_response.status_code == 200
    payload = json_response.json()

    _install_power(monkeypatch, "temperature", annual)
    pdf_response = client.get(
        "/api/trend", params=_params(), headers={"Accept": "application/pdf"}
    )
    _, text = _pdf_response(pdf_response)

    _assert_validated_source_tokens(text, payload)
    assert str(payload["start_year"]) in text
    assert str(payload["end_year"]) in text
    assert payload["variable"] in text
    assert payload["aggregation"] in text
    _assert_number_in_text(text, payload["series"][2]["value"])  # 1992 -> 12.0
    _assert_number_in_text(text, payload["trend"]["per_decade"])
    _assert_number_in_text(text, payload["trend"]["ci95_low"])
    _assert_number_in_text(text, payload["trend"]["ci95_high"])


def test_trend_pdf_flags_the_incomplete_year(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(year - 1990) for year in range(1990, 1996)}

    _install_power(monkeypatch, "temperature", annual, missing=("19930615",))
    json_response = client.get("/api/trend", params=_params(end_year=1995))
    assert json_response.status_code == 200
    payload = json_response.json()
    assert any("1993" in warning for warning in payload["warnings"])

    _install_power(monkeypatch, "temperature", annual, missing=("19930615",))
    pdf_response = client.get(
        "/api/trend",
        params=_params(end_year=1995),
        headers={"Accept": "application/pdf"},
    )
    _, text = _pdf_response(pdf_response)
    # The incomplete year must be surfaced, not silently filled in.
    assert "1993" in text
    _assert_number_in_text(text, payload["trend"]["per_decade"])


# ---------------------------------------------------------------------------
# POST /api/compare
# ---------------------------------------------------------------------------


def test_compare_pdf_contains_both_points_and_the_difference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual_a = {year: float(year - 1990 + 10) for year in range(1990, 1995)}
    annual_b = {year: 10.0 + 0.5 * (year - 1990) for year in range(1990, 1995)}

    _install_compare_power(monkeypatch, "temperature", annual_a, annual_b)
    json_response = client.post("/api/compare", json=_compare_body())
    assert json_response.status_code == 200
    payload = json_response.json()

    _install_compare_power(monkeypatch, "temperature", annual_a, annual_b)
    pdf_response = client.post(
        "/api/compare",
        json=_compare_body(),
        headers={"Accept": "application/pdf"},
    )
    _, text = _pdf_response(pdf_response)

    # Each point keeps its own source metadata in the report.
    assert SOURCE_URL_A in text
    assert SOURCE_URL_B in text
    assert payload["comparison"]["method"] in text
    assert "Descriptive" in text  # descriptive only, never an inferential claim
    _assert_number_in_text(text, payload["point_a"]["trend"]["per_decade"])
    _assert_number_in_text(text, payload["point_b"]["trend"]["per_decade"])
    _assert_number_in_text(text, payload["comparison"]["slope_difference_per_decade"])
    _assert_number_in_text(text, payload["comparison"]["years_used"])


def test_compare_pdf_keeps_null_difference_visible_when_years_are_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    years = list(range(1990, 1998))
    annual_a = {year: float(year - 1990) for year in years}
    annual_b = {year: 0.4 * (year - 1990) for year in years}
    daily_a = _build_daily("temperature", annual_a, missing=("19920615",))
    daily_b = _build_daily("temperature", annual_b, missing=("19940615",))
    source_a = _source("temperature") | {"request_url": SOURCE_URL_A}
    source_b = _source("temperature") | {"request_url": SOURCE_URL_B}

    def fake_load_daily(
        **kwargs: object,
    ) -> tuple[dict[str, float], dict[str, object]]:
        point = {"latitude": kwargs.get("latitude"), "longitude": kwargs.get("longitude")}
        if point == POINT_A:
            return dict(daily_a), dict(source_a)
        return dict(daily_b), dict(source_b)

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    json_response = client.post("/api/compare", json=_compare_body(end_year=1997))
    assert json_response.status_code == 200
    payload = json_response.json()
    assert payload["comparison"]["years_used"] == 6  # six common complete years

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    pdf_response = client.post(
        "/api/compare",
        json=_compare_body(end_year=1997),
        headers={"Accept": "application/pdf"},
    )
    _, text = _pdf_response(pdf_response)
    _assert_number_in_text(text, payload["comparison"]["years_used"])
    _assert_number_in_text(text, payload["comparison"]["slope_difference_per_decade"])


# ---------------------------------------------------------------------------
# GET /api/anomalies
# ---------------------------------------------------------------------------


def test_anomalies_pdf_contains_baseline_and_display_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 10.0 + 0.5 * (year - 1985) for year in range(1985, 1995)}

    _install_power(monkeypatch, "temperature", annual)
    json_response = client.get(
        "/api/anomalies",
        params=_params(baseline_start_year=1985, baseline_end_year=1989),
    )
    assert json_response.status_code == 200
    payload = json_response.json()

    _install_power(monkeypatch, "temperature", annual)
    pdf_response = client.get(
        "/api/anomalies",
        params=_params(baseline_start_year=1985, baseline_end_year=1989),
        headers={"Accept": "application/pdf"},
    )
    _, text = _pdf_response(pdf_response)

    _assert_validated_source_tokens(text, payload)
    for year in ("1985", "1989", "1990", "1994"):
        assert year in text
    _assert_number_in_text(text, payload["baseline_mean"])
    _assert_number_in_text(text, payload["baseline_complete_years"])
    _assert_number_in_text(text, payload["series"][0]["value"])
    _assert_number_in_text(text, payload["series"][0]["anomaly"])


# ---------------------------------------------------------------------------
# GET /api/seasonal
# ---------------------------------------------------------------------------


def test_seasonal_pdf_contains_all_four_seasons(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1989 is included because winter 1990 needs the previous December.
    annual = {year: 10.0 + 0.5 * (year - 1990) for year in range(1989, 1995)}

    _install_power(monkeypatch, "temperature", annual)
    json_response = client.get("/api/seasonal", params=_params(period="all"))
    assert json_response.status_code == 200
    payload = json_response.json()

    _install_power(monkeypatch, "temperature", annual)
    pdf_response = client.get(
        "/api/seasonal",
        params=_params(period="all"),
        headers={"Accept": "application/pdf"},
    )
    _, text = _pdf_response(pdf_response)

    _assert_validated_source_tokens(text, payload)
    for period in ("winter", "spring", "summer", "autumn"):
        assert period in text
    for entry in payload["periods"]:
        assert entry["trend"] is not None
        _assert_number_in_text(text, entry["trend"]["per_decade"])


def test_seasonal_pdf_handles_single_month(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: 20.0 + 0.4 * (year - 1990) for year in range(1990, 1995)}
    _install_power(monkeypatch, "temperature", annual)
    json_response = client.get("/api/seasonal", params=_params(period="m07"))
    assert json_response.status_code == 200
    payload = json_response.json()

    _install_power(monkeypatch, "temperature", annual)
    pdf_response = client.get(
        "/api/seasonal",
        params=_params(period="m07"),
        headers={"Accept": "application/pdf"},
    )
    _, text = _pdf_response(pdf_response)
    assert "m07" in text
    _assert_number_in_text(text, payload["periods"][0]["trend"]["per_decade"])


# ---------------------------------------------------------------------------
# GET /api/extremes
# ---------------------------------------------------------------------------


def _extreme_source(event: str) -> dict[str, object]:
    parameter = {
        "hot_days": "T2M_MAX",
        "cold_nights": "T2M_MIN",
        "heavy_rain": "PRECTOTCORR",
    }[event]
    return _source("precipitation", parameter=parameter)


def _event_daily(
    event: str, counts: Mapping[int, int], threshold: float
) -> dict[str, float]:
    def event_value(is_event: bool) -> float:
        if event == "cold_nights":
            return threshold - 1.0 if is_event else threshold
        return threshold + 1.0 if is_event else threshold

    daily: dict[str, float] = {}
    for year, count in counts.items():
        days = list(_year_dates(year))
        if not 0 <= count <= len(days):
            raise ValueError("count must fit within the calendar year")
        for index, day in enumerate(days):
            daily[f"{day:%Y%m%d}"] = event_value(index < count)
    return daily


def _ols(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope /= sum((x - mean_x) ** 2 for x in xs)
    return slope, mean_y - slope * mean_x


class _DescriptiveFit:
    """Deterministic descriptive fit matching the agreed fit_extremes contract."""

    def __call__(
        self, aggregates: Sequence[Any]
    ) -> tuple[dict[str, Any] | None, dict[int, float]]:
        complete = [item for item in aggregates if item.value is not None]
        if len(complete) < 5:
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


def test_extremes_pdf_contains_event_counts_and_trend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = {1990: 10, 1991: 15, 1992: 20, 1993: 25, 1994: 30}
    threshold = 33.0
    daily = _event_daily("heavy_rain", counts, threshold)
    source = _extreme_source("heavy_rain")

    def fake_load_daily(
        **kwargs: object,
    ) -> tuple[dict[str, float], dict[str, object]]:
        return dict(daily), dict(source)

    params = {
        "latitude": 42.87,
        "longitude": 74.60,
        "start_year": 1990,
        "end_year": 1994,
        "event": "heavy_rain",
        "threshold": threshold,
    }

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    monkeypatch.setattr(extremes, "fit_extremes", _DescriptiveFit())
    json_response = client.get("/api/extremes", params=params)
    assert json_response.status_code == 200
    payload = json_response.json()

    from app import report_pdf
    plotted = []
    original_chart = report_pdf._time_chart

    def capture_chart(lines, **kwargs):
        plotted.append(lines)
        return original_chart(lines, **kwargs)

    monkeypatch.setattr(report_pdf, "_time_chart", capture_chart)

    monkeypatch.setattr(main, "load_daily", fake_load_daily)
    monkeypatch.setattr(extremes, "fit_extremes", _DescriptiveFit())
    pdf_response = client.get(
        "/api/extremes", params=params, headers={"Accept": "application/pdf"}
    )
    _, text = _pdf_response(pdf_response)

    _assert_validated_source_tokens(text, payload)
    assert "heavy_rain" in text
    assert "mm/day" in text
    _assert_number_in_text(text, payload["threshold"])
    _assert_number_in_text(text, payload["series"][0]["events"])
    _assert_number_in_text(text, payload["series"][-1]["events"])
    _assert_number_in_text(text, payload["trend"]["per_decade"])
    assert list(plotted[0][1].points) == [
        (row["year"], row["fitted"]) for row in payload["series"]
    ], "Fitted rates already use event days per 365 days"


# ---------------------------------------------------------------------------
# GET /api/maps/world/cell
# ---------------------------------------------------------------------------


def _world_cell_payload() -> dict[str, Any]:
    years = list(range(1990, 2026))
    return {
        "latitude": 0.0,
        "longitude": 0.0,
        "parameter": "PRECTOTCORR",
        "unit": "mm",
        "slope": 1.25,
        "ci_low": 0.5,
        "ci_high": 2.0,
        "p": 0.0034,
        "q": 0.021,
        "version": "SYNTHETIC MERRA-2 TEST",
        "series": [
            {"year": year, "value": 1.0 + 0.1 * (year - 1990)} for year in years
        ],
    }


def _install_world_cell(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    payload = _world_cell_payload()

    def forbidden_archive(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "the world-cell report must not read the local POWER archive; "
            "the deterministic fixture is substituted at global_map.cell_result"
        )

    monkeypatch.setattr(main.global_map, "cell_result", lambda *_args: dict(payload))
    monkeypatch.setattr(main.global_map, "get_global", forbidden_archive)
    monkeypatch.setattr(main.global_map, "archive_path", forbidden_archive)
    return payload


def test_world_cell_pdf_contains_cell_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_world_cell(monkeypatch)
    params = {"row": 180, "col": 288, "variable": "precipitation"}

    json_response = client.get("/api/maps/world/cell", params=params)
    assert json_response.status_code == 200
    payload = json_response.json()
    assert payload["slope"] == pytest.approx(1.25)

    _install_world_cell(monkeypatch)
    pdf_response = client.get(
        "/api/maps/world/cell", params=params, headers={"Accept": "application/pdf"}
    )
    _, text = _pdf_response(pdf_response)

    assert payload["parameter"] in text
    assert payload["unit"] in text
    assert payload["version"] in text
    assert "1990" in text
    assert "2025" in text
    _assert_number_in_text(text, payload["slope"])
    _assert_number_in_text(text, payload["ci_low"])
    _assert_number_in_text(text, payload["ci_high"])
    _assert_number_in_text(text, payload["p"])
    _assert_number_in_text(text, payload["q"])


def test_world_cell_pdf_error_stays_json_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_world_cell(monkeypatch)
    response = client.get(
        "/api/maps/world/cell",
        params={"row": 361, "col": 288, "variable": "precipitation"},
        headers={"Accept": "application/pdf"},
    )
    _assert_error(response, 422, "INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Default JSON behaviour is unchanged
# ---------------------------------------------------------------------------


def test_trend_default_json_is_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    annual = {year: float(year - 1990 + 10) for year in range(1990, 1995)}

    for headers in (None, {"Accept": "application/json"}, {"Accept": "*/*"}):
        _install_power(monkeypatch, "temperature", annual)
        kwargs: dict[str, Any] = {"params": _params()}
        if headers:
            kwargs["headers"] = headers
        response = client.get("/api/trend", **kwargs)
        assert response.status_code == 200
        assert response.headers["content-type"].lower().startswith("application/json")
        payload = response.json()
        assert set(payload) == TREND_DEFAULT_KEYS
        assert payload["trend"]["per_decade"] == pytest.approx(10.0)


def test_world_cell_default_json_is_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _world_cell_payload()
    for headers in (None, {"Accept": "application/json"}, {"Accept": "*/*"}):
        _install_world_cell(monkeypatch)
        kwargs: dict[str, Any] = {
            "params": {"row": 180, "col": 288, "variable": "precipitation"},
        }
        if headers:
            kwargs["headers"] = headers
        response = client.get("/api/maps/world/cell", **kwargs)
        assert response.status_code == 200
        assert response.headers["content-type"].lower().startswith("application/json")
        assert response.json() == payload


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "status_code", "code"),
    [
        (
            "get",
            "/api/trend",
            {"params": {"latitude": 90.5, "longitude": 0.0, "variable": "temperature",
                        "start_year": 1990, "end_year": 1994}},
            422,
            "INVALID_REQUEST",
        ),
        (
            "post",
            "/api/compare",
            {"json": {"point_a": {"latitude": 0.0, "longitude": 0.0},
                      "point_b": {"latitude": 0.0, "longitude": 0.0},
                      "variable": "humidity", "start_year": 1990, "end_year": 1994}},
            422,
            "INVALID_REQUEST",
        ),
        (
            "get",
            "/api/anomalies",
            {"params": {"latitude": 0.0, "longitude": 0.0, "variable": "temperature",
                        "start_year": 1980, "end_year": 1994}},
            422,
            "INVALID_REQUEST",
        ),
        (
            "get",
            "/api/seasonal",
            {"params": {"latitude": 0.0, "longitude": 0.0, "variable": "temperature",
                        "start_year": 1990, "end_year": 1994, "period": "monsoon"}},
            422,
            "INVALID_REQUEST",
        ),
        (
            "get",
            "/api/extremes",
            {"params": {"latitude": 0.0, "longitude": 0.0,
                        "start_year": 1990, "end_year": 1994,
                        "event": "hot_days", "threshold": 500.0}},
            422,
            "INVALID_REQUEST",
        ),
    ],
)
def test_errors_stay_json_even_with_pdf_accept(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    status_code: int,
    code: str,
) -> None:
    annual = {year: 1.0 for year in range(1990, 1995)}
    _install_power(monkeypatch, "temperature", annual)
    response = client.request(
        method, path, headers={"Accept": "application/pdf"}, **kwargs
    )
    _assert_error(response, status_code, code)


def test_unknown_route_stays_json_with_pdf_accept(client: TestClient) -> None:
    response = client.get("/api/not-a-route", headers={"Accept": "application/pdf"})
    _assert_error(response, 404, "NOT_FOUND")


def test_compare_upstream_failure_stays_json_with_pdf_accept(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_load_daily(
        **_kwargs: object,
    ) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("synthetic outage")

    monkeypatch.setattr(main, "load_daily", failing_load_daily)
    response = client.post(
        "/api/compare",
        json=_compare_body(),
        headers={"Accept": "application/pdf"},
    )
    _assert_error(response, 502, "POWER_UNAVAILABLE")
