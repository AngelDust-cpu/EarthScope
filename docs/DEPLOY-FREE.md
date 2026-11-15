# Temporary hosting

## Cloudflare Pages

Connect AngelDust-cpu/EarthScope, main branch. Choose project name earthscope if available, then earthscope-lab. The assigned address depends on availability and is not reserved by this document.

- Root directory `frontend`
- Build command `npm ci --ignore-scripts && npm run build`
- Output directory `dist`
- Node.js 22
- Build variable `VITE_API_BASE_URL` must equal the deployed backend HTTPS origin

The `_redirects` file supports direct visits and refreshes on `/research` routes. Rebuild after changing the API origin.

## Koyeb

Connect the same repository. Build with the root Dockerfile, exposed HTTP port 8000, health check `/api/health`. Select the Free instance explicitly, one worker. Set `EARTHSCOPE_ALLOWED_ORIGINS` to the exact final frontend HTTPS origin. Set `PORT=8000` if the platform does not supply a port. Cache uses the ephemeral `/tmp/earthscope-cache` directory. Free resources and domain availability must be confirmed in the account before deployment.

The image includes Cyrillic fonts for PDF generation and locked production Python dependencies. Docker is unavailable in the current development environment, so image execution remains unverified until the remote build.

## Global map dependency

The Docker build runs the existing NASA POWER archive preparation script with numcodecs 0.16.5, writing `/app/data/global/annual.npz` into the image. A build fails if preparation fails. The same directory holds the ephemeral writable daily cache at runtime. The archive can be reconstructed from the image on restart. Local Windows calculation of both map variables measured about 387 MiB peak working set and 164 MiB retained memory. This excludes the full server's concurrent workload and is not a Linux/Koyeb measurement. Verify map and concurrent point analysis on the remote 512 MiB instance before sharing.

## Verification before sharing

Check health, real point analysis, profile partial errors, CSV/PDF and Cyrillic, heatwaves, direct `/research/profile` navigation, mobile layout, source attribution, and both world variables. Confirm exact CORS origin, resource limits and no paid instance. Set the final address only after the provider confirms deployment. An owned domain requires separate domain registration and DNS access.

Provider documentation checked 2026-11-15:

- https://www.koyeb.com/docs/reference/instances
- https://developers.cloudflare.com/pages/configuration/custom-domains/

Account creation and login are completed by the owner. Secrets must stay in provider secret settings.
