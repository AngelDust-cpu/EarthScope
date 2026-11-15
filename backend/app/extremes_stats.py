"""Exploratory linear trends for annual complete-year event frequencies."""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from scipy.stats import t

from .analysis import AnnualAggregate, fit_trend, hac_slope_standard_error


def _interval(slope: float, se: float, n: int) -> dict[str, float]:
    width = float(t.ppf(0.975, n - 2)) * se
    return {
        "ci95_low": float(slope - width),
        "ci95_high": float(slope + width),
        "p_value": float(2 * t.sf(abs(slope / se), n - 2)),
    }


def fit_extremes(
    aggregates: list[AnnualAggregate],
) -> tuple[dict | None, dict[int, float]]:
    """Fit event days per 365 days; apply HAC only to eligible annual records."""
    counts = [p for p in aggregates if p.value is not None and p.valid_days == p.expected_days]
    complete = [replace(p, value=365 * float(p.value) / p.valid_days) for p in counts]
    fit, fitted, _ = fit_trend(complete)
    count_fit, _, _ = fit_trend(counts)
    if fit is None or count_fit is None:
        return None, {}

    n = len(complete)
    years = np.array([p.year for p in complete], dtype=float)
    y = np.array([p.value for p in complete], dtype=float)
    fitted_values = np.array([fitted[p.year] for p in complete], dtype=float)
    residual = y - fitted_values
    adjacent = np.diff(years) == 1
    noise = np.linalg.norm(residual) > 1e-10 * max(np.linalg.norm(y - y.mean()), 1.0)
    lag1 = None
    if noise and np.count_nonzero(adjacent) >= 4:
        before, after = residual[:-1][adjacent], residual[1:][adjacent]
        if np.std(before) > 1e-12 and np.std(after) > 1e-12:
            correlation = float(np.corrcoef(before, after)[0, 1])
            if math.isfinite(correlation):
                lag1 = max(-1.0, min(1.0, correlation))

    result = dict(
        years_used=n, per_decade=fit.per_decade, count_per_decade=count_fit.per_decade,
        ci95_low=None, ci95_high=None, p_value=None, significant=None,
        residual_lag1=lag1, sensitivity=[],
    )
    if not (n >= 20 and adjacent.all() and noise and fit.ci95_low < fit.ci95_high):
        return result, fitted

    # Centering makes the slope's diagonal entry of (X'X)^(-1) equal 1/Sxx;
    # the Bartlett lag sum is shared with the regional map layer.
    sensitivity = [
        dict(method="ols", lags=None, ci95_low=fit.ci95_low,
             ci95_high=fit.ci95_high, p_value=fit.p_value),
    ]
    for bandwidth in (1, 3, 5):
        standard_error = hac_slope_standard_error(
            years, y, fitted_values, bandwidth
        )
        if standard_error is None:
            return result, fitted
        sensitivity.append(dict(method="hac", lags=bandwidth,
                                **_interval(fit.per_decade, 10 * standard_error, n)))
    primary = sensitivity[2]
    result.update(
        ci95_low=primary["ci95_low"], ci95_high=primary["ci95_high"],
        p_value=primary["p_value"], significant=primary["p_value"] < 0.05,
        sensitivity=sensitivity,
    )
    return result, fitted
