# Application input, output, and team handoff

For the simplified webpage and its controls, start with the [website guide](website_guide.md).
This document covers the full Python/CLI interface, including advanced options.

The application requests hourly normalized-power forecasts for `T1`, `T2`, or
both. Supply an issue time, a 24- or 48-hour horizon, observation data, and
forecast weather. The service filters what could have been known at that issue
time, runs a Python predictor, and returns validated rows with an inspectable
report. OpenAI can coordinate those steps and explain their summaries.
`ForecastMonitor` watches for changes and repeats the complete cycle
automatically; it supports both a fixed historical issue and a live UTC hour.

## Agent workflow

The application exposes seven ordered tools to both the local controller and
the optional OpenAI agent:

| Tool | Work performed |
|---|---|
| `fetch_weather` | Retrieve original eligible weather for the requested coordinates and hours |
| `prepare_data` | Validate measurements, aggregate complete hours and apply availability cutoffs |
| `train_model` | Fit the configured numerical model or verify/load a teammate artifact |
| `audit_inputs` | Check weather provenance, time eligibility and complete input coverage |
| `predict_power` | Produce one numerical prediction per turbine and future hour |
| `inspect_forecast` | Check coverage/values and analyse peaks, ramps, training ranges and observation age |
| `save_forecast` | Persist a new version with its forecast, inputs, analysis and trace |

Python guards the order and eligibility rules. An LLM cannot skip validation or
replace forecast values with generated text. The automatic monitor detects a
changed input, invokes this cycle, and retains the last successful forecast if
an update fails. See [autonomous_agent.md](autonomous_agent.md).

With `jev_enabled=True`, `inspect_forecast` also performs an independent Jev
operational review for the next three hours. It returns advisory actions under
`run.report["operations"]` and saves `operations.json`. The power-model contract
and forecast CSV stay the same. See [Jev I/O and integration](jev_operations.md).

| Additional configuration | Default | Purpose |
|---|---|---|
| `jev_enabled` | `false` | Enable one TypeSafe review per real forecast run |
| `jev_model` | `jev-1.13.0` | Requested TypeSafe model; returned version is recorded |
| `operational_notes` | `[]` | Inline notes with `text`, `turbine_ids`, `available_at`, `valid_until` |
| `operational_notes_path` | empty | JSON notes feed watched for changes; use instead of inline notes |

The project includes a shared Jev key in `typesafe.env`; setting `TYPESAFE_API_KEY`
on the host overrides it. The website also reads
`TYPESAFE_MODEL`; Python/CLI callers set `jev_model` explicitly to override its
default. The offline fixture always skips Jev.

## Start with a working fixture

From the repository root after installing the package:

```bash
streamlit run app.py
```

Choose **Demo data** and click **Run demo forecast**. No data upload, API key,
GPU, or network request is needed. Fixtures and their output are synthetic. Use this mode to
demonstrate the interaction and inspect saved artifacts.

Equivalent command:

```bash
python scripts/run_forecast.py --config examples/fixture_request.json
```

Equivalent Python call:

```python
from wind_forecast.agent.application import run_forecast

run = run_forecast({
    "issue_time": "2026-02-01T00:00:00Z",
    "turbine_ids": ["T1", "T2"],
    "horizon_hours": 48,
    "mode": "fixture",
    "weather_source": "mock",
    "controller": "deterministic",
    "output_root": "runs/application",
})

print(run.state)
print(run.summary)
print(run.run_dir)
if run.result is not None:
    print(len(run.result.rows))  # 96 for a completed two-turbine, 48-hour run
    for row in run.result.rows[:3]:
        print(row.turbine_id, row.valid_time, row.prediction)

response = run.to_dict()  # JSON-safe response for another application.
```

## Input for a real historical run

Copy the attachments into local paths or upload them into their separate turbine
slots. Keep source files unchanged. This example uses **UTC and interval starts
as explicit example assumptions**; replace them with the organizer-confirmed
export convention before relying on the results.

