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

## Daily SCADA-only 48-hour forecasting experiment

The user clarified the operating schedule: every day at **06:00 UTC+5**, receive
wind, power, temperature and timestamps through 06:00 and forecast the next 48
hours. Use no external weather for this first model. February actual power is the
future scoring target; the official loss formula remains unspecified, so this
experiment selects on RMSE and also reports MAE. This supersedes the earlier
assumption that February observations would remain frozen at January 31.

### Replay protocol

- Only the supplied two real CSVs are used. February targets are absent.
- Source interval-start timestamps are an explicit assumption. Six ten-minute
  records become one hourly mean labeled by its END; incomplete hours are missing.
  A row at 05:50 becomes available at 06:00; a row at 06:00 is not used at 06:00.
- Every issue is at 06:00 local time. Leads 1..48 refer to hourly interval ends.
  Both vintages of overlapping forecast hours are retained, not overwritten.
- Base training includes complete target windows available by 2025-09-01 06:00.
  Missing training target windows are excluded; missing evaluation targets are
  never filled. Feature imputers are fitted only on training data.
- September out-of-sample forecasts train the optional learned error correction.
  October–November select the candidate; all selection labels must be available
  by 2025-12-01 06:00. December–January assess the frozen selection.
- December–January was also inspected during exploratory implementation runs,
  and January appeared in the earlier measured-wind diagnostic. These results
  are development evidence, not a pristine competition test or evidence of
  statistically significant superiority. February remains unscored.
- 1,397 complete daily turbine windows train the base models; 114 selection
  origins and 124 control origins yield 5,332 and 5,852 scored hourly pairs.
  Missing recent history may exclude an issue; counts and exclusions matter.

### Hypotheses and measured results

18 candidates include persistence, daily profile, weekly mean, fixed training
mean, direct Ridge (7/30-day summaries), two direct ExtraTrees configurations,
direct histogram gradient boosting, forecast wind followed by LUT/PCHIP,
averaging mapped individual tree forecasts, additive error corrections (7/30
days and a learned correction), and three blends with the best direct model.
All use CPU; no NVIDIA calls or future measured wind are used.

| Candidate | Selection RMSE | Control RMSE | Control MAE |
|---|---:|---:|---:|
| Wind tree forecasts → PCHIP → mean power (selected) | 0.330823 | 0.355808 | 0.305129 |
| Mean predicted wind → PCHIP | 0.332150 | 0.357185 | 0.302265 |
| Direct small ExtraTrees | 0.338195 | 0.357835 | 0.316412 |
| Direct histogram boosting | 0.347221 | 0.372905 | 0.336230 |
| Constant training mean | 0.360160 | 0.364937 | 0.321859 |
| Last measured power | 0.438743 | 0.471765 | 0.351816 |

The selected model improves control RMSE by approximately **2.5%** over constant
training mean, and 24.6% over persistence. The small improvement over the stronger
constant baseline is the relevant limitation. Choosing MAE instead of RMSE can
change the preferred model; the official metric is still needed.

| Selected model control group | n | MAE | RMSE |
|---|---:|---:|---:|
| T1, 1–24 hours | 1,478 | 0.291901 | 0.351238 |
| T1, 25–48 hours | 1,454 | 0.321075 | 0.362680 |
| T2, 1–24 hours | 1,472 | 0.289095 | 0.348384 |
| T2, 25–48 hours | 1,448 | 0.318919 | 0.360907 |

Selected architecture: an ExtraTrees regressor with 160 trees, maximum depth 8,
minimum leaf size 15, and two CPU workers forecasts 48 wind values directly.
Features are own-turbine hourly lagged wind/power/temperature, summaries over
6/24/72/168/720 hours, coverage, season, and turbine ID (159 features). Each tree's
wind forecast is mapped through a per-turbine PCHIP curve and the resulting
powers are averaged. Tree disagreement is not a calibrated atmospheric ensemble.
No physical wake effect or causal neighbor effect is inferred from these files.

The correction form is explicitly `final = base + predicted_error`. Corrections
use only matured, previously issued forecast errors; their unobserved future
errors never enter an update. All tested correction variants worsened selection
RMSE, so the saved point predictor has **zero added correction**. No blend beat
the selected model on the selection period, so no second power model is deployed.

Fixed empirical P10/P90 offsets achieved only **71.9%** control coverage against
an 80% target. Updating offsets using the preceding 30 days of matured errors
achieved **77.7%**, with a very wide mean width of **0.949** in source power units.
The standalone saved predictor currently uses fixed offsets; dynamic intervals
are evaluated in replay and require a forecast/actual history store at integration.
Neither intervals nor tree outputs have formal coverage guarantees. Bands are
not clipped to an assumed capacity and can extend beyond the observed 0–1 range.
Intervals following the final refit have not been independently validated.

### Artifacts and reproducibility

Latest local output directory: `src/wind_forecast/models/artifacts/history-48h-v3/`
(ignored by Git, hand off separately).

- `report.html`: readable Russian report and first control-issue graph.
- `report.json`: all 18 candidates, per-turbine/lead metrics, source hashes,
  dependencies, timings, caveats, and interval coverage.
