# EarthScope 2.4 extreme weather contract

`GET /api/extremes` accepts WGS84 `latitude`, `longitude`, inclusive
`start_year`, `end_year` (1981–2025), `event` and numeric `threshold`.
Events: `hot_days` uses T2M_MAX > threshold (°C; default UI 30);
`cold_nights` uses T2M_MIN < threshold (°C; default UI 0; UTC daily
minimum, not a night-only observation); `heavy_rain` uses PRECTOTCORR >
threshold (mm/day; default UI 20). Require finite thresholds, temperature
[-100,100], precipitation [0,2000]. Equality is not counted. Use the
existing error envelope.

Response:

```ts
{
 latitude: number; longitude: number; start_year: number; end_year: number;
 event: 'hot_days' | 'cold_nights' | 'heavy_rain';
 threshold: number; threshold_unit: '°C' | 'mm/day'; operator: '>' | '<';
 source: SourceInfo; warnings: string[];
 series: {
   year: number; events: number|null; fraction: number|null;
   valid_days: number; expected_days: number; excluded_days: number;
   fitted: number|null;  // primary fit in event days per 365 days; not counts
 }[];
 trend: {
   years_used: number; per_decade: number; count_per_decade: number;
   ci95_low: number|null; ci95_high: number|null; p_value: number|null;
   significant: boolean|null; residual_lag1: number|null;
   sensitivity: {method: 'ols'|'hac', lags: number|null,
                 ci95_low: number; ci95_high: number; p_value: number}[];
 }|null;
}
```

A year must have all 365/366 UTC days. Incomplete years retain diagnostics,
but events and fraction are null; zero from a complete year is a real
result. Fraction is events / valid days. Plot counts and (if shown together)
label the fitted series clearly as normalized event days per 365 days, or
show it on a separate chart. Report both primary slope and count slope.

Fit the annual normalized rate `365*events/valid_days` with OLS intercept
and actual calendar years; at least 5 complete years, reusing existing point
fit. Count slope uses the same OLS spec on raw counts. No trend for fewer
than 5. Inference only when all included years are one consecutive block of ≥20 years, nonconstant rates,
nondegenerate residuals and finite positive covariance. Use Bartlett
Newey–West HAC with lag 3, finite-sample multiplier n/(n−2) and approximate
t(n−2) inference; report conventional OLS, HAC(1), HAC(3), HAC(5) sensitivity.
If any inference guard fails, retain descriptive slopes, null inference
fields, empty sensitivity. No trend line for degenerate or short fits if
the UI would imply evidence. Lag-one residual correlation (Pearson) uses
only consecutive calendar-year pairs if estimable (≥4 pairs and nonzero
variance); never compress gaps into artificial adjacent observations.
No causal inference, no correction for repeated exploration, no relative
climatological definition. Frequency extrapolation is not supported.

Comparisons may issue two requests for the same event, threshold and
years (one for identical points) and display independent results. Mark
differences as descriptive; separate p-values do not test differences.
Absolute thresholds can have different local meanings.

Data role extends existing `power.load_daily` variable keys
`temperature_max` (T2M_MAX), `temperature_min` (T2M_MIN), and existing
`precipitation`. Data owns power.py/test_power.py. Backend consumes these
keys and owns main.py, schemas.py, extremes.py and test_extremes_api.py.
Codex owns extremes_stats.py and Detective integration.
`extremes_stats.fit_extremes` takes `list[AnnualAggregate]` whose values are
full-year raw counts or null and returns `(trend_dict_or_none, fitted_by_year)`.
