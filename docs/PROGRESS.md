# EarthScope progress

## November 14, 2026

- Workspace inspected. Existing OpenCode roles and provider assignments are preserved.
- Official challenge calls for variable selection, a time series by location, change magnitude, and statistical significance. [Challenge brief](https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/)
- Live POWER Daily Point request at 42.87° N, 74.60° E returned both parameters for 1990–2025, with 13,149 UTC days per variable, no fill values, and MERRA2 provenance. Raw diagnostic response is under ignored `.tools/`. [NASA POWER Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/)
- API contract implemented. FastAPI validates input and computes complete-year aggregates and trends. The React dashboard provides the interactive world map, Bishkek preset, coordinate and year controls, chart, interpretation and CSV export.
- Verified the 1981 start and upstream validation failure for 1980. The dashboard currently accepts 1981–2025. Real live POWER results entered the local cache with source timestamps.
- Both local servers started. The production Vite/TypeScript build passed. Backend and ingestion suites passed 62 tests with one upstream test-client deprecation warning. Edge browser checks passed for the initial chart, map selection, precipitation, CSV, mobile layout and coverage-error retry.
- At the Bishkek preset over 1990–2025, independently recomputed all 36 annual values per variable plus the slopes, 95% intervals and p-values from the recorded NASA response. Temperature slope +0.373 °C per decade (95% CI +0.184 to +0.561, p=0.000316). Annual precipitation slope −1.10 mm per decade (95% CI −29.68 to +27.49, p=0.938). These are nominal OLS results at a single grid point.

## OpenCode handoffs

| Role and model | Result | OpenCode cost estimate |
|---|---|---|
| Research · Gemini 3.8 Flash | Google returned HTTP 503 on both attempts, including a no-tool retry. Codex stopped the retrying sessions and verified the official brief directly. | No completed inference estimate |
| Science · NVIDIA Nemotron 3 Ultra | Reviewed methods, requested explicit centering and n−2, then approved implementation after formula and fixture review. Codex independently verified the live results. | 0 USD for both reviews |
| Data · GLM-5.3-Flash Free | Independently queried POWER (including 1981), implemented the client and cache, and passed ingestion tests. | 0 USD for discovery and implementation |
| Backend · DeepSeek V4.1 Flash Free | Implemented and tested the contract, including OLS, missing years and CORS. | 0 USD |
| Frontend · ERNIE 5.5 Flash Preview Free | Wrote the dashboard. OpenRouter daily quota interrupted handoff. Codex integrated it, fixed errors and responsiveness, and replaced the external tile endpoint with bundled Natural Earth data. | 0 USD reported before interruption |
| QA · Inkling 2 Small Free | Reviewed the code and test suite. Codex verified the coverage-error retry, stale display behavior and browser checks. | 0 USD |
| Pitch · Particle-1 Free | OpenRouter daily quota blocked the draft. Codex wrote DEMO.md. | Unavailable |

Known scope: one point per analysis, no autocorrelation or multiple-testing adjustment in the dashboard, and cached queries retain the original retrieval date until the cache is replaced. The production bundle contains Plotly and exceeds Vite's chunk-size advisory.

Final demo verification passed for both variables at Bishkek and Reykjavík. The four queries use validated local cache for rehearsal. Science reviewed the HAC calculation and QA identified the need to prewarm the exact second point. Both reviews reported an OpenCode estimate of 0 USD. [Results and limitations](DEMO-VERIFICATION.md) · [Two-minute script](DEMO.md)

## EarthScope 2.0

OpenRouter rejected the Particle-1 Free probe with HTTP 429 (daily limit). Frontend implemented the workspace, form, result lifecycle and Russian copy through GPT-6.1 Sol Medium (session `ses_e26ef2707ffeinWcdiMyCaS62h`). Codex integrated the shared locale, map controls, chart, and browser regressions. Inkling 2 Small reviewed the implementation (session `ses_e26ea245affeDHkaCedscDTggq`). Both completed specialist sessions reported a 0 USD OpenCode estimate.