```python
from wind_forecast.agent.application import run_forecast

config = {
    "issue_time": "2026-02-01T00:00:00Z",
    "turbine_ids": ["T1", "T2"],
    "horizon_hours": 48,
    "mode": "historical",
    "data_paths": {
        "T1": "data/raw/turbine_1.csv",
        "T2": "data/raw/turbine_2.csv",
    },
    "input_format": "organizer",
    "source_timezone": "UTC",
    "interval_label": "start",
    "reporting_delay_minutes": 0,
    "assumptions_confirmed": True,
    "observation_policy": "frozen_jan31",
    "weather_source": "noaa_gfs",
    "wind_height_m": 100,
    "model_kind": "gradient_boosting",
    "controller": "deterministic",
    "output_root": "runs/application",
    "cache_dir": "data/cache/weather",
}
run = run_forecast(config)
print(run.state, run.summary)
```

`assumptions_confirmed` records the operator's acknowledgment of chosen
conventions. Its historical name does not imply organizer confirmation. The
report retains configuration and warnings.

Without teammate model configuration, real runs default to histogram gradient
boosting, fitted separately for each turbine on eligible SCADA. Its features
are wind speed, temperature and cyclic UTC hour/season. At least 168 valid hourly
wind/power pairs are needed per turbine. The synthetic fixture defaults to the
empirical wind-bin baseline. Set `model_kind="empirical"` to select that baseline
explicitly for a real run.

The ML report includes chronological holdout errors and a baseline comparison.
That diagnostic uses held-out **measured weather**; it does not establish
24–48-hour forecasting accuracy with uncertain weather inputs. February targets
are absent from the supplied files. See [model_ml.md](model_ml.md) for features,
training policy and the validation limits.

The NOAA path requires the `weather` optional dependencies and network access.
It inspects the latest four GFS cycles, newest first, and chooses a complete cycle
whose required files and indices were available by the issue. It then downloads
the original fields and caches them locally. Eligibility can fail if fields are
missing, inaccessible, inconsistent, or modified after the issue. This discovery
works for both historical and current forecasts without a fixed publication-delay
assumption. See [weather_sources.md](weather_sources.md) for public source links
and discovery, and [weather_archive.md](weather_archive.md) for integrity checks.

## Configuration fields

`RunConfig` is in `wind_forecast.agent.settings`. `run_forecast` accepts that
dataclass or a dictionary with the same fields. A JSON configuration can also
be passed to the CLI through `--config`.

| Field | Input and purpose |
|---|---|
| `issue_time` | ISO timestamp with an explicit timezone, aligned to an hour; normalized to UTC |
| `turbine_ids` | Nonempty unique list/tuple, normally `T1`, `T2`, or both |
| `horizon_hours` | `24` or `48` |
| `mode` | `fixture`, `historical`, or `live` |
| `data_paths` | Turbine ID → local observation CSV path |
| `input_format` | `organizer` for supplied Russian headers, or `canonical` |
| `source_timezone` | Explicit IANA timezone for naive timestamps; confirm the actual convention |
| `interval_label` | `start` or `end` of the source ten-minute interval |
| `reporting_delay_minutes` | Nonnegative minutes added after an hour completes |
| `assumptions_confirmed` | Boolean acknowledgment of source-time/target assumptions for a real run |
| `observation_policy` | `frozen_jan31` or `available` |
| `weather_source` | `mock`, `noaa_gfs`, `bundles`, or trusted `python` |
| `weather_path` | File/directory of full bundle JSON when using `bundles` |
| `weather_factory` | Trusted `module:function`, called with `config=config`, for `python` weather |
| `wind_height_m` | `100` or `10` for GFS; default `100` |
| `coordinates` | Turbine → `(latitude, longitude)`; case coordinates supplied by default |
| `model_kind` | `auto` (default), `gradient_boosting`, or `empirical`; `auto` uses ML for real runs and the baseline for fixtures |
| `model_factory` | Trusted `module:function` loading the team's predictor |
| `model_path` | Single fitted model artifact file |
| `model_metadata_path` | JSON metadata binding training provenance and SHA-256 to that file |
| `controller` | `deterministic` or `openai` |
| `output_root` | Parent directory for new versioned run directories |
| `cache_dir` | Local NOAA cache directory |

Case defaults are `T1=(43.645150, 78.535604)` and
`T2=(43.643198, 78.538828)`. They are close together; the coarse GFS grid may give
both the same nearest weather point. This is not independent turbine-resolution
weather. An injected predictor takes priority, followed by a configured
`model_factory`; `model_kind` selects the built-in model when neither is supplied.

