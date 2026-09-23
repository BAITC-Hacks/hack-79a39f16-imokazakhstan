# Codex prompt for Person 1

Read `README.md`, `AGENTS.md`, `docs/architecture.md`, and `docs/data.md`. You are Person 1. Work only in `src/wind_forecast/data/`, `src/wind_forecast/weather/`, and `docs/data.md`. Your task is to map and validate the supplied turbine data and retrieve eligible original archived forecasts for the two locations.

Keep the `contracts.py` interface backward compatible. Do not edit root dependencies, model code, app, or shared fixtures. Commit your work on `codex/person-1-data-weather` in your own clone. Begin by checking timestamps, interval labels, target units and coordinates. Then prove one archive date with both locations, full 48-hour coverage, run initialization and documented public availability. Report archive blockers immediately. Do not silently substitute observed weather, reanalysis, synthetic values, or today's forecast.

Publish a small immutable, hashed input artifact as soon as it is usable; expand January validation and February test coverage afterward. Keep raw inputs read-only. Handoff the commit hash, exact commands, artifact paths, provenance evidence, availability assumptions, coverage and missing rows. Label anything synthetic or unverified.
