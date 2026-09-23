# HackAlem wind-power forecasting application

A Python application for forecasting hourly **normalized active power** for the
two case turbines, 24 or 48 hours ahead. It includes a Streamlit interface, a
Python API, a command-line runner, historical replay, versioned outputs, and an
optional OpenAI tool controller. Numerical forecasts come from Python models.

The offline fixture runs without credentials or internet. Real runs load the
organizer's SCADA exports, select eligible forecast weather, fit the supplied
empirical baseline or load your team's model, and preserve input provenance.
The baseline gives the application a usable starting model; a successful run
does not establish its accuracy.

## Run locally

Use Python 3.11 or newer, from this repository directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[app,openai,weather]"
streamlit run app.py
```

On Windows, activate with `.venv\Scripts\activate` instead. The `weather` extra
installs the decoder for the original NOAA archive provider. An offline fixture
needs only `pip install -e ".[app]"`.

In the application, run the fixture first. Select the issue time in UTC, one or
both turbines, and a 24- or 48-hour horizon. A completed two-turbine, 48-hour run
contains **96 hourly predictions** and downloadable artifacts. Fixture outputs
are visibly synthetic and cannot be used as case evaluation forecasts.

The same fixture can run from the terminal:

```bash
python scripts/run_forecast.py --config examples/fixture_request.json
```

## Run with the supplied data

Upload each organizer CSV into its turbine slot in the application, or keep
local copies at:

```text
data/raw/turbine_1.csv
data/raw/turbine_2.csv
```

Set the export's timezone, whether its ten-minute timestamps label interval
starts or ends, and the reporting delay. Acknowledging these assumptions records
your choice; it does **not** establish organizer confirmation. The attachments
themselves do not supply those conventions.

Choose a historical issue time, turbines, and horizon. The real workflow uses
the original NOAA GFS archive by default, with a local cache. Your teammate can
instead supply canonical weather bundles or a configured Python provider. The
first real run downloads forecast fields and can take longer than a fixture.
See [weather archive behavior](docs/weather_archive.md). Person 1's merged
provider also exports app-compatible `weather.json` bundles; follow the
[mapping and export instructions](docs/data.md#integration-after-merging-the-team-branches).

Without a configured teammate model, the application fits the included empirical
wind-to-power baseline using only observations eligible at the issue time. A
teammate's fitted model replaces it through the documented factory interface.
Model training, including fitted transformations, must obey the same cutoff as
inference inputs.

The supplied CSVs actually end on **January 31, 2026**, although their filenames
mention February 28. They contain no February targets for scoring. Read the
[measured dataset profile](docs/dataset_profile.md) before choosing a replay or
claiming accuracy.

For command-line use, copy `examples/historical_request.json` to your own JSON
configuration, set the agreed conventions, and acknowledge the assumptions.
The supplied example starts with acknowledgment disabled. Run it with
`python scripts/run_forecast.py --config my_real_request.json`.

## Input and output

| Input | What it controls |
|---|---|
| UTC issue time | Simulated time when the forecast is made |
| `T1`, `T2`, or both | Per-turbine output selection |
| 24 or 48 hours | Number of future hourly intervals per turbine |
| CSVs and timestamp assumptions | Eligible historical measurements |
| Weather provider or bundle files | Future meteorological forecast inputs |
| Optional model artifact and metadata | Team model replacing the empirical baseline |

A run returns a state (`completed`, `blocked`, or `failed`), forecast rows when
available, a quality/provenance report, a summary, a workflow trace, and a new
artifact directory. Forecasts retain issue time, turbine, valid time, lead, and
normalized-power prediction. Uncertainty columns are populated only if the model
supplies them. Capacity and normalization details remain unconfirmed, so the
application does not convert results to MW/MWh or claim a farm total.

[Application inputs, outputs, examples, and teammate handoff](docs/application_io.md)
is the main operating guide. [Architecture](docs/architecture.md) explains the
module boundaries and eligibility checks.

## Team handoff

| Owner | Deliver to the application |
|---|---|
| Person 1: data/weather | Confirmed timestamp conventions; complete eligible `WeatherBundle` JSON with original-source evidence, or a `fetch(request)` provider |
| Person 2: models | Fitted model file, metadata JSON, and trusted Python factory exposing the shared `predict` interface; separate chronological validation results |
| Person 3: application/integration | Interface, run configuration, eligibility checks, agent controller, exports, replay, and integration of team artifacts |

Each teammate works in their own clone/branch. Keep the records in
`src/wind_forecast/contracts.py` compatible, and exchange immutable artifacts
with hashes and training/availability timestamps. Person 3 integrates their
branches. See [AGENTS.md](AGENTS.md) for repository ownership.

## Optional OpenAI controller

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in the local environment or `.env`, then
select the OpenAI controller. Choose a model available to your API project.
Secrets stay outside run configuration and artifacts. The application uses at
most eight API requests per run with at most 900 output tokens per request;
actual charges depend on model and token usage. The run report records usage.
No paid OpenAI calls are needed for the deterministic workflow.

OpenAI coordinates `fetch → audit → predict → inspect → save` and can explain
returned summaries. Python controls tool arguments, eligibility checks,
numerical predictions, and saved outputs. Missing keys, unsupported responses,
or API errors trigger the Python workflow fallback. API commentary is
explanatory text; `forecast.csv` is the numerical result.

NVIDIA Brev and FourCastNet are optional teammate work. They are not prerequisites
for the application: any chosen weather model must produce the same eligible
weather contract, and any power model must satisfy the predictor contract.

## Before submission

Confirm the source timezone/interval convention, target normalization and
capacity, required issue schedule, submission columns, target aggregation, and
scoring metric with the organizers. Evaluate the team's model chronologically
without future observations or forecast releases. February accuracy requires
separate February labels. The replay export is a provisional format until the
organizer's submission schema is confirmed.

Keep local CSVs, caches, run artifacts, model files, `.env`, and credentials out
of Git. Commit code, configuration examples, and reproducibility documentation.

## Verification performed

The offline two-turbine, 48-hour workflow produced 96 synthetic rows. A real
two-turbine, 24-hour run using the supplied CSVs and the original January 31,
2026 18Z GFS archive also completed, producing 48 baseline predictions for an
issue at February 1 `00:00Z`. That run explicitly acknowledged UTC/start labels
as operator assumptions; their organizer confirmation remains outstanding.

This establishes an end-to-end numerical run and original-weather retrieval.
No February accuracy claim follows because the attachments have no matching
targets. No paid OpenAI API calls were made. The Streamlit interface has not
been manually verified in a browser during this implementation.