`fixture` requires `mock` weather. Real modes reject `mock`. `live` uses actual
forecast-time inputs with the same eligibility checks; the January-only file
does not supply a live operational feed. Choose `available` only when additional
observations genuinely existed by each simulated issue time.
An individual `run_forecast` call uses its supplied issue time in every mode.
`ForecastMonitor(..., rolling_live=True)` and the website's live monitor advance
it to the current UTC hour and use `observation_policy="available"`.

## Observation input

The organizer format is UTF-8 CSV with these exact columns:

```text
ID
Статистическое время
Средняя скорость ветра(m/s)
Нормализованная активная мощность
Средняя температура окружающей среды(°C)
```

`load_scada` accepts a file path or uploaded bytes. The explicit turbine ID is
required because the ID column contains source row numbers.

```python
from wind_forecast.agent.input_data import load_scada

dataset = load_scada(
    uploaded_csv_bytes,
    "T1",
    source_timezone="UTC",  # Use the agreed source convention.
    interval_label="start",
    reporting_delay_minutes=0,
)
# Request T1 only here, or also supply the T2 dataset.
config["turbine_ids"] = ["T1"]
run = run_forecast(config, datasets={"T1": dataset})
```

The adapter validates values and timestamps, resolves interval ends in UTC, and
averages six complete ten-minute intervals into one hourly observation. Missing
samples cause incomplete hours to be omitted and reported. Duplicate timestamps,
nonfinite numbers, negative wind, off-grid times, and ambiguous/nonexistent local
times are rejected. [dataset_profile.md](dataset_profile.md) records measured
coverage and source hashes.

Person 1 can instead supply a canonical hourly CSV:

```text
turbine_id,observed_at,available_at,power_norm,wind_ms,temp_c,quality_flag
```

Canonical timestamps include `Z` or an offset. `observed_at` is the end of a
completed hour; `available_at` records actual availability. Numeric units remain
normalized power, m/s, and °C. `quality_flag` defaults to `ok`; empty optional
numeric fields become `None`. Canonical data is already hourly and bypasses
ten-minute aggregation.

For an issue at `00:00Z`, lead 1 predicts the interval ending `01:00Z`. With source
interval-start labels, January 31's samples from `23:00` through `23:50` form an
hour ending February 1 at `00:00` in the source zone. The frozen-January policy
includes that completed January hour when available; it does not include actual
February intervals.

## Returned response

The Python API also supports trusted dependency injection:

```python
run_forecast(
    config,                # RunConfig or dict
    datasets=None,         # optional dict[str, ObservationDataset]
    predictor=None,        # optional trusted injected predictor
    weather_provider=None, # optional object with fetch(request)
    model_metadata=None,   # provenance required for injected team model
)
```

`ApplicationRun` exposes:

| Attribute | Meaning |
|---|---|
| `state` | `completed`, `blocked`, or `failed` |
| `request` | Validated `ForecastRequest` with generated request ID |
| `result` | `ForecastResult` when generated, otherwise `None` |
| `weather` | Selected bundle when available, otherwise `None` |
| `trace` | Workflow events and controller/fallback evidence |
| `run_dir` | `Path` to the versioned artifact directory |
| `report` | Structured quality, provenance, model, and run information |
| `summary` | Human-readable outcome and limitations |
| `error` | Block/failure reason when applicable |
| `to_dict()` | JSON-safe response for another client/service |

The serialized response contains `state`, `request`, `result`, `run_dir`,
`report`, `summary`, and `error`. Full weather and trace are saved separately.
`report` includes `assumptions`, `training_limit`, `controller`, `datasets`,
`observations`, `model`, `model_validation` (for built-in ML), `forecast`,
`evaluation`, `lineage`, and `weather` when
the corresponding stages finish. Blocked runs may contain only earlier fields.

| State | Interpretation |
|---|---|
| `completed` | Validated forecast rows exist; review warnings and provenance |
| `blocked` | Missing/ineligible inputs prevent a valid run; resolve the reported dependency |
| `failed` | Execution failed; inspect saved diagnostics |

Completion is not an accuracy score. A result can carry
`ForecastResult.status="degraded"`, for example for synthetic fixtures or
baseline limitations. Constructing an invalid `RunConfig` directly raises a
validation error before a run starts.

