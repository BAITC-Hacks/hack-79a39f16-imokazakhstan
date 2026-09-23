# Frontend guide for AI agents

Follow the repository-root `AGENTS.md` and the person assigned in your task.
This directory contains the separately authorized React frontend. Its existence
does not transfer ownership of Person 1's data/weather modules, Person 2's
model/evaluation modules, or Person 3's shared contracts and integration files.

## Read first

1. The root `README.md`, `docs/architecture.md`, and
   `src/wind_forecast/contracts.py` for the Python interfaces and ownership.
2. [`README.md`](README.md) for running, testing, configuration, and known limits.
3. [`API.md`](API.md) for the exact browser-facing response contract.
4. [`INTEGRATION.md`](INTEGRATION.md) for upstream field mappings, missing inputs,
   implementation sequence, and integration acceptance checks.

## Code map

| File                                                     | Responsibility                                                                                        |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `src/App.tsx`                                            | Runtime config, selected day/turbine, loading and errors, comparisons, turbine details, weather cards |
| `src/types.ts`                                           | TypeScript display types; these do not replace the Python shared contracts                            |
| `src/data.ts`                                            | Offline synthetic fixtures, strict API validation, requests, matched-hour energy calculations         |
| `src/SiteMap.tsx`                                        | Offline schematic, turbine markers, selection, map-layer controls                                     |
| `src/StreetMap.tsx`                                      | Optional Leaflet/OpenStreetMap layer, tile-error handling, keyboard marker selection                  |
| `src/HistoryChart.tsx`                                   | Actual/predicted charts, accessible data table, provenance-labeled CSV export                         |
| `src/styles.css`                                         | Responsive layout and visual styles                                                                   |
| `src/data.test.ts`                                       | Numerical, schema, provenance, and time-boundary regression tests                                     |
| `e2e/dashboard.spec.ts`                                  | Desktop/mobile interactions, accessibility, offline behavior, API failures and missing readings       |
| `Dockerfile`, `nginx.conf`, `docker-entrypoint/start.sh` | Static build, non-root server, runtime JSON, optional same-origin API proxy                           |
| `compose.yaml`, `Dockerfile.backend`                     | Standalone frontend and optional existing Streamlit service                                           |

The current data paths are:

```text
demo: browser -> createDemoDashboard() -> validated display semantics -> components
api:  browser -> /api/dashboard -> nginx proxy -> local dashboard service
                                                   -> existing Python artifacts
```

The local dashboard REST service is now `wind_forecast.agent.dashboard`; see `docs/local_integration.md`. The historical handoff below predates this adapter. `app.py` is Streamlit;
`DASHBOARD_BACKEND_URL` only creates a navigation link. To connect data, an
integration service must implement the API and be reachable through
`DASHBOARD_API_UPSTREAM`. Do not point the JSON endpoint at the Streamlit UI.

## Preserve these behaviors

- Demo mode remains usable without internet or API keys. Only explicitly
  selecting Street loads external map tiles. Keep attribution visible there.
- Synthetic readings, statuses, forecasts, and event energy stay clearly labeled.
  API errors and timeouts must never silently substitute synthetic values.
- Site-day selection uses `Asia/Qyzylorda` (UTC+05:00), independent of browser
  timezone. Keep 24 interval-start rows; unfinished or missing readings are null.
- Energy comparisons use only completed hours with both actual and forecast
  power. A zero forecast total has no percentage. Do not assume a [0, 1] scale,
  rated capacity, MW/MWh, or a farm aggregation rule.
- Display each weather measurement from its own latest available interval;
  missing power must not hide independently available weather readings.
- Wind direction is circular; avoid drawing a misleading line through the
  0/360-degree boundary. Keep keyboard selection and the accessible data table.
- The server must verify provenance, issue-time availability, and SCADA mapping.
  A client-side `verified_original` label is not independent source evidence.
- Credentials remain on the server. Runtime JSON and browser assets are public.

## Verification and current status

Use the commands in `README.md`. The checked implementation passed a production
build, 41 data tests, 16 desktop/mobile browser tests, and formatting checks.
Default and backend-profile Compose configurations were validated. Image builds
and container health checks remain outstanding because the local Docker Linux
engine did not respond; do not describe those checks as completed.

Run tests appropriate to your changes. Use the production browser suite for
interaction, rendering, or API-boundary changes. Keep generated files, real data,
`.env`, browser artifacts, `node_modules`, and `dist` out of commits.

## Starting prompt for an integration agent

> Analyze the turbine dashboard and connect it to the existing application's
> evidenced observation, weather, and numerical forecast artifacts. Start with
> the root ownership instructions and frontend/AGENTS.md, README.md, API.md, and
> INTEGRATION.md. Identify your assigned person before editing. Person 3 owns the
> server adapter and shared integration changes; coordinate with the data/model
> owners rather than rewriting their modules. Inventory available artifacts and
> confirm turbine mapping, units, interval labels, availability evidence, and
> forecast-issue selection. Implement the documented JSON endpoint within your
> authorized scope, preserving nulls, provenance, and the offline demo. If only
> historical archives exist, do not relabel them as today: document the input gap
> or agree an explicit historical-date extension first. Connect the API through
> the existing runtime configuration and Docker network, then run the documented
> acceptance checks. Report the exact sources used, tests actually executed, and
> any remaining input or deployment gaps. Do not fabricate missing measurements,
> precipitation events, operating status, or predicted energy.
