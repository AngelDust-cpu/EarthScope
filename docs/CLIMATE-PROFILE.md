# EarthScope 2.8 Climate Profile

Worktree `C:/Users/15dav/earthscope-profile`, branch `feature/climate-profile`, base `3003d3f`.

## Architecture and scope

The profile aggregates existing NASA POWER ingestion, annual OLS analysis, temperature anomalies, four meteorological temperature seasons and absolute-threshold extremes. Scientific calculations remain in the existing services. A profile uses at most four daily loads over one expanded interval. The expanded interval includes the display years, baseline years and preceding December for winter, bounded by the existing 1981 coverage start. Each source retains its request URL, dataset, retrieval time, cache flag and UTC convention. Incomplete years retain completeness diagnostics and null results.

The default anomaly reference is 1991–2020. A shorter custom reference is a user reference period. The app requires every baseline year to be complete. This is an application rule. Annual temperature and precipitation summaries use equally weighted complete annual values. A common requested period may still produce different sets of complete years across variables and seasons. Their counts and gaps must be inspected before interpreting differences. Seasonal rankings describe estimated slopes and do not test differences between seasonal slopes.

Absolute defaults are daily maximum >30 °C, daily minimum <0 °C, daily precipitation >20 mm/day. Equality is excluded. They are configurable research thresholds, with no calibrated risk classification. Extreme count trends and trends normalized to 365 days use different units. Nominal OLS intervals and p-values assume approximately independent, constant-variance residuals. Autocorrelation and exploratory multiple comparisons limit inference. Missing statistical support does not establish absence of change.

## Dependencies and integration

This base contains the existing `report_pdf.py` renderer, unlike the separately evolving PDF feature in the main working directory. The profile PDF adapter reuses that renderer. When integrating with the PDF branch, verify the adapter against that branch's final renderer API. Do not copy implementations from another worktree. Heatwaves have no module in this base and return an explicit unavailable section. Integrating a future heatwave service requires a shared contract and its original algorithm.

Frontend ownership covers `frontend/**`. Backend ownership covers `profile.py`, `profile_schemas.py`, `profile_pdf.py`, and `test_profile.py`. Codex integrates main route registration and documentation. Science and QA perform read-only reviews. No merge or public deployment is part of this task.

## Source provenance

NASA POWER Daily API documentation accessed 2026-11-15, https://power.larc.nasa.gov/docs/services/api/temporal/daily/. Public API, no account required for these queries. Daily API defaults to local solar time, so the existing client explicitly requests UTC. Parameters retain original source resolution. Source licensing should be checked against NASA POWER terms for redistribution, which this task has not independently revalidated. Runtime data are gridded reanalysis-derived estimates and do not represent a station measurement within a city.

The existing project identifies the selected challenge as Earth System Trend Detective, https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/. Access attempt on 2026-11-15 returned HTTP 502, so new challenge requirements could not be independently revalidated.

## Development checks

Use Python 3.11+, `uv sync --locked`, and `npm ci --ignore-scripts` in frontend. Run backend tests from this worktree with its backend on Python's path, then production build and Detective unit tests. Run this worktree's backend on an unused port and its frontend with a matching API origin to avoid the parallel PDF application.

A dependency audit of the existing lockfile on 2026-11-15 reported a high-severity Vite advisory for the pinned 6.4.1 dependency, with 6.4.6 offered as a patch fix. Dependency updates require a separate reviewed change. Local development must remain loopback-bound.

## API and exports

`GET /api/climate-profile` takes latitude, longitude, start_year, end_year, baseline_start_year, baseline_end_year, hot_threshold, cold_threshold and rain_threshold. It returns the contract in CLIMATE-PROFILE-CONTRACT.md. Each section has status, data and message. Partial failures retain successful sections with HTTP 200. Failure of all network-backed sections returns HTTP 503 in the standard error envelope. Invalid inputs return HTTP 422 before data loading. The baseline may extend beyond the display years. Up to four load_daily calls are sequential; the existing TLS-only retry can produce up to two HTTP attempts per load.

`Accept: application/pdf` negotiates a unified profile PDF. `GET /api/climate-profile.csv` produces UTF-8 BOM CSV with comment metadata, limitations, section status, annual values, completeness, units, estimates and uncertainty where available. Empty values are nulls. Exports repeat the same profile query against the current source/cache, so a future source update can change a downloaded result. Runtime uses no LLM API.