For a historical issue inside the observed data period, `report["evaluation"]`
matches future target labels **after** the prediction and reports matched/missing
row counts plus per-turbine MAE/RMSE. Those labels are excluded from model inputs.
With the supplied February request there are no matching targets, so it reports
`no_ground_truth`. These diagnostic metrics are not an organizer-confirmed score.

This future-target comparison is separate from `report["model_validation"]`,
which checks the built-in ML model on a chronological slice of eligible training
history using measured wind and temperature. `report["forecast"]` records
per-turbine mean/peak, the largest hourly change, observation age, and counts of
forecast wind or predictions outside the measured training ranges. Findings
appear in the webpage and can mark a forecast as degraded.

`ForecastResult` contains forecast/request IDs, schema version, model ID,
weather bundle ID, input hash, creation time, status, synthetic flag, warnings,
and rows. Each output row has:

| Column | Meaning |
|---|---|
| `turbine_id` | `T1` or `T2` |
| `issue_time` | UTC issue time, retained across replay overlaps |
| `valid_time` | UTC end of predicted hourly interval |
| `lead_hours` | Integer from 1 through configured horizon |
| `prediction` | Predicted normalized active power |
| `p10`, `p50`, `p90` | Optional model quantiles, blank when unavailable |

There must be exactly `len(turbine_ids) × horizon_hours` unique rows. The
application checks coverage, identifiers, times, and finite predictions. It does
not invent uncertainty. Normalization details and capacities are unknown: do
not label normalized predictions MW, sum them into farm MW, or call their sum
MWh.

## Saved artifacts

Successful runs append a new directory under `output_root`:

| File | Purpose |
|---|---|
| `forecast.csv` | Authoritative per-issue numerical forecast |
| `manifest.json` | Identity, model/weather provenance, warnings, and hashes |
| `trace.jsonl` | Workflow and controller events |
| `request.json` | Run request and configuration |
| `report.json` | Quality, assumptions, and outcome details |
| `response.json` | Serialized application response |
| `weather.json` | Full weather bundle with rows for inspection/replay |
| `weather_source_manifest.json` | Copied NOAA source evidence when that provider is used |
| `summary.md` | Human-readable outcome and limitations |

Blocked/failed runs retain diagnostics. An absent forecast is not zero
generation. Keep the artifact directory with the result you present. Original
NOAA fields and detailed source evidence live in the configured cache; preserve
it for reproducibility.

If you rerun the same issue/turbines/horizon after changing inputs, prior runs
remain intact. `report["lineage"]` identifies the previous version, whether the
input hash changed, the number of changed predictions, and the largest change.

## Automatic recalculation

Use `ForecastMonitor` to repeat the complete agent workflow when input files,
model files/metadata, settings, eligible weather, or the live issue hour change:

```python
from wind_forecast.agent.monitor import ForecastMonitor

monitor = ForecastMonitor("runs/monitor-team", poll_interval_seconds=300)
outcome = monitor.poll(config)  # One check; no sleep inside poll().
print(outcome.state, outcome.reasons)
if outcome.run is not None:
    print(outcome.run.run_dir)  # New ApplicationRun, including failed attempts.
if outcome.last_success is not None:
    print(outcome.last_success["result"]["rows"])  # Full saved response as a dict.
```

Schedule another `poll` call after `outcome.next_check_at`, or use the continuous
CLI, which also reloads configuration JSON on each cycle:

```bash
python scripts/watch_forecast.py --config my_real_request.json \
  --state-dir runs/monitor-team --interval-seconds 300

# One offline check, or a bounded two-cycle demonstration.
python scripts/watch_forecast.py --config examples/watch_request.json --once
python scripts/watch_forecast.py --config examples/watch_request.json \
  --state-dir runs/monitor-demo --interval-seconds 30 --max-cycles 2
```

For rolling current forecasts, set `mode="live"` in the config and add `--live`
to the command, or use `ForecastMonitor(..., rolling_live=True)`. It advances
issue time to the current UTC hour and admits only observations available then.
Historical mode keeps the configured issue fixed. A one-time upload supplies a
snapshot; continuing measurement updates require durable configured files.

Unchanged inputs do not retrain or call OpenAI. `waiting` and `busy` indicate a
check is not due or another worker has the lock; `unchanged` means a due check
found no change. Failed updates preserve the last successful forecast and retry.
`state.json` stores the last successful full response and input signature;
`history.jsonl` records checks, triggers and run references. Changed inputs during
execution are rechecked and retried. See [autonomous_agent.md](autonomous_agent.md)
for persistence, polling bounds and service operation.

