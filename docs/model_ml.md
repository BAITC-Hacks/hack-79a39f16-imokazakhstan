# Numerical ML model

`src/wind_forecast/models/gradient_boosting.py` implements
`HistogramPowerRegressor`. It learns a separate supervised regression model for
each turbine using scikit-learn's histogram gradient boosting. CPU training is
sufficient for the organizer's hourly datasets; no GPU or API key is required.
The model has fixed hyperparameters and deterministic random state.

## Inputs and output

```python
from wind_forecast.models.gradient_boosting import HistogramPowerRegressor

model = HistogramPowerRegressor().fit(
    observations,                 # list[Observation], complete hourly measurements
    issue_time=request.issue_time,
    training_limit=training_limit,
    turbine_ids=request.turbine_ids,
)
result = model.predict(request, observations, weather_bundle)
training_report = model.training_report  # JSON-serializable diagnostics
```

Training needs at least 168 valid hourly wind/power pairs per selected turbine.
The fit method only admits measurements ending before or at `training_limit`
and available before or at `issue_time`. It rejects duplicate hours. Bad quality
flags, missing or nonfinite wind/power, and negative wind are excluded and counted.
`training_limit` is additionally capped at issue time. All timestamps are UTC.

Features are wind speed in m/s, temperature in °C, and sine/cosine encodings of
UTC hour and annual season. Calendar features are known at forecast time. Each
prediction uses the weather for that future hour; it never reads future measured
power or wind. Missing temperature follows the estimator's learned missing-value
branches. Fitting does not impute from future data, shuffle time, or enable the
estimator's random internal validation split.

The returned `ForecastResult` contains exactly 24 or 48 rows per turbine.
Each row has issue time, UTC valid hour end, lead hours, and predicted normalized
active power. Values retain the original dataset scale: the code does not assume
normalization means 0–1, and it does not convert to MW. No uncertainty quantiles
are claimed. Missing wind or incomplete weather blocks prediction; wind outside
the training range produces an explicit degraded warning.

## Analysis and validation

For each turbine, the most recent 20% of eligible rows (at least 24 and at most
720) form a chronological holdout. The diagnostic model is fitted on earlier
rows available by the hour immediately before that holdout. Reporting-delayed
training rows are purged. A fixed empirical wind-bin baseline uses precisely the
same earlier data. The report contains each model's MAE, RMSE, and signed bias,
sample counts, date boundaries, data ranges, missing-value counts, and a training
data hash. These errors use the dataset's normalized units.

**This is a measured-weather regression holdout. It is not a 24–48 hour weather
forecast backtest.** The holdout gives the model actual measured wind/temperature;
deployed forecasts use numerical weather estimates, whose errors and grid/hub
height differences add further uncertainty. Full forecast evaluation needs
original weather issued before each validation issue plus withheld production.
The provided files contain no February target labels, so the application cannot
report February accuracy from those files.

A local run on the supplied CSVs, assuming UTC timestamps, interval starts, and
zero reporting delay, fitted 23,667 T1 hours and 24,785 T2 hours. With issue time
February 1, 2026 00:00 UTC, the final 720 eligible hours formed the diagnostic
holdout (January 2 01:00 through February 1 00:00 UTC hour ends). scikit-learn
1.9.1 and the default parameters produced these **measured-weather** errors:

| Turbine | ML MAE | Wind-bin MAE | ML RMSE | Wind-bin RMSE |
|---|---:|---:|---:|---:|
| T1 | 0.02447 | 0.04002 | 0.04980 | 0.06429 |
| T2 | 0.02538 | 0.04607 | 0.06404 | 0.08102 |

The timezone assumptions still need organizer confirmation. These measurements
show that the regression learns useful relationships in these files; they do
not establish the accuracy of future weather-driven production forecasts.

After diagnosis, a fresh model is fitted on all eligible history for the actual
forecast. Hyperparameters and model family are fixed rather than selected from
holdout results. Therefore a baseline can score better, and the report preserves
that result. Repeatedly tuning against this holdout would require a separate final
evaluation period. Training diagnostics also preserve the final training period
and latest data availability time for audit.

## Team integration

The application fits this model for real runs. The small synthetic fixture keeps
the existing empirical baseline so the offline demo remains lightweight. A
teammate can still replace the predictor using the factory/artifact contract in
[application_io.md](application_io.md). A replacement should preserve target
units, time eligibility, turbine/hour coverage, and honest evaluation labels.

For better accuracy, train using archived forecast weather matched to historical
issues, validate separately for 24- and 48-hour leads, and account for turbine
availability when those measurements become available. Reanalysis or present-day
weather cannot be substituted for an original historical forecast in a scored run.

The implementation uses the documented native missing-value support and disables
automatic early stopping in
[scikit-learn's HistGradientBoostingRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).
