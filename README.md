# WindScope — wind-power forecasting

Проект создан для участия в хакатоне.

WindScope predicts **hourly normalized active power** for two wind turbines,
24 or 48 hours ahead. The website lets a reviewer run a demo in one click or
forecast from the organizer's measurements and original archived weather.
The autonomous cycle retrieves weather, prepares data, trains the model, predicts,
analyses results and recalculates when the inputs change.

## Run the project

Requires **Python 3.11 or newer**. Run these commands in a terminal:

```bash
git clone https://github.com/BAITC-Hacks/hack-79a39f16-imokazakhstan.git
cd hack-79a39f16-imokazakhstan
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

On Windows PowerShell, replace the activation command with:

```powershell
.venv\Scripts\Activate.ps1
```

Open **http://localhost:8501** (or the address printed by Streamlit).
The installation includes the interface, gradient boosting ML, optional OpenAI
client, and NOAA weather decoder. If you only need the offline demo, install
`python -m pip install -e ".[app]"` instead.

Already cloned? Pull the team's latest changes before installing dependencies.
Run commands from the repository folder so local files and `.env` are found.

## A one-minute demonstration

1. Leave **Demo data** selected.
2. Choose **24 hours** or **48 hours**, and one or both turbines.
3. Click **Run demo forecast**.
4. Read the chart under **Read your forecast**, then download the CSV.

This path works without API keys or internet after installation. All demo
measurements, weather, and predictions are explicitly synthetic.

## Forecast from the organizer's measurements

1. Select **Your measurements**.
2. Upload each turbine's CSV in **Measurement files**, or copy the original
   files to `data/raw/turbine_1.csv` and `data/raw/turbine_2.csv`.
3. Choose **Historical date**, the issue date/time in UTC, horizon, and turbines. For the
   case example use **February 1, 2026 at 00:00 UTC**, **24 hours**, and both turbines.
4. Review timestamp settings in **Optional settings**, then tick **Use these
   timestamp assumptions**. The supplied files do not declare their timezone
   or whether timestamps mark interval starts/ends; confirm these with organizers.
5. Leave **Gradient boosting ML** selected in Optional settings and click
   **Generate forecast**. The chart, hourly values and downloads appear below.

The [NOAA GFS public forecast archive](https://registry.opendata.aws/noaa-gfs-bdp-pds/)
supplies wind and temperature by turbine coordinates, without a weather API key.
The app selects the newest complete weather run whose source timestamps prove
it was available by the issue. It averages complete groups of six ten-minute readings, uses only
measurements available by the issue time, retrieves eligible original NOAA
weather, and runs the configured numerical model. A first 24-hour weather
request transfers about 67 MB; 48 hours is about 135 MB. Internet is required
for the automatic NOAA provider, including metadata checks on cached runs.
A matching **weather.json** upload allows the forecast to run from local inputs.

Raw CSVs, weather caches, model artifacts, and generated runs are ignored by Git.
Teammates and reviewers must supply their own measurement copies for real runs.

## Keep forecasts updated automatically

Select **Latest available (live)** to forecast from the current UTC hour, or keep
**Historical date** to replay a fixed issue. Tick **Keep forecast updated
automatically** and click **Generate forecast**. While the page stays open, the
agent checks every five minutes for updated CSVs, model files and eligible weather.
Live mode also advances the forecast window each hour. Unchanged inputs cause no
new model run or OpenAI charge. Failed updates keep the last successful result
and retry automatically.

For a process that runs independently of the browser:

```bash
# Complete offline cycle; creates 96 hourly turbine predictions.
python scripts/watch_forecast.py --config examples/watch_request.json --once

# Continuous historical monitoring using your configured inputs.
python scripts/watch_forecast.py --config my_real_request.json \
  --state-dir runs/monitor-case --interval-seconds 300

# Current forecasts: config must use mode="live" and real inputs.
python scripts/watch_forecast.py --config my_live_request.json \
  --state-dir runs/monitor-live --live --interval-seconds 300