## Person 1: deliver weather and data

Confirm source timezone, interval labeling, and reporting lag. Return canonical
hourly observations or let the application adapt original exports. Do not fill
missing measurements with fabricated values.

Weather JSON must match `WeatherBundle`, directly or under a top-level `weather`
key. Full rows are required; metadata-only manifests are insufficient.

| Field | Contract |
|---|---|
| `bundle_id`, `provider`, `weather_model` | Nonempty identifying strings |
| `run_init_time` | Aware ISO model initialization time |
| `available_at` | Aware ISO time when every included field was obtainable |
| `retrieved_at` | Aware ISO archive retrieval time |
| `availability_basis` | Nonempty evidence/explanation of availability |
| `source_uri` | Source location without credentials |
| `source_hash` | Nonempty hash identifying original input evidence |
| `provenance_status` | `verified_original`, `unverified`, or `synthetic` |
| `is_synthetic` | JSON boolean consistent with provenance |
| `rows` | Nonempty list of `WeatherPoint` objects |

Each point contains `turbine_id`, aware `valid_time`, finite nonnegative
`wind_ms`, and `temp_c` (finite or `null`). Optional `wind_direction_deg` is within
0–360, and `wind_height_m` is positive. Every requested turbine/hour appears once.
Larger bundles can include more hours/turbines; the application slices them
after validating the entire bundle.

Use a single file or a directory containing only canonical bundle JSON files:

```python
config["weather_source"] = "bundles"
config["weather_path"] = "data/weather/bundles"
```

The loader chooses the complete eligible bundle with latest `available_at`, then
latest `run_init_time`. Historical/live accept only non-synthetic
`verified_original` bundles. Retrieval may happen later; initialization and
availability must precede the issue. The provenance marker is the provider's
assertion, so include auditable original-source evidence for team review.

Alternatively implement the shared provider interface:

```python
class TeamWeatherProvider:
    def fetch(self, request):
        # Return a complete WeatherBundle for the fixed ForecastRequest.
        ...

    def probe(self, request):
        # Required only for automatic monitoring of a custom provider.
        # Return stable JSON identity of the eligible forecast; exclude check time.
        ...

def create_provider(*, config):
    return TeamWeatherProvider()
```

Use `weather_source="python"` and
`weather_factory="team_weather.provider:create_provider"` in trusted server/CLI
configuration, or inject the provider directly in Python. FourCastNet or another
weather model is optional work behind this contract; historical availability
requirements still apply.

## Person 2: deliver the model

Supply a fitted artifact file, metadata JSON, and importable factory. Keep
framework-specific loading in your module. The application does not assume
an artifact framework or pickle format for teammate models. The built-in model
uses scikit-learn; its implementation and diagnostics are in
[model_ml.md](model_ml.md).

| Metadata field | Contract |
|---|---|
| `model_id` | Versioned ID matching the loaded predictor |
| `trained_through` | Aware ISO training cutoff, no later than issue/policy limit |
| `features` | Nonempty list of unique feature names |
| `target_units` | Exactly `normalized_active_power` |
| `trained_on_synthetic` | JSON boolean, `false` for historical/live |
| `training_data_hash` | Nonempty hash identifying the training dataset |
| `artifact_sha256` | 64-character SHA-256 of the exact artifact file |

Compute the artifact hash after saving the model:

```python
from hashlib import sha256
from pathlib import Path

artifact_sha256 = sha256(Path("models/team_model.bin").read_bytes()).hexdigest()
```

Factory and predictor signatures:

```python
def load_predictor(*, model_path, metadata):
    # model_path is pathlib.Path. Load your trusted native artifact.
    # Return an object exposing model_id and predict below.
    ...

class TeamPredictor:
    model_id = "the-same-versioned-id-as-metadata"

    def predict(self, request, observations, weather):
        # request: ForecastRequest
        # observations: list[Observation], restricted to eligible inputs
        # weather: WeatherBundle, audited for the request
        # Return ForecastResult with one ForecastRow per turbine/hour.
        ...
```

Use shared dataclasses from `wind_forecast.contracts`; preserve request ID,
weather bundle ID, and issue time. All numerical predictions and any uncertainty
come from your model. Do not use withheld targets or observed future wind or
temperature inside `predict`.

