"""Deterministic contract tests for ``GET /api/heatwaves``.

Every daily series in this module is a synthetic fixture.  ``app.main.load_daily``
is monkeypatched before any request can run, so no test contacts NASA POWER and
no synthetic data enters the runtime ingestion path.

The heatwaves endpoint is owned by the implementation role.  These
tests pin the agreed HTTP contract only; no schema, module or
route internal is imported.  When the implementation lands it MUST satisfy the
contract below or the pinned decisions must be renegotiated with whoever owns
this file.

Pinned contract decisions
-------------------------

* Detection uses T2M_MAX (``temperature_max`` in ``power.load_daily``) with a
  strict ``>`` comparison against ``threshold`` (equality never counts,
  mirroring ``hot_days``).  ``threshold`` defaults to 30 and ``min_duration``
  to 3; ``min_duration`` must be an integer >= 1.
* An *episode* is a run of consecutive UTC calendar days with tmax strictly
  above the threshold, lasting at least ``min_duration`` days.
* Detection runs across Dec 31 inside the requested window.  Episode stats
  are attributed entirely to the episode's start year, never split.
* Outer-window censoring is real: an episode touching the first day of
  ``start_year`` or the last day of ``end_year`` may extend beyond the window.
  The response MUST disclose this in ``warnings``: at least one
  warning must state that a boundary-touching episode may extend
  beyond the window.  Wording and locale are not pinned (the
  implementation reports in Russian).  A clean window carries no such
  warning.
* ``valid_days`` / ``expected_days`` are reported for every year.  A year is
  complete only when all 365/366 UTC days are present.  Incomplete years keep
  the day diagnostics but report null ``count``, ``total_days``,
  ``max_duration``, ``mean_duration`` and ``max_tmax``, and are excluded from
  every trend fit.  A complete year with zero episodes reports ``count`` = 0,
  ``total_days`` = 0, ``max_duration`` = 0, ``mean_duration`` = null and
  ``max_tmax`` = null.
* Per-year aggregates: ``total_days`` is the sum of episode durations starting
  that year; ``mean_duration`` is ``total_days / count``; ``max_tmax`` is the
  highest daily tmax observed across that year's episode days.
* Episode objects carry inclusive ISO ``start``/``end`` (YYYY-MM-DD), integer
  ``duration`` in days, ``max_tmax``, ``mean_tmax`` (mean of daily tmax) and
  ``max_excess`` (max of ``tmax - threshold``).  Episodes are sorted ascending
  by start.
* ``trends`` holds exactly the keys ``count``, ``total_days``,
  ``max_duration``.  Each is null when fewer than 5 complete years are
  available, otherwise ``{per_decade, years_used, residual_lag1}``:
  ``per_decade`` is the descriptive OLS slope times 10 against calendar year,
  ``years_used`` the number of complete years in the fit, and
  ``residual_lag1`` the Pearson correlation of residuals at consecutive
  calendar-year pairs.  Descriptive OLS plus the lag-1 diagnostic only: no
  confidence interval, p-value or significance keys may appear.
* The series contains every requested year in ascending order.  Request
  validation happens before ingestion and returns the shared error envelope
  (422 ``INVALID_REQUEST``).

Open contract clarifications (pinned here so the implementation owner can push
back explicitly):

* A missing day breaks a would-be run (consecutiveness cannot be confirmed);
  it does not silently bridge.  ``test_missing_hot_day_breaks_a_run``.
* Censoring warnings must disclose that a boundary-touching episode may extend beyond the window; wording and locale are not pinned.
* Whether/how episodes that started in an incomplete year surface in the
  episodes list is intentionally not pinned here.
* No threshold bounds are pinned beyond "finite"; NASA POWER working range
  is not re-declared in this test module.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.power import PowerError

SYNTHETIC_DATASET = "SYNTHETIC TEST FIXTURE - not NASA data"
SYNTHETIC_URL = "https://example.invalid/earthscope-heatwaves-synthetic-test"
RETRIEVED_AT = "2026-11-15T08:00:00Z"

RESPONSE_KEYS = {
    "latitude",
    "longitude",
    "start_year",
    "end_year",
    "threshold",
    "min_duration",
    "source",
    "series",
    "episodes",
    "trends",
    "warnings",
}
SERIES_KEYS = {
    "year",
    "count",
    "total_days",
    "max_duration",
    "mean_duration",
    "max_tmax",
    "valid_days",
    "expected_days",
}
EPISODE_KEYS = {"start", "end", "duration", "max_tmax", "mean_tmax", "max_excess"}
TRENDS_KEYS = {"count", "total_days", "max_duration"}
TREND_KEYS = {"per_decade", "years_used", "residual_lag1"}
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

MIN_COMPLETE_YEARS_FOR_TREND = 5
DEFAULT_THRESHOLD = 30.0
DEFAULT_MIN_DURATION = 3


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _source(cached: bool = False) -> dict[str, object]:
    return {
        "name": "NASA POWER",
        "parameter": "T2M_MAX",
        "daily_unit": "C",
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


def _flat_daily(
    start_year: int, end_year: int, *, default: float = 20.0
) -> dict[str, float]:
    daily: dict[str, float] = {}
    for year in range(start_year, end_year + 1):
        for day in _year_dates(year):
            daily[_key(day)] = default
    return daily


def _cast_spell(
    daily: dict[str, float], start: str, temps: Sequence[float]
) -> dict[str, float]:
    current = date.fromisoformat(start)
    for offset, temp in enumerate(temps):
        daily[_key(current + timedelta(days=offset))] = temp
    return daily


def _daily_with(
    spells: Sequence[tuple[str, Sequence[float]]],
    start_year: int = 1990,
    end_year: int = 1994,
    *,
    default: float = 20.0,
    drop: Sequence[str] = (),
) -> dict[str, float]:
    daily = _flat_daily(start_year, end_year, default=default)
    for start, temps in spells:
        _cast_spell(daily, start, temps)
    for token in drop:
        daily.pop(token, None)
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


def _residual_lag1(years: Sequence[float], values: Sequence[float]) -> float | None:
    """Pearson correlation of OLS residuals at consecutive calendar years."""

    slope, intercept = _ols(years, values)
    residuals = [y - (intercept + slope * x) for x, y in zip(years, values, strict=True)]
    pairs = [(residuals[i - 1], residuals[i]) for i in range(1, len(years))
             if years[i] - years[i - 1] == 1]
    if len(pairs) < 4:
        return None
    first = [pair[0] for pair in pairs]
    second = [pair[1] for pair in pairs]
    mean_a = sum(first) / len(first)
    mean_b = sum(second) / len(second)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in pairs)
    var_a = sum((a - mean_a) ** 2 for a in first)
    var_b = sum((b - mean_b) ** 2 for b in second)
    if var_a == 0.0 or var_b == 0.0:
        return None
    return cov / math.sqrt(var_a * var_b)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any accidental upstream call instead of contacting NASA."""

    def forbidden(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise AssertionError(f"unexpected load_daily call: {kwargs!r}")

    monkeypatch.setattr(main, "load_daily", forbidden)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def _install_power(
    monkeypatch: pytest.MonkeyPatch,
    daily: Mapping[str, float],
    *,
    cached: bool = False,
    source: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    payload = dict(source) if source is not None else _source(cached)
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
        "threshold": 30,
        "min_duration": 3,
    }
    params.update(overrides)
    return params


def _default_span_spells() -> list[tuple[str, list[float]]]:
    """Four episodes: 1990 (Jul), 1991->1992 (cross-year), 1993 (Jul), 1994 (Aug)."""

    return [
        ("1990-07-01", [31.0, 31.0, 31.0]),
        ("1991-12-30", [31.0, 32.0, 33.0, 34.0]),
        ("1993-07-10", [35.0, 36.0, 37.0]),
        ("1994-08-01", [31.0, 31.0, 31.0, 31.0, 31.0, 31.0]),
    ]


def _install_default_span(
    monkeypatch: pytest.MonkeyPatch,
    *,
    drop: Sequence[str] = (),
    cached: bool = False,
) -> list[dict[str, object]]:
    daily = _daily_with(_default_span_spells(), drop=drop)
    return _install_power(monkeypatch, daily, cached=cached)


def _assert_error(response: Any, status_code: int, code: str) -> dict[str, str]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message"}
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    return payload["error"]


# ---------------------------------------------------------------------------
# Full-contract fixture: attribution, episodes, trends and warnings
# ---------------------------------------------------------------------------


def test_heatwaves_response_contract_and_cross_year_attribution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_default_span(monkeypatch)

    response = client.get("/api/heatwaves", params=_params())

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == RESPONSE_KEYS
    assert set(payload["source"]) == SOURCE_KEYS

    assert payload["latitude"] == pytest.approx(42.87)
    assert payload["longitude"] == pytest.approx(74.60)
    assert payload["start_year"] == 1990
    assert payload["end_year"] == 1994
    assert payload["threshold"] == pytest.approx(30.0)
    assert payload["min_duration"] == 3
    assert payload["source"]["parameter"] == "T2M_MAX"
    assert payload["source"]["daily_unit"] == "C"
    assert payload["source"]["cached"] is False

    # Series: one row per requested year, ascending.
    assert [point["year"] for point in payload["series"]] == list(range(1990, 1995))
    assert all(set(point) == SERIES_KEYS for point in payload["series"])

    by_year = {point["year"]: point for point in payload["series"]}

    # 1990: one 3-day episode.
    zero = by_year[1990]
    assert zero["count"] == 1
    assert zero["total_days"] == 3
    assert zero["max_duration"] == 3
    assert zero["mean_duration"] == pytest.approx(3.0)
    assert zero["max_tmax"] == pytest.approx(31.0)

    # 1991: the cross-year episode is attributed entirely here.
    crossing = by_year[1991]
    assert crossing["count"] == 1
    assert crossing["total_days"] == 4
    assert crossing["max_duration"] == 4
    assert crossing["mean_duration"] == pytest.approx(4.0)
    assert crossing["max_tmax"] == pytest.approx(34.0)

    # 1992: every hot day (Jan 1-2) belongs to the 1991-started episode, so
    # no episode starts in 1992 and the year reports a real zero.
    empty = by_year[1992]
    assert empty["count"] == 0
    assert empty["total_days"] == 0
    assert empty["max_duration"] == 0
    assert empty["mean_duration"] is None
    assert empty["max_tmax"] is None
    assert empty["valid_days"] == empty["expected_days"] == 366

    # 1993: one 3-day episode at its annual peak; 1994: one 6-day episode.
    assert by_year[1993]["max_tmax"] == pytest.approx(37.0)
    assert by_year[1993]["mean_duration"] == pytest.approx(3.0)
    assert by_year[1994]["total_days"] == 6
    assert by_year[1994]["max_duration"] == 6
    assert by_year[1994]["mean_duration"] == pytest.approx(6.0)

    # Every requested day is present and UTC-complete.
    for year, point in by_year.items():
        assert point["valid_days"] == (366 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 365)
        assert point["expected_days"] == point["valid_days"]

    # Episodes: ascending by start, inclusive ISO dates, consistent duration.
    episodes = payload["episodes"]
    assert all(set(episode) == EPISODE_KEYS for episode in episodes)
    starts = [date.fromisoformat(episode["start"]) for episode in episodes]
    assert starts == sorted(starts)
    assert [episode["start"] for episode in episodes] == [
        "1990-07-01",
        "1991-12-30",
        "1993-07-10",
        "1994-08-01",
    ]
    for episode in episodes:
        start = date.fromisoformat(episode["start"])
        end = date.fromisoformat(episode["end"])
        assert episode["duration"] == (end - start).days + 1
        assert start <= end

    assert episodes[0]["duration"] == 3
    assert episodes[0]["max_tmax"] == pytest.approx(31.0)
    assert episodes[0]["mean_tmax"] == pytest.approx(31.0)
    assert episodes[0]["max_excess"] == pytest.approx(1.0)

    # The cross-year episode: 4 days, mean 32.5, peak 34, excess 4.
    assert episodes[1]["start"] == "1991-12-30"
    assert episodes[1]["end"] == "1992-01-02"
    assert episodes[1]["duration"] == 4
    assert episodes[1]["max_tmax"] == pytest.approx(34.0)
    assert episodes[1]["mean_tmax"] == pytest.approx(32.5)
    assert episodes[1]["max_excess"] == pytest.approx(4.0)

    assert episodes[2]["max_tmax"] == pytest.approx(37.0)
    assert episodes[2]["mean_tmax"] == pytest.approx(36.0)
    assert episodes[2]["max_excess"] == pytest.approx(7.0)

    # trends: exactly the three raw annual aggregates, fitted over 5 years.
    trends = payload["trends"]
    assert set(trends) == TRENDS_KEYS
    years = [float(y) for y in range(1990, 1995)]
    expected = {
        "count": [1.0, 1.0, 0.0, 1.0, 1.0],
        "total_days": [3.0, 4.0, 0.0, 3.0, 6.0],
        "max_duration": [3.0, 4.0, 0.0, 3.0, 6.0],
    }
    for key, values in expected.items():
        trend = trends[key]
        assert trend is not None
        assert set(trend) == TREND_KEYS
        slope, _ = _ols(years, values)
        assert trend["per_decade"] == pytest.approx(slope * 10.0, rel=1e-6, abs=1e-9)
        assert trend["years_used"] == 5
        expected_lag1 = _residual_lag1(years, values)
        assert expected_lag1 is not None
        assert trend["residual_lag1"] == pytest.approx(expected_lag1, rel=1e-6, abs=1e-9)
        # Descriptive only: no inference block may leak in.
        assert "ci95_low" not in trend
        assert "p_value" not in trend

    # trends.count is exactly linear-free but must be finite and descriptive.
    assert trends["count"]["per_decade"] == pytest.approx(0.0, abs=1e-9)
    assert trends["total_days"]["per_decade"] == pytest.approx(5.0, rel=1e-6)
    assert trends["max_duration"]["per_decade"] == pytest.approx(5.0, rel=1e-6)

    # This window holds a cross-year episode, so the implementation may
    # disclose start-year attribution in warnings; the exact wording and
    # locale are not pinned.  All entries must be non-empty strings.
    assert all(isinstance(w, str) and w.strip() for w in payload["warnings"])

    # One ingestion call for the requested window, T2M_MAX only.
    assert calls == [
        {
            "latitude": 42.87,
            "longitude": 74.60,
            "variable": "temperature_max",
            "start_year": 1990,
            "end_year": 1994,
        }
    ]


def test_defaults_threshold_30_and_min_duration_3(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_default_span(monkeypatch)
    params = {key: value for key, value in _params().items() if key not in ("threshold", "min_duration")}

    response = client.get("/api/heatwaves", params=params)

    assert response.status_code == 200
    payload = response.json()
    assert payload["threshold"] == pytest.approx(30.0)
    assert payload["min_duration"] == 3


# ---------------------------------------------------------------------------
# Detection semantics: strict comparison, min_duration, Dec 31 crossing
# ---------------------------------------------------------------------------


def test_equality_at_threshold_breaks_a_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_with([("1990-07-01", [31.0, 31.0, 30.0, 31.0, 31.0, 31.0])], 1990, 1990)
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1990))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["episodes"]) == 1
    episode = payload["episodes"][0]
    assert episode["start"] == "1990-07-04"
    assert episode["duration"] == 3
    assert payload["series"][0]["count"] == 1