The PDF adapter composes existing render_report outputs with a profile cover when pypdf is available. Without the optional dev dependency pypdf, it uses existing ReportLab hooks for one profile document. Missing renderer hooks produce an explicit PDF_UNAVAILABLE error while JSON remains available. Private hooks require a compatibility check with the parallel PDF branch.

## Run alongside the PDF worktree

From this worktree, after installing dependencies:

```powershell
$env:EARTHSCOPE_CACHE_DIR = Join-Path (Get-Location) '.earthscope-cache'
uv run --locked uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8018
```

In a second terminal in this worktree:

```powershell
$env:VITE_API_BASE_URL = 'http://127.0.0.1:8018'
cd frontend
npm run dev -- --host 127.0.0.1 --port 5188 --strictPort
```

Open http://127.0.0.1:5188/. Select Климатический профиль or the new Detective scenario. Production builds require VITE_API_BASE_URL to be set at build time.

## Verification performed 2026-11-15

- Full pytest: 425 passed, one pre-existing Starlette/httpx deprecation warning. Test data are explicitly synthetic fixtures.
- Profile-specific pytest: 42 passed, including input guards before network calls, four-call reuse, partial and total failures, missing baseline days, winter bounds, response coherence, CSV and PDF fallback.
- Frontend: TypeScript check and production build passed, 11 Node tests passed (7 profile and 4 existing Detective).
- Real-data browser scenario: Bishkek 42.87, 74.6, 2015–2020 with the same custom baseline. All seven analytical sections returned real NASA POWER results, source parameter identity was checked, CSV and PDF downloaded successfully. Heatwaves unavailable.
- Browser: no analysis on map or preset selection, stable map element across selection, 390 px mobile layout without horizontal overflow, no page errors. Explicit intercepted partial-error and delayed-response simulations verified section resilience and Detective reset.
- PDF: rendered every page with Poppler and visually inspected the contact sheet. All seven successful sections represented. Sample data are reanalysis estimates, with provenance preserved.

Existing PDF defects were corrected in this worktree during integration: fonts registered before Paragraph construction, comparison/extreme metadata use the correct model fields, API method/variable/season identifiers retained, and fitted extreme rates kept in their existing days-per-365-days units. The existing numeric tests now recognize the renderer's Russian decimal comma. These small renderer/test changes need particular review against the separately evolving PDF branch.

NASA meteorological sources documentation accessed 2026-11-15, https://power.larc.nasa.gov/docs/methodology/data/sources_meteorology/. It documents the primary MERRA-2 grid as 0.5° latitude by 0.625° longitude and identifies near-real-time GEOS-IT continuation. Per-response dataset attribution is preserved rather than assuming every returned source is identical.

## Integration checklist

The implementation remains on feature/climate-profile. Review its commit against the target branch, especially main.py registration, App/Detective navigation, and the small report_pdf.py corrections. Compare the PDF adapter's required hooks with the final PDF feature API. Run the full pytest suite, Node tests, build and browser scenario after resolving those interfaces. Merge only on the user's instruction.

Changed files are README.md; backend/app/main.py, profile.py, profile_schemas.py, profile_pdf.py, report_pdf.py; backend/tests/test_profile.py, test_report_pdf.py; frontend/src/App.tsx, components/DetectiveMode.tsx, components/ProfileMode.tsx, components/ProfileResults.tsx, lib/profile.ts, lib/ru.ts; frontend/tests/test_profile.mjs, test_profile_browser.cjs; docs/CLIMATE-PROFILE-CONTRACT.md, CLIMATE-PROFILE-ROUTING.md, CLIMATE-PROFILE.md.

## Интеграция с main

При объединении сохранены изменения PDF и heatwaves из main. Сценарии Детектива поддерживают и профиль, и волны жары. Единый профиль пока сохраняет статус heatwaves unavailable, а существующий модуль доступен отдельно через Погодные экстремумы → Волны жары. Текст профиля и PDF уточнён под объединённую версию.

Проверки после объединения: 464 backend-теста, 100 повторных тестов профиля/волн жары/PDF, 11 клиентских тестов, TypeScript и production build прошли. Браузерный профиль на реальных NASA-данных проверен на объединённом приложении http://127.0.0.1:5189/ с API на порту 8019, включая CSV/PDF, частичную ошибку, сброс Детектива и мобильную ширину 390 px.