The map keeps its viewport during selection and analysis. Only the explicit recenter button and preset move it to the marker. The trend request starts on form submit or explicit retry. The Russian interface labels the previous result during draft edits or a pending request. The backend contract and statistical methods are unchanged.

Verified TypeScript, production build, 62 backend tests, and Chromium desktop/mobile flows using the running API and cached NASA POWER data. The browser suite checks map movement, zoom, click/tap selection, request counts, viewport persistence, chart data, localized CSV, cancellation and recovery from an injected upstream error. Plotly still results in a Vite chunk-size advisory.

## EarthScope 2.1

- Added independent A/B selection on one map, Bishkek and Reykjavík presets, common variable and dates, overlay and shared-axis charts, baseline anomalies, and comparison CSV. Single-point analysis still works.
- Added `POST /api/compare` using existing POWER cache and annual aggregation. The comparison estimates a descriptive A − B trend over common complete years. [API](API.md) · [Scientific method](SCIENTIFIC_METHOD.md).
- Verified four fresh POWER downloads, cache reuse, and independently reproduced both paired slopes. 90 backend tests, TypeScript, production build and desktop/mobile Edge checks passed. Known limitations: no inferential comparison, individual OLS intervals are nominal, and the Plotly bundle triggers a build advisory. [Verification](DEMO-VERIFICATION.md) · [Demo](DEMO.md).
- No deployment. Next milestone: hosting and deployment after separate authorization.

| OpenCode role | Exact route | Session and result | OpenCode estimate |
|---|---|---|---:|
| Frontend | `openai/gpt-6.1-sol#medium` | `ses_e26ad9c20ffeH19f7EoT1Izp4m`: implemented A/B UI and reported build and synthetic browser checks | $0 |
| Backend | `opencode/deepseek-v4.1-flash-free` | `ses_e26ae283affeDKuk29PCNXo3Zt`: implemented API and 28 tests | $0 |
| Data | `opencode/glm-5.3-flash-free` | `ses_e26a2f4f4ffeZRlnMwB3YodB59`: verified independent cache keys, provenance and no runtime mocks | $0 |
| Science | `nvidia/nvidia/nemotron-3-ultra-550b-a55b` | `ses_e26b269b7ffeiIt8lw0NnhO7jl`: reviewed inference options and caveats; `ses_e26a251e1ffeyro9sP86J2qbxi`: validated the descriptive method | $0 for each |
| QA | `opencode/inkling-2-small-free` | `ses_e26ace854ffeYw7p7S1FBzF5ey`: reviewed integration and flagged preset, documentation and browser checks; Codex verified the final app | $0 |

Costs are OpenCode event estimates, not provider billing or subscription usage.

## EarthScope 2.2 · Detective

The Russian guided mode adds three questions (temperature, precipitation and A/B), coordinates and presets, a shared or single period, and a result step with charts, explanations and NASA provenance. Going back preserves settings, and a saved answer can reopen without an API call when its settings match. The wizard shares the workstation's coverage request and uses the same trend and comparison endpoints.

| OpenCode role | Exact route | Session and result | OpenCode estimate |
|---|---|---|---:|
| Frontend | `openai/gpt-6.1-sol#medium` | `ses_e269aad73ffeHvwRPBcm4tyH0r`: implemented the wizard and reported browser checks with fixtures | $0 |
| Backend | `opencode/deepseek-v4.1-flash-free` | `ses_e269acea3ffebJW85ogxErFART`: checked compatible API contracts, null cases and thresholds | $0 |
| Data | `opencode/glm-5.3-flash-free` | `ses_e269ace96ffeLSmtT5mlgfKkBT`: reviewed coverage, cache and source metadata | $0 |
| Science | `nvidia/nvidia/nemotron-3-ultra-550b-a55b` | `ses_e269aceb5ffeWm9yEsEIQeGklm` and `ses_e26994f3bffeMU3ItL6qaVZBZ7`: reviewed planned and actual templates | $0 each |
| QA | `opencode/inkling-2-small-free` | `ses_e269850c3ffeVLUr9uLyUOherh`: reviewed UI and templates; flagged duplicate coverage requests and result reuse | $0 |

