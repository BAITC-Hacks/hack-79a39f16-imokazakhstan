# Codex prompt for Person 2

Read `README.md`, `AGENTS.md`, `docs/architecture.md`, and `docs/model.md`. You are Person 2. Work only in `src/wind_forecast/models/`, `src/wind_forecast/evaluation/`, and `docs/model.md`. Start with the included empirical power-curve baseline, then add a small CatBoost challenger only when real archived weather training inputs are ready.

Keep `Predictor.predict(request, observations, weather)` backward compatible. Do not edit root dependencies, weather, app or shared contracts. Commit on `codex/person-2-model` in your own clone. Use chronological splits, respect every issue-time cutoff, and report MAE/RMSE/sample counts by turbine and lead. Do not claim skill from synthetic fixtures or observed future weather. Do not assume power normalization or capacity. Keep the model artifact and metadata reproducible. Brev GPU work is optional and must not delay the CPU baseline.

Handoff the commit hash, model artifact and hash, train cutoff, features, exact commands, real validation numbers and limitations. Keep each demo number clearly identified as synthetic.
