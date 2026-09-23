# Wind operations dashboard

A React and TypeScript dashboard for the two turbine sites, with an offline map,
today/yesterday comparisons, observed and predicted time series, and a 48-hour
weather outlook. The default dataset is explicitly synthetic. Its illustrative
values and weather impacts are not measurements or operational forecasts.

AI agents should start with [`AGENTS.md`](AGENTS.md) for the code map, required
reading, behavioral constraints, and a reusable integration-agent prompt.

## Run with Docker

Install Docker Engine or Docker Desktop with Docker Compose v2. From the repository root:

```sh
docker compose -f frontend/compose.yaml up --build -d
```

Open [the dashboard](http://localhost:8080). The default app runs without API keys,
external map tiles, or network access after the image is built. Image builds need
internet access to fetch base images and dependencies.

```sh
docker compose -f frontend/compose.yaml logs -f frontend
docker compose -f frontend/compose.yaml down
```

To run the existing Python/Streamlit app alongside it:

```sh
docker compose -f frontend/compose.yaml --profile backend up --build -d
```

Streamlit is available at [localhost:8501](http://localhost:8501). It retains the
repository's existing synthetic workflow. The frontend can link to it when
`DASHBOARD_BACKEND_URL` is set. Streamlit does not implement the dashboard REST
endpoint; use the API contract below when integrating verified measurements and
model output.

## Configure a deployment

Environment variables are read when the container starts, so one built frontend
image can be used in different environments. Create `frontend/.env` if using
Compose (keep local configuration out of Git):

```dotenv
DASHBOARD_PORT=8080
STREAMLIT_PORT=8501
DASHBOARD_DATA_MODE=demo
DASHBOARD_BACKEND_URL=http://localhost:8501
```

The backend link must be a URL reachable from the user's browser. Replace
`localhost` with the deployed host when sharing the application. Clear the value
to hide the link.

| Variable                 | Default      | Purpose                                                                                  |
| ------------------------ | ------------ | ---------------------------------------------------------------------------------------- |
| `DASHBOARD_PORT`         | `8080`       | Compose host port for the frontend.                                                      |
| `STREAMLIT_PORT`         | `8501`       | Compose host port for optional Streamlit.                                                |
| `DASHBOARD_DATA_MODE`    | `demo`       | `demo` or `api`.                                                                         |
| `DASHBOARD_API_UPSTREAM` | empty        | API server origin such as `http://api:8000`; required in API mode.                       |
| `DASHBOARD_BACKEND_URL`  | empty        | Optional visible HTTP(S) link to the existing app.                                       |
| `DASHBOARD_DNS_RESOLVER` | `127.0.0.11` | Docker DNS resolver, used only by the API proxy. Override for other container platforms. |

For API mode, provide an integration service implementing [API.md](API.md), then
set `DASHBOARD_DATA_MODE=api` and `DASHBOARD_API_UPSTREAM` to its HTTP(S) origin.
The frontend calls `/api/dashboard?period=today` or `period=yesterday`; nginx
forwards the full path and query to the configured service. The upstream must be
reachable from the container, for example through a shared Docker network.
An integration service is not included in this frontend change.

The upstream accepts an origin only, with a hostname or IPv4 address and optional
port. Do not include a path or credentials. The optional backend link also
rejects embedded credentials. Only public display settings are exposed through
`/runtime-config.json`; keep provider/API credentials in the server environment.
HTTPS upstream certificate verification is enabled. API failures remain visible;
they never silently switch the UI to synthetic data.

The standalone demo has no backend startup dependency. API hostnames are resolved
when a request is made, so nginx can start before an integration service is ready.
The `/healthz` endpoint reports frontend server health, not upstream API health.

For a single image without Compose, run from the repository root:

```sh
docker build -t wind-dashboard ./frontend
docker run --rm -p 8080:8080 --read-only --tmpfs /tmp:rw,size=16m,mode=1777 wind-dashboard
```

The image serves the compiled app using nginx on port 8080 as a non-root user.
Compose also drops Linux capabilities and mounts the frontend filesystem read-only
with a writable temporary directory. Put the service behind your deployment's
HTTPS ingress. Runtime configuration and the app entry document are not cached;
hashed Vite assets receive long-lived caching. Deploy at the site root (`/`).

## Local frontend development

Use Node.js 22 (22.12 or later), matching the container build:

```sh
cd frontend
npm ci
npm run dev
```

The development server reads `public/runtime-config.json`, which defaults to
demo mode. For a local API, edit that local file to use `dataMode: "api"` and an
absolute `apiUrl` with suitable API CORS rules, or provide a development proxy.
Container environment settings apply to the built nginx image rather than Vite.

```sh
npm test
npm run build
```

## Browser tests

From `frontend/`, install the test browser and run the suite against the production
build. Playwright starts the preview server on port 4173:

```sh
npm ci
npx playwright install chromium
npm run build
npm run test:e2e
```

You can use an existing Microsoft Edge installation instead of downloading
Chromium. After installing dependencies and building, run in PowerShell:

```powershell
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm run test:e2e
```

Or in a POSIX shell:

```sh
PLAYWRIGHT_CHANNEL=msedge npm run test:e2e
```

If automatic preview-server cleanup stalls on Windows, start
`npm run preview -- --host 127.0.0.1 --port 4173 --strictPort` in a separate
terminal first. The test suite reuses that server; stop it with Ctrl+C afterward.

Validated in this workspace: the production build, 41 data unit tests, and 16
desktop/mobile browser tests passed. Browser coverage includes keyboard map
selection, all chart metrics, CSV export, weather filters, offline operation,
API errors/timeouts, incomplete observations, and automated WCAG A/AA checks.
Use `npm run format:check` to check source formatting.

## Data and integration limits

Both locations use the repository's source-site coordinates. Turbine-ID mapping,
SCADA time conventions, normalization, and real output capacities still need
confirmation before connecting real data. The demo uses normalized output units;
do not interpret them as MW or MWh. Weather event cards are illustrative scenarios
in demo mode. Verified event forecasts and energy numbers must come from the API.

The frontend only displays artifacts supplied through [the API contract](API.md).
It does not train a model, reinterpret normalized power as physical energy, or
change shared Python contracts. Integration must preserve issue times, missing
observations, and provenance. See [the data handoff](../docs/data.md) and
[repository architecture](../docs/architecture.md).
The [Person 3 integration handoff](INTEGRATION.md) describes the remaining server
adapter work and deployment acceptance checks.

## Container verification

Before deploying an image, run the Compose build and inspect service health:

```sh
docker compose -f frontend/compose.yaml --profile backend up --build -d
docker compose -f frontend/compose.yaml ps
curl --fail http://localhost:8080/healthz
curl --fail http://localhost:8080/runtime-config.json
curl --fail http://localhost:8501/_stcore/health
```

The installed Docker CLI validated both the default and backend-profile Compose
configurations. Its Linux engine did not become responsive after starting the
existing Docker Desktop application, and Ubuntu WSL integration was unavailable.
Image builds, Compose startup, and container health checks have not been executed
here.
The entrypoint passed a shell syntax check, and its actual jq validation rules
passed 18 cases covering valid configuration, credentials, control characters,
and configuration injection. JSON escaping also passed a quoted-URL round trip.
