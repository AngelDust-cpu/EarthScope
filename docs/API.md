# EarthScope API contract

EarthScope examines annual trends at one coordinate using NASA POWER gridded meteorology. Comparison uses the same annual aggregation and period for two independently requested locations.

## Ownership and sequence

Research confirms challenge scope and source attribution. Science reviews the methods before implementation and audits the finished output. Data owns `backend/app/power.py` and `backend/tests/test_power.py`. Backend owns `backend/app/{main,schemas,analysis}.py` and `backend/tests/test_api.py`. Frontend owns `frontend/src/` against the routes below. Codex owns scaffold, dependency locks, browser and live integration checks, and fixes across components after handoff. QA reviews the integrated code. Pitch drafts demo and submission copy.

## Routes

- `GET /api/health` returns `{"status":"ok"}`.
- `GET /api/coverage` returns `{"min_year":1981,"max_year":2025,"min_years":5}`. The bounds are the MVP's supported calendar years, and `min_years` is the minimum number of complete values for a fit. The API can return annual values for a shorter window with a null trend. Validate inclusive bounds before requesting POWER. Actual usable years depend on the location and returned data.
- `GET /api/trend?latitude=42.87&longitude=74.6&variable=temperature&start_year=1990&end_year=2025`. WGS84 degrees with latitude in [-90,90] and longitude in [-180,180]. Years are inclusive UTC calendar years. Variables are `temperature` and `precipitation`.
- `POST /api/compare` accepts `{ "point_a": {"latitude": number, "longitude": number}, "point_b": {"latitude": number, "longitude": number}, "variable": "temperature"|"precipitation", "start_year": number, "end_year": number }`. Coordinates are WGS84 decimal degrees. Years are inclusive UTC calendar years and are identical for A and B. The server loads each point independently through the existing location/variable/date cache and applies the existing complete-year aggregation and OLS routine to both. A failed upstream fetch fails the comparison explicitly; no partial result or mock fallback is returned.

The response is `{ "point_a": TrendResponse, "point_b": TrendResponse, "comparison": { "method": "paired_annual_difference_descriptive", "years_used": number, "slope_difference_per_decade": number|null, "ci95_low": null, "ci95_high": null, "p_value": null, "inferential": false, "note": string } }`. The difference is the OLS slope of A − B on common complete years, multiplied by ten. It is null with fewer than five paired complete years. CI and p-value are always null; the UI does not claim statistical significance. Differing missing years are excluded pairwise for this difference estimate, while each point's trend uses its own complete years. Same units are enforced by the shared variable. [Scientific method](SCIENTIFIC_METHOD.md)

Successful Trend DTO (all keys required):

```ts
interface TrendResponse {
  latitude: number
  longitude: number
  variable: 'temperature' | 'precipitation'
  start_year: number
  end_year: number
  unit: string // °C for annual mean temperature; mm for annual precipitation total
  aggregation: 'mean' | 'sum'
  series: { year: number; value: number | null; valid_days: number; expected_days: number; fitted: number | null }[]
  trend: {
    years_used: number
    per_decade: number
    ci95_low: number
    ci95_high: number
    p_value: number
    significant: boolean
    r_squared: number
  } | null
  source: {
    name: string // NASA POWER
    parameter: 'T2M' | 'PRECTOTCORR'
    daily_unit: 'C' | 'mm/day'
    dataset: string // from header.sources (MERRA2 in verified sample)
    time_standard: 'UTC'
    request_url: string
    retrieved_at: string // ISO 8601 UTC
    cached: boolean
  }
  warnings: string[]
}
```

Every requested year appears in ascending order. Only complete years are used. Calculate mean of valid daily T2M or sum daily PRECTOTCORR over one-day intervals. Reject nonfinite, null, missing or fill-value days and negative precipitation. A year with any missing day has null value. A minimum of five valid annual values is required for OLS. Fit to calendar year, report slope × 10 and two-sided Student t 95% slope interval × 10 and p-value (alpha .05). Include fitted values at included years only. For a constant series report slope/interval/R² zero and p=1. Exact nonconstant fit with zero residual variance gets a point interval and p=0 as the zero-error limit. Use centered years, residual-based standard error, and document independence and variance assumptions, possible autocorrelation, exploratory multiple testing, reanalysis/grid limitations and lack of causality. A null trend has a warning explaining insufficient complete years. CSV is generated from this response in the frontend.

Errors always return `{"error":{"code":"...","message":"..."}}`. Use HTTP 422 `INVALID_REQUEST` for input constraints and HTTP 502/503 `POWER_UNAVAILABLE` for invalid or unavailable upstream data. Show actionable errors in the UI.

## Internal data boundary

Fit with an intercept and center the predictor at the mean of the included calendar years. Set residual degrees of freedom to n − 2. Compute R² as 1 − SSE/SST for nonconstant values. The constant and exact-fit results above are explicit degenerate conventions. Display exact-fit p=0 as a zero-residual limit or a threshold such as <0.001. EarthScope uses the ordinary OLS interval without an autocorrelation adjustment. Include this limitation in the UI and avoid inferring causation or treating an exploratory p-value as confirmation.

`backend/app/power.py` exports synchronous `load_daily(latitude: float, longitude: float, variable: str, start_year: int, end_year: int) -> tuple[dict[str,float], dict]` and `PowerError`. Daily keys are YYYYMMDD, invalid and missing values omitted. The second value is the source object above. Fetch HTTPS at NASA POWER Daily Point using `community=AG`, `time-standard=UTC`, `format=JSON`, dates Jan 1 through Dec 31 and the selected parameter. Validate upstream headers and units, avoid silently accepting truncated header coverage, cache validated payloads by request using atomic writes and a timestamp, and expose cache status. `EARTHSCOPE_CACHE_DIR` controls local file storage. HTTPS verification stays enabled. No synthetic data at runtime.
