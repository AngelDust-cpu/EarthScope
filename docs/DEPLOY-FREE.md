# Temporary Railway deployment

Public site: https://earthscope.up.railway.app

The Railway project `endearing-insight` runs the `EarthScope` service from the public GitHub repository `AngelDust-cpu/EarthScope`, branch `main`. The deployed application commit is `69ff842`.

## Build and runtime

The root Dockerfile builds the Vite frontend with Node.js 22 and installs locked Python dependencies with Python 3.12. FastAPI serves the frontend and API on the same origin. Direct `/research` routes return the SPA entry point. The server listens on Railway's injected `PORT`, with one worker. `railway.json` configures Docker builds and `/api/health` as the health check.

The image includes DejaVu fonts for Cyrillic PDF export. During the build, `scripts/build_global_power.py` prepares NASA POWER annual data at `/app/data/global/annual.npz`. A failed archive preparation fails the build. Daily request caches use the writable ephemeral `/app/data` directory. A new container starts with the baked annual archive.

## Updating

The service was created using the public repository URL. In the Railway service settings, use **Check for updates**, review the upstream commit, and select **Update**. Automatic deployment from GitHub has not been configured. Check the deployment logs and health status after each update.

## Free trial

The account displayed a $5 trial credit valid for up to 30 days on 2026-11-15. Usage can exhaust the credit earlier. Continued operation depends on the account's current limits and remaining credit. The chosen address is a Railway-provided subdomain. No purchased domain was connected.

## Verification

On 2026-11-15, the public homepage and direct `/research/profile` route returned HTTP 200. `/api/health` returned HTTP 200. A real NASA POWER temperature trend for Bishkek, 2015–2020, exported a two-page PDF with extractable Cyrillic text over HTTPS.

## Data source

NASA POWER annual archive source: https://registry.opendata.aws/nasa-power/ (accessed 2026-11-15). Point requests use NASA POWER. Preserve source attribution and scientific limitations shown in each analysis.