Codex integrated the interpreter and addressed QA findings. Verification: 90 backend tests, 4 interpreter tests, TypeScript and Vite build, plus Edge regressions on desktop and at 390 px. All three questions reached real NASA POWER results through the existing cache; synthetic fixtures were used only for error and missing-data checks. The app uses no runtime AI service. Individual OLS inference remains nominal, the A/B estimate remains descriptive, and Vite still reports the Plotly bundle size advisory. [Scientific method](SCIENTIFIC_METHOD.md).

## EarthScope 2.2.1 · Detective navigation

The three scenario cards open the coordinate step directly. **Новое исследование** resets the wizard and invalidates an active request before aborting it. Ordinary analysis settings and the NASA cache are preserved. **Назад** keeps settings; **Повторить анализ** resubmits them. The Edge regression forces a late NASA-backed response to be read after reset and verifies the new answer stays intact.

OpenCode Frontend: `openai/gpt-6.1-sol#medium`, `ses_e268cef9affe5lrUIzWZzRttfc`; implemented cards and reset, verified TypeScript; estimate $0.

OpenCode QA: `opencode/inkling-2-small-free`, `ses_e268b681cffe1dnLguhKc5C2h4`; checked reset and request guards, flagged coverage recovery from the result step; estimate $0. Codex added a coverage retry on the result step.

Codex verification: 90 backend tests, four interpreter tests, TypeScript, production build, and both Edge browser suites passed. The build still reports the existing Plotly bundle size advisory.

## EarthScope 2.3 · Climate anomalies and seasons

- Added standalone anomaly and season/month workstations, CSV, and two guided Detective questions using the existing reset and abort handling.
- API uses one contiguous POWER fetch, the existing cache, annual aggregation and OLS. Strict reference completeness and winter year mapping are documented in [CLIMATE-API.md](CLIMATE-API.md) and [SCIENTIFIC_METHOD.md](SCIENTIFIC_METHOD.md).
- Independently reproduced the annual references, anomalies, seasonal values and OLS slopes from freshly downloaded public NASA POWER data. [Verification and figures](DEMO-VERIFICATION.md).
- No Git metadata existed. Saved the original source, tests and docs under `.tools/before-2.3-20261114-160307` for local rollback.

| Role and actual model | OpenCode session | Result and event estimate |
|---|---|---|
| Frontend `openai/gpt-6.1-sol#medium` | `ses_e267c96e2ffezrxTvbJw14eOcT` | Standalone modes, shared charts/results and client; $0 |
| Backend `opencode/deepseek-v4.1-flash-free` | `ses_e267c961bffeSDNs6YVVzYFY6Z` | API, schemas and integration tests; $0 |
| Data `opencode/glm-5.3-flash-free` | `ses_e267c96ccffefu5JN2n69i3lh4` | Daily transformations and deterministic fixtures; $0 |
| Science `nvidia/nvidia/nemotron-3-ultra-550b-a55b` | `ses_e267d447affeV6A1ySgZAvN4U5`, `ses_e267788a7ffeJmelvCQx2yMp1Z` | Reviewed completeness, DJF, units and exploratory inference; $0 each |
| QA `opencode/inkling-2-small-free` | `ses_e26764fafffev6Bzde9Pbd79zL` | Deterministic QA and review; $0 |

Model routes were preserved. The values are OpenCode estimates, not subscription usage.

Verification: 182 backend tests after QA removed two overlapping fixtures, four interpreter tests, TypeScript and production Vite build passed. The three Edge suites cover the new modules, all five Detective questions and the single-point/comparison regressions, including request timing, CSV and stale-response protection. The known Plotly size advisory and Starlette test-client deprecation remain.

## EarthScope 2.4 · Weather extremes

