# Forecast model and evaluation

## Person 2 owns

- Keeping the empirical per-turbine wind-to-power baseline runnable and serializable.
- Adding a CatBoost challenger only when archived weather covariates are available for both training and replay.
- Using chronological development/validation splits and freezing choices before the February test.
- Reporting sample counts and MAE/RMSE by turbine and lead bucket, with the baseline comparison.

The included `EmpiricalPowerCurve` uses the nearest observed integer wind-speed bin. The SCADA diagnostic below evaluates this curve on real measured wind; operational forecast quality is not yet validated. The separate demo training data are fabricated, and all demo outputs remain synthetic. Do not claim prediction skill from them.

Do not use February SCADA values unless organizers confirm that they become available to the simulated predictor at each issue time. Do not train or validate on future observed weather while claiming operational forecast accuracy. Keep model artifact, feature list, training cutoff, input hash and dependency versions with results.

## SCADA baseline v1 (Person 2)

Implemented `EmpiricalPowerCurve` v1 with JSON save/load, deterministic nearest-bin
selection, finite numeric checks, a real prediction-input SHA-256, and a training
availability cutoff enforced by `predict`. The existing
`predict(request, observations, weather)` interface remains unchanged.
`fit_samples` is for numeric diagnostics; its output cannot enter the forecast API
until time metadata is established. `fit(Observation[])` records the latest of
observation time and availability time across usable training samples.

### Dataset and time assumptions

The two supplied CSVs contain 142,360 and 149,499 records, respectively, from
2023-03-11 00:00 through 2026-01-31 23:50. They contain no February records,
despite their filenames. The user confirmed a fixed UTC+5 offset. UTC conversion
uses that offset for the entire source period, without inferred DST rules.

There are no duplicate timestamps or missing/nonfinite numeric values in these
files. The 10-minute grid has 9,992 missing slots for T1 and 2,853 for T2. Gaps are
not filled. Power remains in the source normalized scale; observed values are
0–1, but the physical normalization and turbine capacity remain unconfirmed.

Availability is conservatively ASSUMED to be timestamp + 10 minutes because the
interval-label convention is unknown. This is not verified publication latency.
Person 1 must confirm interval labeling and real availability for historical
replay. The evaluation-only CSV reader lives in `evaluation/train_baseline.py`;
it does not replace Person 1's production ingestion adapter.

### Chronological diagnostic

Train: source timestamps before 2026-01-01 00:00 UTC+5, available by that boundary.
Validation: January 2026, available by 2026-02-01 00:00 UTC+5. No random split,
parameter search, or February training is used. The baseline averages power in
integer wind-speed bins per turbine, using the nearest populated bin at inference.
Unknown turbines use the pooled curve. Temperature is not a feature.

**These are measured-wind curve diagnostics, NOT 24/48-hour forecast scores.**
The January wind at the target time is observed, not forecast. No lead-time
metrics can honestly be reported without archived weather forecasts.

| Turbine | Training rows | Validation rows | Curve MAE | Curve RMSE | Constant training-mean MAE | Constant training-mean RMSE |
|---|---:|---:|---:|---:|---:|---:|
| T1 | 137,896 | 4,464 | 0.041282 | 0.066795 | 0.309689 | 0.349587 |
| T2 | 145,035 | 4,464 | 0.043933 | 0.079524 | 0.311226 | 0.350476 |

Scores use the native 10-minute rows, not hourly averages. They do not establish
performance with forecast weather or resolve the difference between measured
wind and weather-model wind height/location. January is now a development
validation period; it must not be advertised as an untouched final test after
further model tuning.

### Reproduce

From the repository root (Python 3.11+, no third-party packages required):

```bash
PYTHONPATH=src python3 -m wind_forecast.evaluation.train_baseline \
  --turbine-1 '/Users/baimurzin_r/Downloads/Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 1.csv' \
  --turbine-2 '/Users/baimurzin_r/Downloads/Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 2.csv' \
  --utc-offset-hours 5 \
  --availability-delay-minutes 10 \
  --output src/wind_forecast/models/artifacts/scada-baseline-v1

PYTHONPATH=src python3 -m unittest wind_forecast.models.test_power_curve
PYTHONPATH=src python3 scripts/run_demo.py
```

Use a NEW output directory for each run: existing directories are rejected to
preserve earlier artifacts. Adjust input paths on another machine. Raw CSVs are
not copied or committed. Artifacts stay in the repository's ignored model-artifact
folder and must be handed off separately.

- `validation_model.json`: trained before January; this model produced the scores above.
- `model.json`: refitted on all 291,859 usable records through January for subsequent integration.
- `report.json`: source SHA-256 hashes, coverage, metrics, model hashes, and limitations.

Both models include features, source hashes, sample counts, timestamp assumptions,
training cutoff and Python version. The final model's latest assumed availability
is **2026-01-31 19:00:00 UTC** (2026-02-01 00:00 UTC+5). It cannot forecast an issue
time earlier than that cutoff. January scores do not apply to this refitted model.

Final `model.json` SHA-256:
`9e6cadeee157c044dccb59d59bc6d9861016a3943fc7c26bde412656a97c7a7c`.

Person 3 can load the final model as follows:

```python
from wind_forecast.models.power_curve import EmpiricalPowerCurve

model = EmpiricalPowerCurve.load(
    'src/wind_forecast/models/artifacts/scada-baseline-v1/model.json'
)
# After confirming SCADA availability assumptions and supplying eligible weather:
# result = model.predict(request, observations, weather)
```

Next: obtain eligible archived forecast weather from Person 1, agree hourly
aggregation and issue-time conventions, then evaluate MAE/RMSE and counts by
turbine and forecast lead. Add a challenger only after that input path is ready.
