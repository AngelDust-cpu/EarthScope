"""Deterministic annual-frequency fixtures, not NASA observations."""
import calendar

import numpy as np
import pytest
from scipy.stats import t

from app.analysis import AnnualAggregate
from app.extremes_stats import fit_extremes


def fixture(n=30, missing=(), constant=False):
    return [AnnualAggregate(
        year=year,
        expected_days=365 + calendar.isleap(year),
        valid_days=0 if year in missing else 365 + calendar.isleap(year),
        value=None if year in missing else 0 if constant else 14 + i // 3 + [2, 3, 2, -1, -3, -2][i % 6],
    ) for i, year in enumerate(range(1990, 1990 + n))]


def test_sandwich_intervals_match_independent_matrix_reference():
    raw = fixture()
    result, fitted = fit_extremes(raw)
    assert result is not None and len(fitted) == 30
    n = len(raw)
    x = np.column_stack((np.ones(n), np.arange(n)))
    y = np.array([365 * p.value / p.expected_days for p in raw])
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    e = y - x @ beta
    inverse = np.linalg.inv(x.T @ x)
    assert result["per_decade"] == pytest.approx(10 * beta[1])
    assert result["residual_lag1"] == pytest.approx(np.corrcoef(e[:-1], e[1:])[0, 1])
    for row in result["sensitivity"]:
        if row["method"] == "ols":
            covariance = inverse * (e @ e) / (n - 2)
        else:
            lags = row["lags"]
            weights = np.maximum(0, 1 - np.abs(np.subtract.outer(np.arange(n), np.arange(n))) / (lags + 1))
            covariance = inverse @ x.T @ (weights * np.outer(e, e)) @ x @ inverse * n / (n - 2)
        error = np.sqrt(covariance[1, 1])
        assert row["ci95_low"] == pytest.approx(10 * (beta[1] - t.ppf(.975, n - 2) * error))
        assert row["p_value"] == pytest.approx(2 * t.sf(abs(beta[1] / error), n - 2))
    assert result["p_value"] == result["sensitivity"][2]["p_value"]


@pytest.mark.parametrize("raw", [fixture(19), fixture(missing=(1993,)), fixture(constant=True)])
def test_short_gappy_or_constant_series_has_no_inference(raw):
    result, fitted = fit_extremes(raw)
    assert result is not None
    assert result["significant"] is None and result["p_value"] is None
    assert not result["sensitivity"] and len(fitted) == result["years_used"]


def test_no_fit_if_fewer_than_five_complete_years():
    assert fit_extremes(fixture(5, missing=(1993,))) == (None, {})