@pytest.mark.parametrize("run_length,expected_count", [(2, 0), (3, 1), (4, 1)])
def test_min_duration_boundary(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    run_length: int,
    expected_count: int,
) -> None:
    daily = _daily_with([("1990-07-01", [31.0] * run_length)], 1990, 1990)
    _install_power(monkeypatch, daily)

    response = client.get(
        "/api/heatwaves",
        params=_params(start_year=1990, end_year=1990, min_duration=3),
    )

    assert response.status_code == 200
    assert response.json()["series"][0]["count"] == expected_count


def test_min_duration_one_counts_single_hot_days(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_with([("1990-07-01", [31.0]), ("1990-07-05", [31.0])], 1990, 1990)
    _install_power(monkeypatch, daily)

    response = client.get(
        "/api/heatwaves",
        params=_params(start_year=1990, end_year=1990, min_duration=1),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["series"][0]["count"] == 2
    assert payload["episodes"][0]["duration"] == 1


def test_episode_spanning_new_year_is_single_and_attributed_to_start_year(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_with(
        [("1991-12-30", [31.0, 32.0, 33.0, 34.0, 35.0])],
        1990,
        1992,
    )
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1992))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["episodes"]) == 1
    episode = payload["episodes"][0]
    assert (episode["start"], episode["end"]) == ("1991-12-30", "1992-01-03")
    assert episode["duration"] == 5

    by_year = {point["year"]: point for point in payload["series"]}
    assert by_year[1991]["count"] == 1
    assert by_year[1991]["total_days"] == 5
    assert by_year[1991]["max_tmax"] == pytest.approx(35.0)
    # Jan 1-3 1992 days are inside the 1991 episode: nothing extra in 1992.
    assert by_year[1992]["count"] == 0
    assert by_year[1992]["total_days"] == 0
    assert by_year[1992]["max_duration"] == 0
    assert by_year[1992]["mean_duration"] is None
    assert by_year[1992]["max_tmax"] is None


