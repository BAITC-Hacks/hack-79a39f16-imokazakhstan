# Autonomous weather-to-power forecasting

The agent runs the complete cycle: obtain eligible weather for the turbine
coordinates, prepare observations, load or fit the numerical model, predict
each of the next 24 or 48 hours, inspect the result, and save a new forecast.
The monitor adds automatic repetition when the inputs change.

The workflow uses guarded Python tools. When the OpenAI controller is enabled,
it coordinates those tools and writes commentary; the numerical model still
produces every power prediction. A local controller runs the same complete
cycle without API charges. The monitor works with either controller.

## Start the monitor

From the repository root, with the Python environment activated:

```bash
# One complete offline demonstration; no API key or internet needed.
python scripts/watch_forecast.py --config examples/watch_request.json --once

# Watch continuously; stop with Ctrl+C.
python scripts/watch_forecast.py --config examples/watch_request.json

# A bounded demonstration: initial forecast, then a check 30 seconds later.
python scripts/watch_forecast.py --config examples/watch_request.json \
  --state-dir runs/monitor-demo --interval-seconds 30 --max-cycles 2
```

The example explicitly uses synthetic fixture inputs. A second unchanged poll
reports `unchanged`; it does not fabricate new weather or rerun the model.
To demonstrate recalculation, change the example horizon from 48 to 24 while
the process is running. The next poll reports `configuration_changed`, creates
a new forecast version, and records the trigger. Configuration JSON is reloaded
on every CLI cycle. Reusing a state directory resumes its existing monitor.

For real historical forecasting, copy `examples/historical_request.json`, set
the measurement paths, acknowledge the timestamp assumptions after checking
them, and choose the model/controller settings. Then run:

```bash
python scripts/watch_forecast.py --config my_historical_request.json \
  --state-dir runs/monitor-historical --interval-seconds 300
```

Historical issue time stays fixed. The monitor only considers weather that was
available by that issue; a newly published current forecast cannot replace it.

For forecasts issued now, use a config with `mode: "live"`,
`weather_source: "noaa_gfs"`, real measurement paths and confirmed timestamp
assumptions. Copy `examples/live_request.json` as a starting point; change
`assumptions_confirmed` to true only after reviewing its timezone/interval choices.
The example requests OpenAI; when no local key exists it records a local fallback.
Run:

```bash
python scripts/watch_forecast.py --config my_live_request.json \
  --state-dir runs/monitor-live --live --interval-seconds 300
```

`--live` rolls the issue time to the current UTC hour and uses observations
available by that issue. Every new hour therefore creates a new forecast window.
The configured issue timestamp is required by the common request format but is
replaced by the current hour in this mode. Historical measurement files can
train a model for live predictions; their age and the lack of recent targets
still limit how confidently those predictions can be assessed.

The poll interval must be between 30 seconds and 24 hours; five minutes is the
default. A five-minute check is a reasonable demonstration setting even though
NOAA GFS normally updates in six-hour cycles. NOAA checks use small object
metadata listings, and only changed eligible inputs lead to full retrieval and
forecasting. The controller makes no OpenAI calls on unchanged polls.

## What causes recalculation

| Change | Recorded reason |
|---|---|
| First successful forecast has not yet been produced | `initial_forecast` |
| Contents of a measurement CSV changed | `measurements_changed` |
| Model artifact or its metadata changed | `model_changed` |
| A different complete, eligible weather version is selected | `weather_updated` |
| Forecast settings changed | `configuration_changed` |
| Issue timestamp changed, including a new live UTC hour | `issue_time_changed` |
| Inputs changed while a completed forecast was running | `inputs_changed_during_run` |
| Final input check could not confirm a stable snapshot | `inputs_recheck_failed` |
| A completed run needs a repeat after an unsettled input check | `retry_after_input_change` |

Inputs are compared using SHA-256 content hashes. The NOAA probe and retrieval
use the same cycle eligibility rules. Bundle files use the application's
strict local bundle selector. Retrieval timestamps alone do not trigger a run.
For a custom Python weather provider, implement `probe(request)` with a stable,
JSON-serializable identity (or an object exposing `to_dict()`); the identity
must change when the eligible forecast changes and exclude check timestamps.

