# HackAlem Wind Forecasting Starter

Проект создан для участия в хакатоне.

A small Python starter for the HackAlem agentic wind-power forecasting case. It has three independent work areas, a shared data contract, a runnable **synthetic-only** demo, and a first empirical wind-to-power baseline. It does not contain the team's turbine measurements or an approved archive of historical weather forecasts.

## Quick start

Python 3.11 or newer is required. From this folder:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[app,openai]"
python scripts/run_demo.py
streamlit run app.py
```

The demo should create a forecast CSV, a JSON manifest, and a JSONL agent trace under `runs/demo/`. The inputs and output are synthetic and must not be submitted as real evaluation results. The Streamlit app runs the same demo. Neither requires API credentials.

## Assign the three tracks

All participants have equal skills; assign these labels to people after cloning.

| Person | Owns | First milestone |
|---|---|---|
| Person 1 | `src/wind_forecast/data/`, `src/wind_forecast/weather/`, `docs/data.md` | Load the real SCADA file and retrieve one eligible 48-hour archived forecast |
| Person 2 | `src/wind_forecast/models/`, `src/wind_forecast/evaluation/`, `docs/model.md` | Train and save the baseline; report chronological validation metrics |
| Person 3 | `src/wind_forecast/agent/`, `app.py`, `scripts/`, shared contracts, packaging and README | Keep the mock flow running, implement bounded OpenAI tool calling, integrate artifacts |

Each person gets their own clone, branch and virtual environment. Follow [`AGENTS.md`](AGENTS.md) before asking Codex to work. Use the matching prompt in [`codex_prompts/`](codex_prompts/). Branch suggestions: `codex/person-1-data-weather`, `codex/person-2-model`, `codex/person-3-agent`.

The common interface is in [`src/wind_forecast/contracts.py`](src/wind_forecast/contracts.py). Keep changes backward compatible; ask Person 3 to merge shared-interface changes. Pass immutable, versioned CSV/JSON artifacts between participants. Do not share a mutable notebook or model file.

## Critical data rule

For each simulated issue time, the weather run and each observation must have been available by that issue time. A weather model's run initialization time is not the same as when its forecast became public. Do not use later observations, reanalysis, or today's weather forecast as a historical prediction input. The included provider marks all output synthetic. Person 1 must implement and document the real source and its historical availability evidence before labeling a replay real.

The target's normalization and timezone/interval convention have not yet been confirmed. Preserve input units and expose assumptions in configuration. Do not report MW/MWh or farm totals until capacities and aggregation rules are known.

## Runtime API keys

Copy `.env.example` to `.env` for local runtime configuration, then fill only the keys the team actually has. Never commit `.env` or print credential values. The offline demo works without keys. `OPENAI_API_KEY` is for optional application inference; a Codex subscription is for coding and does not replace the runtime API key. Brev infrastructure credentials and NVIDIA model-service credentials are separate. Jev needs its own TypeSafe credential.

## Next implementation steps

1. Person 3 commits this starter and posts the commit hash to the team.
2. Everyone clones the repository and works only in their assigned area.
3. Within 15 minutes, agree the timezone, interval convention, target normalization, farm/turbine target and issue schedule; store uncertain values as configuration.
4. Person 1 checks historical weather feasibility immediately. Person 2 starts with the baseline and fixture data. Person 3 keeps the app running against mocks.
5. Integrate a small verified data/model artifact at a time. Freeze new features 75 minutes before submission.

The source case scores functionality (25), technical implementation (25), README/reproducibility (25), practical value (15), and development potential (10). Prioritize an honest, reproducible replay and a clear demonstration over optional model integrations.