# ---------------------------------------------------------------------------
# Outer-window censoring must be disclosed
# ---------------------------------------------------------------------------


def test_episode_touching_window_start_triggers_censoring_warning(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_with([("1990-01-01", [31.0, 31.0, 31.0])], 1990, 1994)
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1994))

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any(isinstance(warning, str) and warning.strip() for warning in warnings)


def test_episode_touching_window_end_triggers_censoring_warning(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _daily_with([("1994-12-29", [31.0, 32.0, 33.0])], 1990, 1994)
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1994))

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any(isinstance(warning, str) and warning.strip() for warning in warnings)


# ---------------------------------------------------------------------------
# Zero and incomplete years; leap years
# ---------------------------------------------------------------------------


def test_complete_years_with_zero_episodes_report_real_zeros(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _flat_daily(1990, 1994)
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1994))

    assert response.status_code == 200
    payload = response.json()
    for point in payload["series"]:
        assert point["count"] == 0
        assert point["total_days"] == 0
        assert point["max_duration"] == 0
        assert point["mean_duration"] is None
        assert point["max_tmax"] is None
    assert payload["episodes"] == []
    # Truly clean window: no missing days, no episodes, no cross-year
    # attribution, so no warnings are expected.
    assert payload["warnings"] == []
    trends = payload["trends"]
    for key in TRENDS_KEYS:
        assert trends[key] is not None
        assert trends[key]["per_decade"] == pytest.approx(0.0, abs=1e-9)
        assert trends[key]["years_used"] == 5
        assert "residual_lag1" in trends[key]