## Outputs and recovery

Each actual forecast attempt uses the regular application output directory:

```text
runs/application/<forecast-version>/
  forecast.csv        # Successful hourly predictions
  response.json       # Completion state and full response
  request.json        # Effective issue time and settings
  report.json         # Input, model, weather and result analysis
  trace.jsonl         # Ordered workflow tool actions
```

The monitor directory contains `state.json` and append-only `history.jsonl`.
State stores the complete latest successful response, its run directory,
successful input signature, last attempted update, and next scheduled check.
History records every due check, trigger reasons, and the response path for
every forecast attempt. Failed updates retain the last successful forecast
and do not consume the changed input signature, so the next due poll retries.
Failed source probes also preserve the previous result and retry automatically.
After a successful forecast, the monitor checks the input identities again. If
they moved during execution or the final check failed, it retains the completed
response but leaves the signature unconsumed, so the next poll retries.

A file lock prevents concurrent workers using the same state directory from
running overlapping forecasts. The OS releases it when a process exits.
State updates are written to a temporary file and atomically replaced. These
guarantees apply on a local filesystem; run one service instance when using
network filesystems whose locking behavior is uncertain. Restarting the service
continues from the previous state. Keep the process running for background
updates; a closed terminal stops the CLI unless your deployment supervises it.

## Python and webpage integration

```python
from wind_forecast.agent.monitor import ForecastMonitor
from wind_forecast.agent.settings import RunConfig

monitor = ForecastMonitor("runs/monitor-python", poll_interval_seconds=300)
config = RunConfig.from_dict(request_json)
outcome = monitor.poll(config)

print(outcome.state, outcome.reasons)
if outcome.run is not None:
    print(outcome.run.run_dir)       # New forecast attempt
if outcome.last_success is not None:
    print(outcome.last_success["result"]["rows"])
```

`poll` performs one check and never sleeps. A webpage can invoke it from a
periodic Streamlit fragment; the persisted schedule prevents frequent UI
rerenders from repeating network checks. `waiting` means the next check is not
due, `busy` means another worker owns the lock, and `unchanged` means a completed
check found no input change. `completed`, `blocked`, and `failed` describe new
forecast attempts or failures. Display the last successful forecast together
with a failed-update notice if an update cannot complete.

For real automatic measurement updates, point the config at durable server
files. A one-time browser upload does not provide a continuing observation
feed. Upload or copy newer measurements to the same configured paths, and
publish model artifacts and their matching metadata together. The monitor
checks their hashes; the application still validates model metadata, training
cutoffs, weather provenance and forecast coverage before accepting a result.

API keys belong only in environment variables or the ignored local `.env`.
Monitor config and history never need credentials.

## Verification from this implementation

The 77 tests under `tests/` passed, including the seven-tool controller with a
fake API client, ML cutoffs, source version checks, automatic retries and UI
regressions. A Streamlit check using the real CSVs and a saved original NOAA
bundle completed ML prediction and automatic-update start/stop with 48 rows.

Two complete runs used the supplied real CSVs with explicit UTC/start/zero-delay
assumptions and actual NOAA retrieval:

| Issue (UTC) | Horizon | NOAA initialization | Last required object available | Forecast rows |
|---|---:|---|---|---:|
| 2026-02-01 00:00 | 24 hours | 2026-01-31 18:00 | 2026-01-31 21:54:38 | 48 |
| 2026-09-23 11:00 | 48 hours | 2026-09-23 06:00 | 2026-09-23 10:00:01 | 96 |

Both used `histogram-gradient-boosting-power-v1` and recorded all seven tool
steps. The live run correctly marked stale measurement history for review;
the supplied CSVs stop in January. These checks prove execution and source
eligibility for these windows, not accuracy or current turbine availability.
Live paid OpenAI calls were not exercised; configure a valid replacement key
locally to use that controller. Backend Docker dependencies and writable paths
were updated, but Docker image build/start could not be checked because Docker
is unavailable in this environment.
