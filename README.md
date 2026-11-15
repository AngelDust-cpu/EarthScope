# EarthScope

EarthScope is an interactive Earth System Trend Detective dashboard for investigating how annual air temperature and precipitation change at a selected location. It uses daily NASA POWER gridded meteorology and reports annual values, a fitted trend per decade, a 95% slope interval, and an exploratory significance test.

## Run the dashboard

On this prepared Windows workspace, use the existing local runtimes. From the project root, open two PowerShell terminals:

```powershell
$env:EARTHSCOPE_CACHE_DIR = Join-Path (Get-Location) '.earthscope-cache'
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload --reload-dir backend/app
```

```powershell
$node = (Resolve-Path .tools/node/node-v22.16.0-win-x64).Path
$env:Path = "$node;$env:Path"
Set-Location frontend
& "$node/npm.cmd" run dev
```

For a fresh checkout, install Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and Node.js 22 with npm. From the project root:

```powershell
uv sync --locked
$env:EARTHSCOPE_CACHE_DIR = Join-Path (Get-Location) '.earthscope-cache'
uv run --locked uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload --reload-dir backend/app
```

In a second terminal:

```powershell
cd frontend
npm ci --ignore-scripts
npm run dev
```

Open [EarthScope locally](http://localhost:5173). Use **Одна точка** for a single analysis or **Сравнение A / B** for two. In comparison mode, choose the active point before using the map, typing coordinates, or selecting a preset. A defaults to Bishkek, B to Reykjavík. Choose a common variable and year range, then click **Провести анализ**. The chart can show absolute values or anomalies relative to shared baseline years. The paired slope difference is descriptive. See the [scientific method](docs/SCIENTIFIC_METHOD.md) and [demo script](docs/DEMO.md). The first uncached analysis requires access to NASA POWER. The bundled [Natural Earth basemap](https://www.naturalearthdata.com/about/terms-of-use/) provides geographic context; it does not show NASA POWER grid boundaries.

Run `uv run --locked pytest --basetemp .tools/pytest-local` from the root and `npm run build` from `frontend`. [API and methods](docs/API.md) · [Progress and verification](docs/PROGRESS.md) · [Demo and submission draft](docs/DEMO.md)

For the desktop and mobile frontend regression checks, run `python frontend/tests/test_workstation.py` with Python Playwright and Chromium installed, while both local servers are running. Set `EARTHSCOPE_BROWSER_EXECUTABLE` to use an existing Chromium-based browser. The suite uses the real backend, with live or cached NASA POWER data, and intercepts individual requests for error and cancellation tests.

## Guided investigation: «Детектив»

**Погодные экстремумы** (2.4): choose hot days, cold nights (UTC daily minimum) or heavy precipitation, then set a threshold and year range. Read annual counts and fractions, coverage, frequency trends and their sensitivity to autocorrelation. CSV includes the threshold, source and fit metadata. The guided hot-days question is also available in **Детектив**. See the [scientific method](docs/SCIENTIFIC_METHOD.md) and [API contract](docs/EXTREMES-API.md).

Enable **Добавить точку B** in the extremes mode to compare regions using a shared threshold and period. The comparison is descriptive. With the local servers and browser tools configured, run `python frontend/tests/test_extremes_browser.py` for the real-data smoke tests.

EarthScope 2.3 adds **Климатические аномалии** and **Сезонный анализ**. Select a point, temperature or precipitation, and the years to display. For anomalies, select 1991–2020 or enter a custom reference range: all years in the reference must be complete. For seasonal analysis, choose a fixed calendar season, one of twelve months, or compare all four seasons. Winter uses the preceding December. Both modes offer CSV downloads. The Detective wizard also guides users through unusual temperature years and seasonal trend comparisons. [API contract](docs/CLIMATE-API.md)

Select **Детектив** and click a temperature, precipitation, or region comparison card to start choosing coordinates. **Назад** keeps your settings. **Повторить анализ** reruns the current study, while **Новое исследование** clears the wizard and returns to the scenario cards even during loading. Ordinary analysis and the NASA cache are preserved. The result includes a chart, a Russian explanation, and NASA POWER source details. The explanation uses fixed templates and the existing API statistics. See the [scientific method](docs/SCIENTIFIC_METHOD.md).

Run the interpreter checks with `node --test frontend/tests/test_detective.mjs` after installing frontend dependencies. With both servers running and the same Playwright setup as for the workstation tests, run `python frontend/tests/test_detective_browser.py` to exercise the three questions.

Run `python frontend/tests/test_climate_browser.py` for the climate workflows. Use the documented POWER cache setting when preparing a demo; seasonal requests that include winter may need an additional preceding year.

## Hosting preparation

For Vercel or Cloudflare Pages set the frontend root to `frontend`, the build command to `npm run build`, and the output directory to `dist`. Set `VITE_API_BASE_URL` to the backend HTTPS origin at build time. On a Python host, install with `uv sync --locked --no-dev` and start `uv run --no-sync uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port <host-port>`, replacing `<host-port>` with the host-provided port. Set `EARTHSCOPE_ALLOWED_ORIGINS` to the comma-separated frontend origins and `EARTHSCOPE_CACHE_DIR` to a writable directory for verified POWER responses. Repeat queries can use those responses offline when the local backend is running and the exact coordinates, variable, and dates match. The reported retrieval time refers to the original download.

## Data and interpretation

EarthScope runs without an AI provider account, an API key or a paid data service. NASA POWER is publicly accessible, the basemap is bundled, and the application computes statistics locally. OpenCode is used during development. [NASA POWER access](https://power.larc.nasa.gov/resources/power-one-pager.pdf) · [Final demo verification](docs/DEMO-VERIFICATION.md)

### Scientific PDF reports

After a successful analysis, select **Сформировать PDF-отчёт**. Reports cover
point trends, point comparison, anomalies, seasonal and monthly analysis,
weather extremes, and the corresponding Detective investigations. On the
global map, select a cell to export its original annual series and statistics.
The full map and its interpolated display layer are outside the PDF export.

The API renders the PDF in memory with ReportLab. It reuses the analysis
endpoint and validated parameters, including the NASA data cache when
configured. No language model runs during export. Install a Cyrillic-capable
Arial or DejaVu Sans font on the backend host before enabling PDF exports.
API callers can send `Accept: application/pdf` to the matching analysis
endpoint with the same query parameters or JSON body. A PDF reruns the
validated analysis against the currently available dataset. Record the
result and source metadata if you need to reproduce an exact historical
export after an upstream data or software update.

NASA POWER daily `T2M` is the 2-meter air temperature. EarthScope averages complete UTC years in °C. Daily `PRECTOTCORR` is corrected precipitation in mm/day. EarthScope sums it over the days in a complete year to report annual totals in mm. These are gridded, model-derived MERRA-2 estimates. Local station conditions can differ. The app omits incomplete years, requires at least five complete annual values, and fits an ordinary least-squares line with an intercept. Its interval and p-value assume approximately independent, constant-variance residuals. Autocorrelation, short periods and exploring many locations can make the nominal significance misleading. The trend is descriptive and does not establish a cause. [NASA POWER Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) · [Challenge brief](https://www.spaceappschallenge.org/2026/challenges/earth-system-trend-detective/)

## Delegate from Codex CLI

Open Codex in the project folder and ask it to assign tasks to OpenCode specialists. For example, ask it to have QA review `opencode.json` without edits and report the model, session, and estimated cost.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-Specialist.ps1 -List
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-Specialist.ps1 -Role qa -Task 'Review opencode.json and report findings. Do not edit.'
```

The wrapper uses the running OpenCode Desktop CLI when present. It reads the explicit agent model and checks the live catalog before dispatch. The cost comes from OpenCode run events and is an estimate. The execution policy applies only to the child PowerShell process.

| Work | Role | Model |
|---|---|---|
| Architecture and integration | Codex CLI | Model selected in the Codex session |
| Sources and requirements | research | GPT-6 Luna via OpenAI (temporary) |
| Formulas, units, and uncertainty | science | Nemotron 3 Ultra 550B-A55B via NVIDIA |
| Data processing | data | GLM-5.3-Flash Free |
| APIs and algorithms | backend | DeepSeek V4.1 Flash Free |
| Interface and visualizations | frontend | GPT-6.1 Sol Medium via OpenAI |
| Review and tests | qa | Inkling 2 Small Free |
| Documentation, pitch, and demo | pitch | GPT-6 Luna via OpenAI |

The in-app `orchestrator` entry is disabled pending a verified ChatGPT OAuth Codex route in OpenCode. Codex CLI can dispatch the specialists directly.

> Current specialized routing and runtime evidence:
> [MODEL-ROUTING.md](MODEL-ROUTING.md). Earlier audits below are historical.
> Explicit model pins prevent specialists from inheriting the primary model.
> Free inference is not guaranteed for every existing provider.

## Readiness on November 14, 2026

The workspace was empty at discovery. It now has an OpenCode 2.2.7 configuration, a primary orchestrator, and seven specialist agents. The Windows x64 baseline CLI lives under `.tools/opencode/`, which Git ignores. The release archive matched the publisher's SHA-256 digest. OpenCode loaded the configuration and resolved all eight project agents. [OpenCode release](https://github.com/anomalyco/opencode/releases/tag/v2.2.7)

- **Delegation passed.** OpenCode recorded completed child sessions for Research, Science, Data, Backend, Frontend, QA, and Pitch. All returned their expected markers. Four marker replies added punctuation. QA also read `opencode.json` and returned the expected settings using its final model assignment.
- **Free inference passed.** The catalog exposed 12 models from `opencode`. DeepSeek V4.1 Flash Free is the project default. QA uses Inkling 2 Small Free. The other specialists inherit the parent session model. GLM-5.3-Flash Free returned a live response and can be selected as a fallback. Codex, Gemini, GPT-OSS, Qwen, Particle-1, and ERNIE were absent from the project's OpenCode catalog. The CLI reported zero authenticated integrations and zero configured MCP servers. [OpenCode model selection](https://opencode.ai/docs/models) · [Agent model inheritance](https://opencode.ai/docs/agents#model)
- **NASA access is partial.** NASA's official catalog lists 14 challenges. OpenCode's direct fetch returned a transport error and Windows curl rejected the catalog certificate chain. The headless web search also requested input. Use the official brief in a browser, or provide a local copy of the official brief to Research if OpenCode cannot fetch it. [NASA 2026 challenges](https://www.spaceappschallenge.org/2026/challenges/?linkId=1029316445)
- **Challenge selection is pending.** The workspace had no selection document or challenge brief. To start planning, record the team's selected challenge title and official URL. Research can then extract requirements and confirm data access. Application code, scientific results, and submission materials are pending that selection. Git and a Git repository were absent during discovery, so worktree-based development was unverified.

## Run from the project root

Open PowerShell in this directory and launch OpenCode.

```powershell
$opencode = '.\.tools\opencode\opencode.exe'
& $opencode --standalone
```

On another Windows x64 machine, obtain the baseline archive from the linked release, check it against the published SHA-256, and extract `opencode.exe` to the same location. If OpenCode is already on PATH, set `$opencode = 'opencode'`.

To verify discovery and a live child response, run these commands from the same directory. The JSON output should include a completed `subagent` tool call for `qa` and the marker `QA-LIVE` in its output.

```powershell
& $opencode debug config --standalone
& $opencode debug agents --standalone
& $opencode models --standalone
& $opencode run --standalone --archived --format json 'Call the qa subagent once. Ask it to read opencode.json and report the default_agent value and QA-LIVE. Wait for its result. Use no shell or editing tools.'
```

Use `/models` in the interactive session to switch the parent model. If a pinned model is withdrawn, verify a replacement through the live catalog and a smoke test before updating the project model in [opencode.json](opencode.json) or the QA override in [.opencode/agents/qa.md](.opencode/agents/qa.md). The workflow and delegation rules are in [AGENTS.md](AGENTS.md).


## EarthScope 2.5: interactive regional trend map

Choose **Весь мир** in the map's region selector to open the global overview. It covers 1990–2025 at NASA POWER's native 0.5° × 0.625° grid spacing, with temperature, precipitation, and a global BY significance layer. The user interface clips the polar caps at ±85.05° in Web Mercator. Click a cell to view its annual series and trend statistics. The world uses NASA's annual LST archive, while the Kyrgyzstan layer retains its separate monthly/annual regional pipeline and selectable years.

For a new installation, prepare the local archive from the project root with `uv run --with numcodecs==0.16.5 python scripts/build_global_power.py`. The preparation downloads 36 annual fields per variable from the [NASA POWER public archive](https://registry.opendata.aws/nasa-power/) and writes `global/annual.npz` under `EARTHSCOPE_CACHE_DIR` (default `.earthscope-cache`). Account credentials are unnecessary. Only the preparation script needs `numcodecs`. Startup, panning, and zooming use the local archive. World-level precipitation totals use NASA's annual mean daily rate multiplied by the number of calendar days. The global color scale saturates at the 98th percentile of absolute slopes. The cell panel reports the fitted value at full source precision before display rounding.

A bounded map is available for 39-44 deg N and 69-81 deg E. It shows real NASA POWER Regional Monthly/Annual MERRA-2 cell centers, with temperature trends in deg C/decade or annual precipitation trends in mm/decade. The selected annual period defaults to 1990-2025. Press **Build map** to request a layer; moving, zooming, and selecting cells do not call NASA. Select a cell to inspect its yearly values, trend, confidence interval, p-value, BY-adjusted q-value, completeness, and provenance. The selected center can be passed to the existing point analysis explicitly.

The NASA regional product is an LST monthly/annual product and is not identical to the existing Daily Point UTC product. The first full live query returned 209 cells with 36 complete annual values per cell for both variables; later layers use the local raw-response and processed-analysis caches. The map is a limited demonstration region, not global coverage. Statistical limits and aggregation rules are in [the regional API contract](docs/REGIONAL-MAP-API.md) and [scientific method](docs/SCIENTIFIC_METHOD.md).

Local checks for this milestone: `python -m pytest backend/tests/test_regional_data.py backend/tests/test_regional_map_api.py backend/tests/test_extremes_stats.py`; frontend `tsc --noEmit` and Vite production build. These tests use synthetic fixtures explicitly isolated from production data. Real observations are only read from NASA POWER.
### Heatwave analysis

Open **Погодные экстремумы → Волны жары**. Set the daily maximum
temperature threshold and minimum duration, then run the analysis.
The three charts describe annual frequency, total duration and maximum
duration. Choose a year to see individual episodes. Export the annual
summary and episode list as CSV or generate a scientific PDF. The same
analysis is available in **Детектив**.

Episodes that cross New Year belong to the year in which they start.
Incomplete years are excluded from yearly statistics and descriptive
trends. See [the scientific method](docs/SCIENTIFIC_METHOD.md).