def test_incomplete_year_keeps_diagnostics_and_nulls_stats(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_default_span(monkeypatch, drop=("19930715",))

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1994))

    assert response.status_code == 200
    payload = response.json()
    by_year = {point["year"]: point for point in payload["series"]}
    incomplete = by_year[1993]
    assert incomplete["valid_days"] == 364
    assert incomplete["expected_days"] == 365
    assert incomplete["count"] is None
    assert incomplete["total_days"] is None
    assert incomplete["max_duration"] is None
    assert incomplete["mean_duration"] is None
    assert incomplete["max_tmax"] is None
    # Other years are unaffected.
    assert by_year[1990]["count"] == 1
    # Only four complete years remain: trends must be null.
    assert payload["trends"] == {key: None for key in TRENDS_KEYS}


def test_missing_hot_day_breaks_a_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A missing middle day splits a 5-day hot spell into two 2-day runs,
    # neither of which reaches min_duration=3.  CLARIFICATION: the contract
    # pins that a missing day breaks a run rather than bridging it.
    daily = _daily_with(
        [("1990-07-01", [31.0, 31.0, 31.0, 31.0, 31.0])],
        1990,
        1990,
        drop=("19900703",),
    )
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1990))

    assert response.status_code == 200
    payload = response.json()
    assert payload["episodes"] == []
    (row,) = payload["series"]
    assert row["valid_days"] == 364
    assert row["count"] is None


