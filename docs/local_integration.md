# Local wind and power integration

The React dashboard and Streamlit now use the saved CatBoost power and ExtraTrees
wind models in `local_history` mode. Weights and hash metadata are committed.
Real CSVs, environment files, generated forecasts, caches and virtual environments
remain local. The offline synthetic demo and the upstream NOAA/ML/Jev paths remain
available independently.

## Run locally

Use Python 3.11/3.12; install `python -m pip install -e '.[app,local]'`.
Place original CSVs at `data/raw/turbine_1.csv` and `data/raw/turbine_2.csv`.
`examples/local_request.json` records UTC+5, interval-start measurements, hourly
interval-end predictions, daily 06:00 issue, and the historical February 1 example.
These conventions were selected by the operator; they are not independent source attestation.

```sh
# Build the React site once
cd frontend
npm ci
npm run build
cd ..
# Serve API + compiled React together; no external weather or API credentials
PYTHONPATH=src python -m wind_forecast.agent.dashboard --config examples/local_request.json
# Alternative existing interface
python -m streamlit run app.py
```

Open http://127.0.0.1:8000. Select archive February 1, 2 or 3 to inspect the full
48-hour issue. The issue selector uses UTC+5; selecting another date does not
relabel measurements or retrain weights. Invalid/pre-training/stale issues return
an explicit error. The current CSVs end January 31, so February actuals remain null.

The backend maps T1 → turbine_1 and T2 → turbine_2 using the provided turbine file
labels and case coordinates; independent physical-ID attestation is not claimed.
It subtracts one hour from backend interval-end times to produce React interval
starts. Normalized power stays n.u.; energy comparisons use completed matched hours
in n.u.·h, never MW/MWh. Unknown operational status is explicit. Temperature,
wind direction, rain/snow/icing and event penalties are not fabricated.
`local_scada` provenance explicitly distinguishes these inputs from independently
verified meteorological archives. Both observation files feed the neighbour model,
even when Streamlit displays only one turbine.

GET `/api/dashboard?period=date&date=2026-02-02&issue=2026-02-01T06:00:00%2B05:00`
returns the selected day's 24 intervals and preserves issue/model IDs. `today` and
`yesterday` remain supported with actual current dates. Runtime configuration on
port 8000 chooses the archived example; the separate Vite/nginx demo stays offline
unless explicitly configured for API mode. This lightweight server is for a local
or trusted internal deployment, not an authenticated public service.

## Persistent worker / containers

```sh
python scripts/watch_forecast.py --config examples/local_request.json \
  --state-dir runs/local-monitor --interval-seconds 300

docker compose -f frontend/compose.yaml -f frontend/compose.local.yaml \
  --profile local up --build -d
```

Compose adds API and worker services with restart policies, CPU/memory limits,
read-only observation/model mounts and durable cache/run/state volumes. The worker
continues without a browser and resumes its signature/state after restart. The
example deliberately keeps its historical issue fixed. For daily production,
set `mode=live` in a new config, provide fresh observations and run the worker with
`--live`: local issues advance once a day at 06:00 UTC+5. Historical files cannot
be silently reused as fresh production inputs.

The CLI worker was exercised in separate processes: first run completed, restart
reused the persisted state without repeating the forecast. Docker is not installed
in this workspace, so image builds, container health, and container-level restart
recovery have NOT been verified. This is an explicit outstanding deployment check.

## Audit changes / February readiness

Historical/live modes reject any dataset explicitly marked synthetic, regardless
of the selected model. Model training provenance follows the accepted input report.
Regression tests require rejection, rather than accepting contradictory flags.

The alternate GFS decoder now checks initialization, NCEP centre, variable/level,
instantaneous step, grid spacing, finite missing sentinel, plausible values and
nearest-cell geometry. Index/GRIB requests bind ETag and Last-Modified to the
availability probe; ranges and Content-Range are checked. Controlled attack tests
pass. Existing cached weather has not been declared corrupted.

`--strict-submission` on the replay CLI requires zero blocked/failed issues,
complete February coverage and non-synthetic results. A short successful run is
reported as execution-complete but export-partial, never submission-ready.

The real local-model replay attempted all 29 daily issues (Jan 31–Feb 28 at 06:00).
Only one issue could complete: final weights cannot be used before their training
cutoff, and later issues lack fresh SCADA context. There are 96/1344 February
rows and 1248 missing; submission readiness is FALSE. See
`src/wind_forecast/evaluation/archive/integration-replay-audit.json`.
A complete real submission remains outstanding. Future February target labels
are needed to score accuracy; they are not inherently required to generate a
weather-driven forecast. That alternative requires eligible archived weather for
every issue and an appropriate model/cutoff. No fake fill or backdated final model
was used to conceal the gaps. Prior measured-weather validation is not claimed as
24/48-hour forecast accuracy.
