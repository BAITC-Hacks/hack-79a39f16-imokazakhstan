# Forecast model and evaluation

## Person 2 owns

- Keeping the empirical per-turbine wind-to-power baseline runnable and serializable.
- Adding a CatBoost challenger only when archived weather covariates are available for both training and replay.
- Using chronological development/validation splits and freezing choices before the February test.
- Reporting sample counts and MAE/RMSE by turbine and lead bucket, with the baseline comparison.

The included `EmpiricalPowerCurve` is a first wiring baseline. It uses the nearest observed integer wind-speed bin. It is not calibrated or validated on the case dataset. Its demo training data are fabricated, and all demo outputs remain synthetic. Do not claim prediction skill from them.

Do not use February SCADA values unless organizers confirm that they become available to the simulated predictor at each issue time. Do not train or validate on future observed weather while claiming operational forecast accuracy. Keep model artifact, feature list, training cutoff, input hash and dependency versions with results.