def test_leap_year_diagnostics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _flat_daily(2020, 2021)
    del daily["20200229"]
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=2020, end_year=2021))

    assert response.status_code == 200
    by_year = {point["year"]: point for point in response.json()["series"]}
    assert by_year[2020]["expected_days"] == 366
    assert by_year[2020]["valid_days"] == 365
    assert by_year[2020]["count"] is None
    assert by_year[2021]["expected_days"] == 365
    assert by_year[2021]["valid_days"] == 365
    assert by_year[2021]["count"] == 0


# ---------------------------------------------------------------------------
# Trend availability
# ---------------------------------------------------------------------------


def test_fewer_than_five_complete_years_disables_all_trends(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = _flat_daily(1990, 1993)
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1993))

    assert response.status_code == 200
    assert response.json()["trends"] == {key: None for key in TRENDS_KEYS}


def test_incomplete_year_is_excluded_from_the_fit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spells: list[tuple[str, list[float]]] = [
        (f"{year}-07-01", [31.0] * (3 + index)) for index, year in enumerate(range(1990, 1996))
    ]
    daily = _daily_with(spells, 1990, 1995, drop=("19920701",))  # breaks 1992 spell + completeness
    _install_power(monkeypatch, daily)

    response = client.get("/api/heatwaves", params=_params(start_year=1990, end_year=1995))

    assert response.status_code == 200
    payload = response.json()
    by_year = {point["year"]: point for point in payload["series"]}
    assert by_year[1992]["count"] is None
    for trend in payload["trends"].values():
        assert trend is not None
        assert trend["years_used"] == 5