- Three threshold events, strict annual counts and fractions, diagnostics, CSV, optional A/B and guided hot-day analysis. The normalized trend uses HAC(3) with OLS and HAC(1/3/5) sensitivity. See [method](SCIENTIFIC_METHOD.md), [contract](EXTREMES-API.md) and [demo evidence](DEMO-VERIFICATION.md).
- Verified live source parameters and independently reproduced annual counts, fractions and slopes across six point/event combinations. Changing the threshold reuses the daily cache. Rollback snapshot: `.tools/before-2.4-20261114-162954`.

| Role and actual model | OpenCode session | Result |
|---|---|---|
| Frontend `openai/gpt-6.1-sol#medium` | `ses_e2663bc3effePDz12e3XTI38bG` | Standalone UI, comparison and CSV; estimated $0 |
| Backend `opencode/deepseek-v4.1-flash-free` | `ses_e2663bbd8ffe5UkRQ6SWLGQ9aE` | API, strict aggregation, schemas and tests; estimated $0 |
| Data `opencode/glm-5.3-flash-free` | `ses_e2663bbd7ffeMnPGEWqxu1YlZC` | Tmax/Tmin ingestion and offline cache tests; estimated $0 |
| Science `nvidia/nvidia/nemotron-3-ultra-550b-a55b` | `ses_e2664ae21ffekFZi7t7ayDaHqh`, `ses_e26622652ffekqJgGiuEpRJ8O4` | Reviewed primary method and actual code; estimated $0 each |
| QA `opencode/inkling-2-small-free` | `ses_e265ffd9affe07EfGvomivXL4W` | Code and test audit; estimated $0 |

Codex integrated the frequency statistics, chart and Detective workflow. OpenCode estimates are not provider billing records.

Verified: 262 backend tests, four interpreter tests, TypeScript and production build, four Edge suites for current and prior modes. Known limitations: exploratory HAC, no formal A/B difference test, Plotly bundle size advisory and existing Starlette test-client warning.


## EarthScope 2.5 — interactive regional climate trend map

- Added a bounded, Russian-language regional map integrated into the existing application navigation. A single stable Leaflet map draws the 209-cell observed grid with Canvas-backed rectangles; users can switch trend/significance layers, select cells from the map or keyboard list, inspect a yearly series, download layer/cell CSVs, and explicitly send a cell center to the existing single-point analysis.
- NASA POWER Regional Monthly/Annual API was live-probed and then called through the EarthScope backend. The area is 39-44 N, 69-81 E, split into sequential 6-degree longitude requests. Both parameters returned 209 unique cell centers at observed spacing 0.5 x 0.625 degrees, complete 1990-2025 values in all 209 cells, MERRA-2, API v2.10.0, LST, community AG. Raw tiles and processed layers are locally cached; a repeated identical request reports the processed cache hit.
- T2M uses NASA annual YYYY13 after requiring all 12 valid months. PRECTOTCORR annual totals sum each monthly mm/day rate times Gregorian month length. OLS slopes are descriptive from 5 complete years; HAC inference requires 27 complete consecutive years. HAC(1/3/5) sensitivity and per-variable/per-period Benjamini-Yekutieli q=0.10 are shown. These are not station measurements, causal findings, or map-wide simultaneous confidence statements.
- Verification: 104 regional/data/API/extremes-statistics tests passed in the scoped release run; the final full backend suite passed 363 tests; TypeScript and Vite production build passed with the existing >500 KB chunk advisory; the new real Edge desktop/mobile regional browser regression passed.
- Model work: Frontend OpenCode GPT-6.1 Sol Medium created five isolated regional UI files; DeepSeek V4.1 Flash implemented backend schemas, regional calculations and routes. Nemotron 3 Ultra reviewed the statistical approach before implementation. GLM-5.3 Flash was dispatched for the Data service, performed a live shape check and wrote ingestion/test files, but did not return a completed final handoff; Codex reconciled its tuple return interface and corrected concurrency/integration issues. Inkling reviewed code, tests, cache invalidation, error mapping and map lifecycle; its actionable cache/provenance, ignore, and label findings were fixed.
- No Git operations were used. A pre-change copy is available under `.tools/before-2.5-20261114-172425`. No deployment was performed.