An executable adapter example is provided in
[`examples/team_model.py`](../examples/team_model.py). It loads a genuinely
fitted JSON wind-bin artifact supplied by the model team; no invented learned
weights are bundled. Replace its loader/class with your framework while keeping
the factory and predictor signatures.

Configure the artifact:

```python
config.update({
    "model_factory": "team_model.adapter:load_predictor",
    "model_path": "models/team_model.bin",
    "model_metadata_path": "models/team_model.metadata.json",
})
```

The loader checks metadata and SHA-256 before calling the factory. Factories are
trusted server/CLI configuration, not browser uploads or LLM-provided code. For
the Streamlit host, set `WIND_MODEL_FACTORY`, `WIND_MODEL_PATH`, and
`WIND_MODEL_METADATA` in its environment. Install the team's module and framework
dependencies in the same environment.

Share chronological validation separately, with period boundaries, metric
definition, observation policy, weather source, and baseline comparison.
Metadata and a loaded artifact alone cannot prove training history or accuracy;
the model team must retain that evidence. Scalers, calibration, and feature
selection belong to training and must obey the declared cutoff too.

## February replay

Save a real configuration as `my_real_request.json`, then run repeated issues:

```bash
python scripts/run_forecast.py --config my_real_request.json \
  --replay-start 2026-01-31T00:00:00Z \
  --replay-end 2026-02-28T00:00:00Z \
  --step-hours 24
```

Each issue gets its own run. Replay retains all per-issue forecasts in a combined
CSV and creates a latest-per-hour February export. Overlapping 48-hour forecasts
are distinct predictions and keep their issue times. The compact export selects
the latest eligible issue for each turbine/hour. It is a **provisional submission
schema** until the organizers confirm exact columns and issue schedule.

The replay bounds are inclusive, with at most 64 issue times per invocation.
The example includes the January 31 issue requested by the case. A single saved
model used across the whole replay must have been trained by the earliest issue;
a model trained through the end of January is ineligible for January 31 at
00:00. Supply earlier model versions or let a built-in model fit separately on
eligible history for each issue.
Batch replay forces the deterministic controller, so it makes no OpenAI calls.
It saves `rolling.csv` (all issues), `february.csv` (February intervals retaining
overlaps), `submission.csv` (latest issue per turbine/hour), `request.json`, and
`summary.json`, alongside each issue's artifacts. The summary includes exact
missing turbine/hour keys, per-issue states, provenance, and evaluation status.
Replay state can also be `partial` when only some issues complete. Successful
issues do not imply complete monthly coverage; inspect `coverage.complete`.

Check interval coverage: an issue at February 1 `00:00Z` first predicts the hour
ending `01:00Z`, and source/scoring timezone conventions affect which interval
ends belong to the month. The attachments contain no February targets, so
generating February forecasts does not produce an accuracy metric. Evaluate
against organizer labels when available or use a separate chronological holdout
with training cut off before it. Do not backfill earlier issues with a model
trained later or substitute realized weather for original forecast inputs.

## OpenAI and remaining decisions

Set `OPENAI_API_KEY` in the host's local environment or ignored `.env`, and select `controller="openai"`
in Python (or **Use OpenAI agent** under Optional settings on the website)
for paid API orchestration. `OPENAI_MODEL` optionally overrides the application
default `gpt-4.1-mini`. Never place a key in request JSON, source files, reports
or Git. The Python configuration defaults to the local deterministic controller;
the website enables the OpenAI option when credentials are configured. OpenAI can make at
most eight API requests per run, capped at 900 output tokens per request, with
SDK retries disabled. Only bounded summaries cross the API boundary. Local tools
compute forecasts and enforce eligibility. API errors or invalid tool sequences
fall back to Python. Token usage is recorded; model choice determines charges.
No paid calls are required for the fixture.

Brev credit can support teammate GPU training/inference. The application,
gradient boosting model and baseline run locally without a GPU and do not automatically deploy or purchase
GPU resources.

Before a scored submission, resolve source timezone and interval convention,
normalization and capacity, issue schedule, required output columns, whether the
target is per turbine or aggregated, scoring metric, and February label access.
The application records assumptions and rejects missing dependencies; it cannot
derive organizer decisions from the CSV filenames.