# ---------------------------------------------------------------------------
# Request validation happens before ingestion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": 90.1},
        {"latitude": -90.1},
        {"latitude": "nan"},
        {"latitude": "inf"},
        {"longitude": 180.1},
        {"longitude": -180.1},
        {"longitude": "nan"},
        {"start_year": 1980},
        {"end_year": 2026},
        {"start_year": 1995, "end_year": 1994},
        {"threshold": "nan"},
        {"threshold": "inf"},
        {"threshold": "abc"},
        {"min_duration": 0},
        {"min_duration": -1},
        {"min_duration": "abc"},
        {"min_duration": "2.5"},
    ],
)
def test_invalid_requests_return_the_error_envelope_before_ingestion(
    client: TestClient,
    overrides: dict[str, object],
) -> None:
    response = client.get("/api/heatwaves", params=_params(**overrides))

    _assert_error(response, 422, "INVALID_REQUEST")


def test_missing_required_and_optional_params_are_rejected(
    client: TestClient,
) -> None:
    for missing in ("latitude", "longitude", "start_year", "end_year"):
        params = _params()
        params.pop(missing)
        response = client.get("/api/heatwaves", params=params)
        _assert_error(response, 422, "INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Source metadata and ingestion failure mapping
# ---------------------------------------------------------------------------


def test_source_marks_synthetic_provenance_and_utc(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_default_span(monkeypatch, cached=True)

    response = client.get("/api/heatwaves", params=_params())

    assert response.status_code == 200
    source = response.json()["source"]
    assert source["name"].upper().startswith("NASA POWER")
    assert source["dataset"] == SYNTHETIC_DATASET
    assert source["time_standard"] == "UTC"
    assert source["request_url"].startswith("https://")
    assert source["cached"] is True
    retrieved = source["retrieved_at"].replace("Z", "+00:00")
    from datetime import datetime

    parsed = datetime.fromisoformat(retrieved)
    assert parsed.utcoffset() is not None


def test_power_failure_uses_the_existing_error_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**kwargs: object) -> tuple[dict[str, float], dict[str, object]]:
        raise PowerError("NASA POWER is temporarily unavailable.")

    monkeypatch.setattr(main, "load_daily", fail)

    response = client.get("/api/heatwaves", params=_params())

    _assert_error(response, 502, "POWER_UNAVAILABLE")


def test_source_parameter_mismatch_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_source = _source()
    wrong_source["parameter"] = "T2M_MIN"
    daily = _flat_daily(1990, 1994)
    _install_power(monkeypatch, daily, source=wrong_source)

    response = client.get("/api/heatwaves", params=_params())

    error = _assert_error(response, 502, "POWER_UNAVAILABLE")
    assert "parameter" in error["message"].lower()


def test_pdf_describes_heatwaves_and_exports_the_detective_title(client, monkeypatch):
    from io import BytesIO
    from pypdf import PdfReader

    _install_default_span(monkeypatch)
    response = client.get("/api/heatwaves", params=_params(), headers={
        "Accept": "application/pdf", "X-EarthScope-Report": "detective",
    })
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    for label in ("Детектив", "Волны жары", "T2M_MAX", "Автокорреляция", "Последние эпизоды"):
        assert label in text


def test_two_complete_hot_years_form_one_long_episode(client, monkeypatch):
    _install_power(monkeypatch, _flat_daily(2023, 2024, default=35.0))
    response = client.get("/api/heatwaves", params=_params(start_year=2023, end_year=2024, min_duration=366))
    assert response.status_code == 200
    result = response.json()
    assert [(event["start"], event["end"], event["duration"]) for event in result["episodes"]] == [
        ("2023-01-01", "2024-12-31", 731),
    ]
    assert [row["total_days"] for row in result["series"]] == [731, 0]