### Final 2.5 verification update (November 14, 2026)

- The first browser launch exposed a real parameter-format mismatch: the endpoint accepted `start=1990&end=2025`, while the initial Data code sent full dates in the query and received HTTP 422. The request now uses year arguments; the response validator still requires full header coverage `19900101`-`20251231`. Reused the already-probed cached NASA layer; no unnecessary additional NASA traffic was made.
- Corrected the integration interface: Data returns `(RegionalCell[], source)` while Backend consumes a normalized cell payload. Backend now adapts the tuple. Request errors carry retryable status through the existing API envelope.
- Processed cache keys now include the NASA retrieval timestamp and request/source identity; raw source cache entries expire after 30 days. A source-stamp change invalidates derived analysis. `.cache/` is ignored. The selected-cell action explains that it opens the separate Daily UTC product rather than reproducing the regional LST values.
- Actual NASA-backed results at (39.0000 N, 69.3750 E), 1990-2025: T2M +0.331 °C/decade, pointwise HAC(3) 95% CI +0.118 to +0.543, p=0.00329, BY q=0.0741; HAC(1/3/5) p=0.00363/0.00329/0.00271. PRECTOTCORR +38.16 mm/decade, CI -22.94 to +99.25, p=0.213, q=1.0; HAC(1/3/5) p=0.138/0.213/0.233. These are one-cell estimates; the map uses BY q across 209 cells. Full-grid BY-significant counts were 59/209 for temperature and 48/209 for precipitation at q<=0.10. They are exploratory reanalysis results, not causal findings.
- Final automated checks: `pytest backend/tests` — 363 passed, one Starlette/httpx deprecation warning; frontend `tsc --noEmit` — passed; Vite production build — passed with existing large Plotly bundle advisory; real Edge browser test — passed desktop and mobile, cell selection, product/time handoff, drag/zoom request count and no horizontal overflow. Screenshots are in ignored `.tools/earthscope-2.5-regional-{desktop,mobile}.png`.

#### OpenCode execution record for 2.5

| Role | Exact model route | Actual outcome | Session / cost evidence |
|---|---|---|---|
| Research | `openai/gpt-6-luna` | Read official API documentation. Codex's direct probes established query limits and source format, beyond the Research note. | `ses_e263988d7ffepYahdTWoJYJk2`; $0 estimate |
| Science | `nvidia/nvidia/nemotron-3-ultra-550b-a55b` | Reviewed annual T2M/PRECTOTCORR aggregation, complete-year inference threshold, HAC sensitivity and BY correction before implementation. | `ses_e26345972ffef1nUkx3BFWt9aP`; $0 estimate |
| Backend | `opencode/deepseek-v4.1-flash-free` | Implemented regional response schemas, calculations, API routes and synthetic tests; Codex integrated Data interface and cache. | `ses_e262aab54ffeGuRNkBAP6js69t`; $0 estimate |
| Frontend | `openai/gpt-6.1-sol#medium` | Implemented isolated Russian regional map UI, persistent Canvas Leaflet map, cell detail/chart and CSV exports; reported TypeScript, build and mocked interaction checks passing. | OpenCode wrapper did not preserve a final session ID/cost line in the recorded handoff; do not infer a per-run estimate. |
| Data | `opencode/glm-5.3-flash-free` | Dispatched Data ingestion task and observed a live payload-shape probe. The invocation did not return a completed handoff. Codex completed the regional loader/cache, reconciled the tuple contract and fixed the query format after the live API returned 422. | No completed session summary or cost estimate recorded. |
| QA | `opencode/inkling-2-small-free` | Reviewed current files and tests; flagged processed-cache freshness, `.cache` ignore, retry status and UTC handoff labeling. Findings were addressed and rechecked. | `ses_e2610cfa2ffe7HtHHUbpl7mjUY`; $0 estimate |

No Git operations or deployment were performed. The rollback copy created before edits is `.tools/before-2.5-20261114-172425`.