```

The [agent guide](docs/autonomous_agent.md) explains triggers, saved state, retries
and a demonstration of input changes. Start a live config from
`examples/live_request.json`, review its timestamp settings and acknowledge them;
`--live` replaces the example issue date with the current UTC hour. For new measurements, replace the same
configured project CSV files. Browser uploads are snapshots. The supplied history
can train a live model, but the result analysis flags its age; it cannot establish
the turbines' present operating conditions.

## Read the output

- **Chart:** a distinct color and line style for each turbine, UTC timestamps,
  and a labeled power axis. Each point represents the hour ending at that time.
- **Summary:** each turbine's average and peak predicted normalized power.
- **Hourly values:** the same predictions in a compact table.
- **Jev · Next 3 hours** (optional): operating concerns, supporting evidence and
  an immediate review recommendation for each turbine.
- **Forecast CSV:** numerical predictions with turbine, issue time, valid time,
  lead hours, and optional model quantiles.
- **Full report ZIP:** forecast, input assumptions, weather evidence, workflow
  trace, model identity, and response metadata.

Outputs are also saved in a new directory under `runs/application/` for each run.
Changing controls does not change a saved result: generate again to update it.
The page labels results from an earlier configuration until you do so.

These are **normalized values**. Their normalization definition and turbine
capacity are needed before conversion to MW. The supplied CSVs end on January
31, 2026 and contain no February targets, so February accuracy is not yet known.

## Prediction model and optional AI

Real runs default to a separate **histogram gradient boosting regressor** for
each turbine. Inputs are wind speed, temperature and cyclic calendar features;
the target is hourly normalized active power. It trains only on measurements
available by the issue, reports a chronological holdout against the empirical
baseline, then fits all eligible history. It needs at least 168 valid hours per
turbine. [Model details and measured validation results](docs/model_ml.md).

These diagnostics use measured weather. Full 24–48 hour accuracy still needs
replay with archived forecast weather and held-out production targets. Coarse
weather-grid wind differs from turbine measurements. The dependency-free
**empirical baseline** remains available and powers the synthetic demo.

A teammate model is enabled by setting `WIND_MODEL_FACTORY`, `WIND_MODEL_PATH`,
and `WIND_MODEL_METADATA` in the environment or local `.env`. The factory loads
an artifact with matching metadata and a training cutoff no later than the
forecast issue. See the [model contract and Python examples](docs/application_io.md#person-2-deliver-the-model).

OpenAI is optional. Copy `.env.example` to `.env`, fill `OPENAI_API_KEY` and
`OPENAI_MODEL` (defaults to `gpt-4.1-mini`), then enable **Use OpenAI agent** in Optional settings. The agent
calls weather, preparation, training, audit, prediction, analysis and saving tools;
predictions come
from the numerical model. Each run permits at most eight API calls and 900 output
tokens per call. API failures fall back to Python. Keep `.env` out of Git.
NVIDIA Brev and FourCastNet are optional model-team integrations.
The controller uses the [Responses API function-calling interface](https://developers.openai.com/api/docs/guides/function-calling).

### Jev: immediate operational review

Set `TYPESAFE_API_KEY` in the local `.env`, then select **Your measurements →
Optional settings → Jev: review the next 3 hours**. Add an optional operator note
with its availability/expiry times, and generate the forecast. Read the **Jev ·
Next 3 hours** card below the result analysis.

Jev interprets reports of maintenance, curtailment, icing concerns or faulty
measurements and suggests what to review. The ML model produces the power values.
Jev works without OpenAI and needs no additional Python package. Each enabled
real run uses one TypeSafe request; demo runs stay offline. A service failure
leaves the numerical forecast available. The full ZIP includes `operations.json`
with inputs, decisions, probabilities, model version and usage.

See [Jev setup, I/O and teammate integration](docs/jev_operations.md), including
automatic refresh when a notes file changes. Decision thresholds are provisional;
the displayed probabilities describe evidence judgments, not failure rates.

## Python and batch use

```bash
# Offline forecast
python scripts/run_forecast.py --config examples/fixture_request.json

# Real forecast: first edit paths and timestamp assumptions in your config
python scripts/run_forecast.py --config my_real_request.json

# Rolling replay, including the January 31 issue
python scripts/run_forecast.py --config my_real_request.json \
  --replay-start 2026-01-31T00:00:00Z \
  --replay-end 2026-02-28T00:00:00Z --step-hours 24
```

Start a real config by copying `examples/historical_request.json`. Replay keeps
all issue times and creates a provisional February export with coverage gaps
reported. Confirm the submission format and schedule with organizers. Batch
replay uses the local controller and can download several GB of weather.

## Project guide

| File | Purpose |
|---|---|
| [app.py](app.py) | Website and user controls |
| [Website guide](docs/website_guide.md) | Button-by-button instructions and chart interpretation |
| [Application I/O](docs/application_io.md) | Python inputs/outputs and teammate contracts |
| [Architecture](docs/architecture.md) | Workflow, modules, and time checks |
| [Autonomous agent](docs/autonomous_agent.md) | Recalculation triggers, background execution, recovery |
| [Weather sources](docs/weather_sources.md) | Public endpoints, cycle discovery and availability evidence |
| [ML model](docs/model_ml.md) | Features, chronological validation and limitations |
| [Dataset profile](docs/dataset_profile.md) | Measured CSV coverage and missing data |
| [Weather integration](docs/data.md#integration-after-merging-the-team-branches) | Person 1's app-compatible weather export |
| [Model adapter example](examples/team_model.py) | Person 2's model factory interface |

Person 1 supplies data/weather evidence; Person 2 supplies the fitted model and
validation; Person 3 owns application integration. Follow [AGENTS.md](AGENTS.md)
when contributing. The original brief is in `docs/HackAlem AI_ Agentic AI Case.pdf`.

## Checks

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s src/wind_forecast/data/tests -v
python -m unittest discover -s src/wind_forecast/weather/tests -v
```

Tests cover forecast completion, UI input guards, ML training cutoffs,
chronological validation, NOAA availability/version checks, controller fallback,
automatic change detection, restart recovery and failed-update preservation.
Controller checks use a fake API client; paid OpenAI orchestration has not been
exercised against the live API. Test success establishes execution behavior;
forecasting accuracy still requires original weather and held-out targets.
