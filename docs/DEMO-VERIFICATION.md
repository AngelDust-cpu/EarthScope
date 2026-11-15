# EarthScope final demo verification

## EarthScope 2.4 · Threshold events

Live NASA POWER probes for Bishkek and Reykjavík returned 13,149 days per parameter for T2M_MAX, T2M_MIN and PRECTOTCORR (1990–2025 UTC). Independent strict threshold counts, annual proportions and normalized slopes matched the local API for all six point/event combinations. Repeating a request with a different threshold reused the same daily cache.

At Bishkek, the default thresholds gave normalized slopes of +3.176 hot days per 365 days per decade (HAC(3) p≈0.142), −5.153 cold days (p≈0.0096) and −0.191 heavy-precipitation days (p≈0.103). These are exploratory results, not causal or regional claims. For Reykjavík at Tmax > 30 °C, all 36 complete years had zero events, so the estimate was zero and inference was suppressed. [Method and limitations](SCIENTIFIC_METHOD.md#earthscope-24-threshold-events)

## EarthScope 2.3 · Anomalies and seasons

November 14, 2026. NASA POWER returned 13,514 valid UTC daily values per variable for Bishkek (42.870, 74.600) from 1989 through 2025, including December before the first displayed winter. The independently aggregated 1991–2020 references were **13.596799 °C** (mean of annual T2M means) and **364.908667 mm** (mean of annual precipitation totals). An independent date-based calculation matched the API's annual departures, complete seasonal aggregates and seasonal slopes for 1990–2025. In that example, spring had the largest absolute temperature trend (about **+0.848 °C per decade**); summer had the largest absolute precipitation trend (about **−6.44 mm per decade** in the seasonal total). These are descriptive rankings, with no formal test of differences.

The browser exercises a baseline outside the display window, February 2020 with all 29 days, CSV, both additional Detective questions, and reset. [Method](SCIENTIFIC_METHOD.md) · [Contract](CLIMATE-API.md)

## EarthScope 2.1 · Geographic Comparison

The comparison endpoint independently fetched both demo points from NASA POWER for both variables with the cache disabled on November 14, 2026. All four requests returned MERRA-2 and yielded 36 paired complete years (1990–2025). The exact same coordinates also succeeded from the validated local cache. Independent aggregation and regression on the cached daily values reproduced the paired A − B slope: **+0.242416 °C per decade** for temperature and **+41.395418 mm per decade** for annual precipitation. These describe differences in the fitted trends. The app does not test their statistical significance.

Edge browser checks on desktop and at 390 px covered both modes, independent A/B map selection, unchanged map viewport after analysis, no requests on selection or zoom, combined series, anomaly transform, two-point CSV and recovery from an injected upstream failure. Backend pytest: 90 passed. TypeScript and production build passed; Vite reports a Plotly bundle size advisory. See the [two-minute demo](DEMO.md) and [scientific method](SCIENTIFIC_METHOD.md).

## EarthScope 2.0 baseline

November 14, 2026. The local frontend and backend started from the workspace runtimes on ports 5174 and 8014 with an empty audit cache and no provider API keys in the app processes. The browser performed four live NASA POWER queries. Each returned 13,149 UTC days with no missing dates or fill values, yielding 36 complete calendar years. The POWER Daily API version was v2.10.3 and the dataset was MERRA-2. No AI calls are made by the application. The browser requested local assets and optional Google Fonts with system fallbacks. [NASA POWER access](https://power.larc.nasa.gov/resources/power-one-pager.pdf) · [Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/)

Browser checks covered the initial chart, numeric and map selection, variable switching, plotted values and trend line against API JSON, CSV contents, invalid-year blocking, the Bishkek preset, and 390 px layout. All four exact queries also succeeded from validated cache with upstream fetching forbidden. The production build and 62 pytest tests passed. Vite reports its Plotly bundle size advisory and pytest reports an upstream test-client deprecation.

QA (Inkling 2 Small Free) independently ran the backend and build and flagged the second location's cache, the portable startup instructions and API-origin example. The exact second location is now cached in the default demo directory and the instructions were updated. Science (Nemotron 3 Ultra via NVIDIA) reviewed the aggregation, HAC formula and interpretation. OpenCode estimated 0 USD for both review sessions. The demo flow was replayed in a browser; spoken delivery and a clean installation on different hardware were outside this rehearsal.

## Same-period comparison, 1990–2025

Temperature is the annual mean of daily T2M in °C. Precipitation is the annual sum of PRECTOTCORR daily rates over their one-day intervals, in mm. Slopes below are changes in those annual metrics per decade. Annual values and OLS statistics were independently checked against the fresh raw NASA responses. The intervals in this table are the nominal OLS intervals shown in the dashboard.

| Point | Variable | Slope per decade | 95% OLS interval | OLS p |
|---|---|---:|---:|---:|
| Bishkek (42.870, 74.600) | Temperature | +0.373 °C | +0.184 to +0.561 | 0.000316 |
| Bishkek (42.870, 74.600) | Precipitation | −1.10 mm | −29.68 to +27.49 | 0.938 |
| Reykjavík (64.147, −21.942) | Temperature | +0.130 °C | −0.017 to +0.277 | 0.0812 |
| Reykjavík (64.147, −21.942) | Precipitation | −42.49 mm | −100.72 to +15.74 | 0.147 |

## Autocorrelation sensitivity

Use the fitted OLS residuals for the lag-one Pearson correlation. Hold the OLS slopes fixed and recompute uncertainty with Newey–West HAC covariance, Bartlett weights, the n/(n−2) small-sample multiplier and approximate two-sided t(34) inference. The 36 calendar years are consecutive. Compare 1, 3 and 5 annual lags to show sensitivity to the lag choice. This check is external to the dashboard. [HAC reference](https://www.statsmodels.org/dev/generated/statsmodels.stats.sandwich_covariance.cov_hac.html)

| Point and variable | Lag-one residual correlation | HAC p, 1 lag | HAC p, 3 lags | HAC p, 5 lags |
|---|---:|---:|---:|---:|
| Bishkek temperature | +0.084 | 0.00135 | 0.00297 | 0.00225 |
| Bishkek precipitation | +0.319 | 0.946 | 0.950 | 0.952 |
| Reykjavík temperature | −0.001 | 0.0832 | 0.0756 | 0.0647 |
| Reykjavík precipitation | +0.176 | 0.140 | 0.0873 | 0.0476 |

Bishkek warming remains significant for all tested lags. Bishkek precipitation and Reykjavík temperature remain above 0.05. Reykjavík precipitation crosses the threshold only at five lags, with a 95% HAC interval of about −84.51 to −0.48 mm per decade. Treat this as sensitivity to the method. With 36 years and four exploratory comparisons, these results do not establish causation, a difference between the two slopes, or city-wide conditions. A lack of significance does not establish absence of change.