- `replay.csv`: forecast vintages, base, additive correction, actuals and intervals.
- `model.joblib`, `model_metadata.json`: final refit on eligible available data.
- `replay_models.joblib`: frozen models that produced development/control scores.
- `experiment_data.joblib`: local cache for refitting; contains source-derived data.
- `inference_check.json`: interface equivalence and timing checks.

Final model SHA-256:
`3eec58238e05be0bbcb3ca963ca901055835cbc83861f4631aee68b7e51ebdcf`.

The final model is 7.64 MB; its wind-model fit took 1.16 seconds. Measured median
inference with feature construction, input audit and hashing for 2 × 48 outputs
was **0.169 seconds** across five warm calls on the current machine. This excludes
CSV reading and artifact loading. Peak experiment process memory was **276 MiB**;
this is not an isolated measurement of inference RAM. GPU was not used.

Final training cutoff is 2026-02-01 00:00 UTC+5, not the September cutoff of the
scored models. Final-model future accuracy must be evaluated prospectively.
The supplied CSV lacks 00:00–06:00 on February 1: refresh these records before a
fully up-to-date February 1 06:00 issue. A stale latest hour produces a warning;
fewer than 18 complete recent power hours rejects the request.

Optional experiment dependencies are listed in
`src/wind_forecast/models/requirements-history.txt`; Person 3 should integrate an
optional packaging extra. Root dependencies, app, weather, shared schemas and
other people's modules were not changed. The original offline mock still runs
without these optional model imports.

```bash
# Optional, if the packages are not already installed:
python3 -m pip install -r src/wind_forecast/models/requirements-history.txt

OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 PYTHONPATH=src \
python3 -m wind_forecast.evaluation.history_experiment \
  --turbine-1 '/Users/baimurzin_r/Downloads/Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 1.csv' \
  --turbine-2 '/Users/baimurzin_r/Downloads/Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 2.csv' \
  --output src/wind_forecast/models/artifacts/history-48h-new

PYTHONPATH=src python3 -m wind_forecast.evaluation.refit_history \
  --experiment src/wind_forecast/models/artifacts/history-48h-new

PYTHONPATH=src python3 -m unittest \
  wind_forecast.models.test_history wind_forecast.models.test_power_curve
```

Use a new directory each time. Refit currently supports the selected
`wind_tree_scenarios` architecture and fails clearly if a new experiment selects
another model. For integration:

```python
from wind_forecast.models.history_predictor import HistoryPowerPredictor

predictor = HistoryPowerPredictor.load(
    'src/wind_forecast/models/artifacts/history-48h-v3/model.joblib'
)
# request: 06:00 UTC+5, 24/48 hours, T1/T2, at/after the model training cutoff.
# observations: original ten-minute intervals, only data available at issue time.
result = predictor.predict(request, observations, weather=None)
```

`predict` preserves the shared Predictor signature but uses no weather bundle.
The current Person 3 workflow fetches weather unconditionally: Person 3 must add
an explicit history-only path before using this model through the app. The direct
CLI `python3 -m wind_forecast.evaluation.run_history --help` already works without
that integration. It writes forecasts and a manifest to a new output directory.
Only load trusted joblib artifacts. Hourly results are not predictions of
individual ten-minute fluctuations; output resolution must be agreed with the
organizers before scoring February.

### Next data, ordered by likely value and acquisition effort

1. **Issued weather forecasts** (wind components at a suitable rotor height,
   direction, temperature, pressure, neighboring grid points). Pair original run
   and release times with SCADA. Open-Meteo documents a Single Runs API preserving
   run structure, whereas its stitched Historical Forecast series alone does not
   provide the same replay semantics. Check access, date coverage and publication
   availability before use: https://open-meteo.com/en/docs/historical-forecast-api.
   NOAA also documents GFS archives:
   https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast.
   Person 1 owns retrieval and eligibility checks; no such data entered this model.
2. **Equipment metadata**: coordinates, hub height, rotor diameter, rated capacity
   and normalization definition. Usually available from the operator/passport;
   needed for weather extraction and physical interpretation, not a new sensor.
3. **Existing SCADA channels**: wind direction, yaw/pitch, turbine availability,
   curtailment/maintenance codes. First ask for exports of existing signals.
   Future operating state is only a feature when known at issue time (e.g. planned
   maintenance); otherwise only past states or a forecast of availability may be used.
4. **Variability and density**: the variance of past ten-minute mean winds within
   an hour can already be calculated. True within-interval turbulence requires
   higher-frequency measurements and is not recoverable from six means. Check
   existing pressure and high-frequency wind logging before buying sensors.
5. **Neighbor geometry and direction**: only after the above, test spatial weather
   features and wake corrections. A ground anemometer is not automatically a
   substitute for rotor-height wind.

NVIDIA explanation: an API key grants access to an endpoint, but the documented
hosted FourCastNet demo chooses predefined input cases (`input_id`); arbitrary
operational forecasts require a suitable endpoint plus an initial atmospheric
state. It is not a model that forecasts global weather from these two CSVs.
References: https://docs.api.nvidia.com/nim/reference/nvidia-fourcastnet-infer and
https://docs.nvidia.com/nim/earth-2/fourcastnet/latest/quickstart-guide.html.
No NVIDIA key, calls, or GPU resources are needed for this experiment.
