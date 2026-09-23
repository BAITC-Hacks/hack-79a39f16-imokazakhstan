# Instructions for Codex in this repository

Read `README.md`, `docs/architecture.md`, and the shared schemas in `src/wind_forecast/contracts.py` before editing.

This repository is being developed by three people in separate clones. Identify your assigned person in the task prompt and stay within that person's ownership below. Do not rewrite another person's module, root dependency list, shared contract, or fixtures unless the prompt explicitly assigns integration ownership. Propose shared changes to Person 3 and keep adapters backward compatible. Commit on your assigned branch. Do not add real-looking outputs from synthetic data.

- Person 1: `src/wind_forecast/data/`, `src/wind_forecast/weather/`, `docs/data.md`.
- Person 2: `src/wind_forecast/models/`, `src/wind_forecast/evaluation/`, `docs/model.md`.
- Person 3: `src/wind_forecast/agent/`, `app.py`, `scripts/`, shared contracts, root packaging, README and integration.

The mock path must always run without network access or API keys. Historical mode rejects synthetic or unverified weather and inputs available after the simulated issue time. Keep API credentials in local environment variables; never print, commit, or store them in traces.
