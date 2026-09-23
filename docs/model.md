# Forecast model and evaluation

## Person 2 owns

- Keeping the empirical per-turbine wind-to-power baseline runnable and serializable.
- Comparing history-only statistical, tree, and neural models under the user's daily 06:00 / 48-hour schedule. Weather-based variants additionally require eligible archived weather for both training and replay.
- Using chronological development/validation splits and freezing choices before the February test.
- Reporting sample counts and MAE/RMSE by turbine and lead bucket, with the baseline comparison.

The included `EmpiricalPowerCurve` uses the nearest observed integer wind-speed bin. The initial SCADA diagnostic below evaluates this curve on real measured wind; it does not validate forecasts made with forecast weather. Actual history-only 48-hour replays are documented in the later sections. The separate demo training data are fabricated, and all demo outputs remain synthetic. Do not claim prediction skill from them.

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
The January wind at the target time is observed, not forecast. This diagnostic
has no forecast lead. The history-only replay below evaluates genuinely future
targets without needing future weather inputs.

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

For a weather-based challenger, obtain eligible archived forecast weather from
Person 1 and evaluate MAE/RMSE by turbine and forecast lead. History-only
challengers below are independent of that weather input path.

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

## Monthly replay: February 2025 through January 2026
The same selected wind-scenario architecture was evaluated with monthly expanding-window retraining. Each month at 06:00 UTC+5, fit only complete historical 48-hour target windows available at that cutoff; hold weights fixed for that month and refresh measured input history every day at 06:00. No weather API or future measured covariates enter a forecast.
Model architecture was previously selected using later 2025 data. Although each fit and each forecast is temporally causal, this retrospective architecture assessment is not a new independent model-selection test. February 2026 remains absent.
The main monthly grouping follows the actual production interval, not the issue month. An hourly mean ending at 00:00 belongs to the preceding calendar day. Separate daily forecast vintages for the same valid hour remain separate evaluation pairs. November 2024–January 2025 are unscored warm-up forecasts for calibrating 30/90-day error windows.
| Target month | Scored pairs | MAE | Model RMSE | Historical-mean RMSE | Absolute-error interval coverage |
|---|---:|---:|---:|---:|---:|
| 2025-02 | 2,660 | 0.3093 | 0.3588 | 0.3576 | 78.7% |
| 2025-03 | 2,814 | 0.3272 | 0.3779 | 0.4041 | 78.6% |
| 2025-04 | 2,375 | 0.3021 | 0.3574 | 0.3598 | 80.0% |
| 2025-05 | 2,562 | 0.2881 | 0.3532 | 0.3565 | 79.5% |
| 2025-06 | 2,732 | 0.2415 | 0.3182 | 0.3075 | 83.1% |
| 2025-07 | 2,944 | 0.2188 | 0.2939 | 0.3158 | 81.8% |
| 2025-08 | 2,948 | 0.2339 | 0.3108 | 0.3079 | 77.8% |
| 2025-09 | 2,876 | 0.2681 | 0.3393 | 0.3413 | 79.7% |
| 2025-10 | 2,548 | 0.2575 | 0.3116 | 0.3423 | 85.3% |
| 2025-11 | 2,880 | 0.3008 | 0.3490 | 0.3767 | 73.1% |
| 2025-12 | 2,948 | 0.3208 | 0.3687 | 0.3873 | 74.4% |
| 2026-01 | 2,976 | 0.2897 | 0.3433 | 0.3423 | 82.8% |

Overall: **RMSE 0.340804, MAE 0.279409**, 33,263 scored pairs. Historical mean RMSE is 0.350976; relative RMSE reduction is 2.90%. The model beats historical mean in 8 of 12 months and loses in February, June, August 2025 and January 2026. This is a modest and season-dependent gain.

There are 33,888 forecast rows in the requested target period; 625 lack a complete observed target hour. 24 daily turbine origins in the requested issue-month range were skipped for fewer than 18 complete recent power hours. Results do not cover those excluded origins.

### Prediction intervals, not answer-confidence scores

All interval variants use only matured out-of-sample errors, grouped by turbine and lead bucket (1–24 / 25–48 h). Compare both coverage and width/interval score; reaching coverage with a nearly full-range interval is weak predictive information. A minimum of 200 eligible errors is required; insufficient history produces a missing band.

| Method | Target coverage | Observed coverage | Mean width | Interval score ↓ |
|---|---:|---:|---:|---:|
| signed_30d | 80% | 78.44% | 0.877565 | 1.080189 |
| signed_90d | 80% | 78.24% | 0.890444 | 1.089390 |
| absolute_30d | 80% | 79.49% | 0.877324 | 1.192459 |

`signed_30d` and `signed_90d` use residual quantiles 0.1/0.9. `absolute_30d`
uses a finite-sample order statistic of absolute residuals for a symmetric band.
The latter is closest to nominal aggregate coverage, but the former has lower
interval score. None dominates on all criteria. These are evaluated interval
candidates, not guaranteed conformal coverage under serial dependence and monthly
model changes. November/December undercoverage persists. Bands are intentionally
not clipped to an unconfirmed physical capacity. The deployed saved model and its
fixed interval defaults were not silently replaced by this evaluation.

### Jev assessment (official documentation checked 2026-09-23)

The user did not have a precise model name; the identifiable Jev is TypeSafe's
System One model. Its Choice/Score `confidence` is a statistic of its answer
probability distribution, not a continuous wind-power prediction interval:
https://docs.typesafe.ai/confidence.

TypeSafe explicitly documents limitations with numeric precision and advises
keeping mathematical operations in code:
https://docs.typesafe.ai/model-jaggedness/jev-1.13.

Published Jev 1.13 pricing is $0.042 per million input tokens, outputs free:
https://docs.typesafe.ai/models. For illustration, 730 calls with 2,000 input
tokens each would cost about $0.061 at that rate, excluding any gateway markup
and retries. This is an estimate, not a measured API benchmark. No API key was
read, no customer data was sent, and no Jev API calls were made.

The issue is model suitability rather than token cost. Local calibration already
provides measured coverage against real power targets. Jev could be evaluated
later as a categorical risk/quality signal from textual maintenance logs, with
separate calibration against downstream errors; its answer-confidence must not
be relabeled as power-interval coverage. A different numerical forecasting model
can be assessed once its exact name is known.

### Reproduce and inspect

```bash
MPLCONFIGDIR=/tmp/imokazakhstan-matplotlib \
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 PYTHONPATH=src \
python3 -m wind_forecast.evaluation.monthly_replay \
  --turbine-1 '/Users/baimurzin_r/Downloads/Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 1.csv' \
  --turbine-2 '/Users/baimurzin_r/Downloads/Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 2.csv' \
  --output src/wind_forecast/models/artifacts/monthly-new

PYTHONPATH=src python3 -m unittest \
  wind_forecast.models.test_monthly_replay \
  wind_forecast.models.test_history wind_forecast.models.test_power_curve
```

Current local outputs are in
`src/wind_forecast/models/artifacts/monthly-202502-202601-v2/`:
`report.html`, `monthly_metrics.png`, `report.json`, `replay.csv`, `monthly.csv`,
and `monthly_groups.csv`. The latter includes every month × turbine × lead
bucket with counts, errors, coverage, widths and interval scores. These generated
files remain ignored by Git; the code and this documentation are committed.

Measured computation time: 18.28 seconds before rendering; interval calibration for all three methods took 0.56 seconds; peak process memory 192 MiB. No GPU or paid API was used.

## CPU resource and USD estimates (2026-09-23)

`evaluation/benchmark_cost.py` measures eight candidate architectures in separate
local processes, with two computation threads, on the real cached training data.
Every forecast contains 48 hours for both turbines. Training timings include a
single refit on all eligible data; inference is the median of 20 warm calls.
This benchmark does not update the chosen model or produce new accuracy scores.

| Candidate | Fit, seconds | Features + prediction, seconds | Peak process MiB | Saved model MB |
|---|---:|---:|---:|---:|
| Ridge, 7-day context | 0.044 | 0.01350 | 163 | 0.07 |
| Ridge, 30-day context | 0.016 | 0.00991 | 182 | 0.08 |
| ExtraTrees, shallow | 1.014 | 0.01809 | 186 | 7.38 |
| ExtraTrees, deep | 2.014 | 0.01847 | 178 | 17.62 |
| Histogram gradient boosting | 7.915 | 0.00810 | 338 | 0.24 |
| Wind ExtraTrees + lookup curve | 1.179 | 0.01919 | 172 | 7.64 |
| Wind ExtraTrees + smooth curve | 1.170 | 0.01857 | 177 | 7.64 |
| Selected: individual wind trees → power → mean | 1.039 | 0.03776 | 181 | 7.64 |

Peak memory includes libraries, cached data, training and benchmark machinery;
it is not an isolated model's minimum RAM requirement. Warm prediction excludes
CSV reads, process startup, provenance hashing and output formatting. The earlier
full Predictor benchmark was about 0.169 seconds for both turbines. Short timings
vary between runs and do not establish that longer-context Ridge is faster.

For transparent cost comparison, use the same illustrative **2 vCPU + 4 GiB**
allocation for every candidate. The published Linux/x86 US East (N. Virginia)
example on [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/) gives
$0.000011244 per vCPU-second and $0.000001235 per GiB-second, with a 60-second
minimum per task and durations rounded up to the next second. Thus:

```text
allocation USD/second = 2 × 0.000011244 + 4 × 0.000001235 = 0.000027428
task cost = max(60, ceil(startup + runtime)) × allocation USD/second
monthly compute = 30 forecast tasks + 1 training task
```

Assuming a 5-second startup, 2 seconds of forecast input preparation and 10 seconds
of training input preparation, all eight candidates fit within the one-minute
minimum: **$0.001646 per scheduled task and $0.0510 per 30-day month** for
30 forecasts plus one refit. With measured work slowed down fivefold, estimates
remain $0.0510–$0.0520 per month. Daily retraining in a separate task costs about
$0.0987 per month under the base assumptions. Keeping the same allocation running
continuously costs **$71.09 per 30 days** for compute alone.

Multiplying only measured feature extraction, prediction and fitting durations by
these rates gives $0.000009–$0.000224 per month; the selected model is about
$0.000060. These tiny numbers compare numerical workloads only, not billable
service totals. The task minimum dominates scheduled compute cost at this scale.
Accuracy should remain the deciding criterion among these CPU candidates.

These are local Mac timings converted using a cloud price example, **not cloud
measurements or a quote**. Startup and input preparation are explicit assumptions;
container image download is unmeasured. Storage, logging, network, public IPv4/NAT,
scheduling, UI hosting, weather/API fees, taxes and engineering are excluded.
The same allocation is used for comparability, without claiming that every model
requires 4 GiB. No paid resources were provisioned or API calls made.

Run after `history_experiment` has created its local cached dataset:

```bash
PYTHONPATH=src python3 -m wind_forecast.evaluation.benchmark_cost \
  --experiment src/wind_forecast/models/artifacts/history-48h-v3 \
  --output src/wind_forecast/models/artifacts/cost-estimate-new
```

The output directory must be new. `costs.md` contains the comparison;
`costs.csv` and `costs.json` retain timings, allocation, price sources, assumptions,
and cost breakdowns. The first measured run is in the ignored local directory
`src/wind_forecast/models/artifacts/cost-estimate-v1/`.

## Финальная модель и подключение к сайту (23 сентября 2026)

Новый автономный runtime: `wind_forecast.models.deployment:load_predictor`.
Основной кандидат — прямой CatBoost MultiRMSE с 48 выходами, 200 деревьями глубины 4.
317 признаков: собственная история мощности/ветра/температуры, сезон, номер турбины
и история соседней турбины. Контекст статистик до 30 дней, подробные лаги последней недели.
Это статистическая зависимость двух турбин, не физическая модель следа за турбиной.
Погода будущих часов не подставляется. Весам не нужны сеть, NVIDIA API или GPU.

Веса обучены на Brev и скачаны в
`src/wind_forecast/models/artifacts/deploy-final/`:
`catboost_neighbor.cbm` + `catboost_neighbor.metadata.json` — основной CPU-кандидат;
`patch_transformer.pt` + `patch_transformer.metadata.json` — альтернативный небольшой
трансформер с 76 608 параметрами (168 часов контекста, блоки по 12 часов, шаг 6,
два attention-слоя, ширина 64). Он вдохновлён PatchTST, но не является точной реализацией статьи.
Артефакты исключены из git; передавать их отдельно с metadata и проверкой SHA-256.
Обучение ограничено доступными данными до `2026-01-31T19:00:00Z`;
модель нельзя использовать для честной оценки более ранних дат.
Для исторических метрик использованы отдельные ежемесячные переобучения.

Локальный запуск из корня репозитория (Python 3.11/3.12):

```bash
python -m pip install -r src/wind_forecast/models/requirements-deploy.txt
PYTHONPATH=src python -m wind_forecast.models.deployment \
  --model src/wind_forecast/models/artifacts/deploy-final/catboost_neighbor.cbm \
  --metadata src/wind_forecast/models/artifacts/deploy-final/catboost_neighbor.metadata.json \
  --csv '/path/to/turbine 1.csv' '/path/to/turbine 2.csv' \
  --issue '2026-02-01T06:00:00+05:00' --output forecast.json
```

Для альтернативного трансформера дополнительно установить `torch==2.8.0` и заменить
пару путей на `.pt`/его metadata. Runtime по умолчанию CPU. Готовые локальные результаты
проверки — `deploy-final/local-validation.json`; прогнозы-примеры на февраль являются
прогнозами без доступного факта, а не результатами оценки точности.

### Промпт для Person 3: интеграция

> Подключи модель Person 2 к сайту, сохранив автономный mock-режим. Используй существующий
> загрузчик `load_team_predictor` и factory `wind_forecast.models.deployment:load_predictor`.
> Передай CBM и metadata из `src/wind_forecast/models/artifacts/deploy-final/`.
> Для этого history-only режима не требуй WeatherBundle и не создавай фиктивную погоду.
> Приведи вход к Observation: почасовые средние, timestamp — КОНЕЦ часа, timezone UTC,
> available_at не позже issue_time. Исходные CSV имеют локальное время UTC+5;
> предполагается начало десятиминутного интервала, шесть полных записей дают один час.
> Не агрегируй уже почасовые Observation повторно. Передай обе турбины T1/T2 за последние
> 30 дней, отфильтровав будущее и недоступные на момент выпуска записи. Выпуск строго
> 06:00 UTC+5, горизонт 24 или 48 часов, ежедневное обновление. Менее 18 доступных часов
> мощности за последние сутки — понятная ошибка пользователю; пропуски не скрывать.
> Уважай trained_through, SHA-256 и observation_policy; финальные веса нельзя применять
> к историческому issue_time раньше cutoff. Для февраля 2026 сейчас нет фактической мощности.
> Покажи график мощности 0–1 по часам, приблизительные 80% интервалы при наличии,
> название модели, время выпуска, последний доступный факт и предупреждение о прогнозе
> только по истории без будущей погоды. 0–1 не переводить в кВт/МВт без подтверждённой
> номинальной мощности. Не называй RMSE процентом точности. Не выводи API-ключи.
> Разделяй основной прогноз и любые будущие поправки: итог = основной прогноз + error.
> Переключатель экспериментального трансформера допустим, но более сложная модель
> не должна автоматически заменять более точную. Для ретроспективных сравнений загружай
> сохранённые replay-результаты, а не пересчитывай прошлое финальными весами.

Обучение и сравнение воспроизводят `evaluation/extended_replay.py`,
`run_extended_suite.py`, `remote_gpu_suite.py`, `final_fit.py`, `summarize_extended.py`.
Нейронная early-stopping выборка — последние 30 дней до cutoff; между ней и обучением
исключены пересекающиеся 48-часовые target-окна. Все нейронные CPU-результаты пересчитаны
с этим правилом в `neural-purged/`; первоначальные `extended-v2/` сохраняются для аудита.
Chronos — ретроспективное сравнение современных pretrained-весов: дата выпуска весов
позже части оценочного периода, состав pretraining неизвестен. Нельзя утверждать,
что эти веса реально были доступны на дату исторического выпуска прогноза.

Для этой модели `frozen_jan31` не позволяет честно обновлять прогноз весь февраль:
после первого выпуска свежего суточного контекста уже не хватит. Для ежедневных
выпусков нужны новые фактические измерения до 06:00 (`observation_policy=available`).
Runtime останавливается при недостаточном контексте, вместо подмены данных.

Ссылки на методы: [CatBoost MultiRMSE](https://catboost.ai/docs/en/concepts/loss-functions-multiregression),
[PatchTST](https://arxiv.org/abs/2211.14730),
[Chronos и LoRA](https://github.com/amazon-science/chronos-forecasting).

### Завершённое сравнение

19 конфигураций, 33 263 одинаковые пары прогноз–факт за февраль 2025 — январь 2026.
Каждый день выпуск в 06:00 UTC+5, 48 часов; supervised-модели переобучались ежемесячно.
Ранжирование вариантов — февраль–ноябрь; декабрь–январь — дополнительный контроль,
который уже просматривался ранее, поэтому это не новый независимый тест.

| Конфигурация | RMSE | MAE |
|---|---:|---:|
| **CatBoost + соседняя турбина, 200 деревьев CPU** | **0.335738** | 0.290722 |
| CatBoost GPU, 600 деревьев | 0.339462 | 0.291661 |
| Предыдущее wind-tree-scenarios | 0.340804 | 0.279409 |
| ARIMA(2,0,1) | 0.341299 | 0.290699 |
| Небольшой patch-transformer, ширина 24 | 0.348246 | 0.306581 |
| Patch-transformer GPU, ширина 64 | 0.349689 | 0.306053 |
| GRU | 0.350126 | 0.296007 |
| Graph-GRU, два узла | 0.350687 | 0.296378 |
| LSTM | 0.350947 | 0.296722 |
| Chronos Small + GPU LoRA 200 шагов/месяц | 0.367962 | 0.282587 |
| Chronos Base + GPU LoRA 200 шагов/месяц | 0.378582 | 0.290691 |

Выбран простой CatBoost: снижение RMSE относительно предыдущего решения около 1.49%,
но MAE ухудшился примерно на 4.05%. Это существенное ограничение выбора по RMSE.
На декабре–январе его RMSE 0.355702. Средняя абсолютная поправка по известным ошибкам
последних 30 дней ухудшила RMSE до 0.340090; в основной прогноз она не добавляется.
Ансамбль 75% CatBoost + 25% предыдущей модели дал 0.335107: дополнительное улучшение
лишь около 0.19%, поэтому для простого развёртывания оставлена одна модель.
Признаки соседа не доказывают физическое влияние; для переноса на новые турбины
нужны их идентификация, геометрия, направление ветра и отдельная проверка.

Ретроспективные 80% интервалы CatBoost имели покрытие 79.51% и среднюю ширину 0.8323
на шкале 0–1. Это широкая неопределённость, а не высокая точность.
Metadata финальных весов содержит отдельные радиусы по турбинам и суткам горизонта,
полученные из out-of-fold ошибок последних 30 известных дней. Для новых выпусков
их нужно обновлять по созревшим фактическим значениям, не по будущим таргетам.

Полный интерактивно открываемый HTML-отчёт: `models/artifacts/final-comparison/report.html`
(относительно `src/wind_forecast/`); рядом `report.json`, `comparison.csv`, `monthly.csv`,
`turbine_leads.csv`, сохранённые прогнозы и отдельные additive_errors.
Первый GPU CatBoost завершился ошибкой режима boosting; повтор с `boosting_type=Plain`
успешно прошёл все месяцы, обе записи сохранены для аудита.

Brev: 4×L40S, тариф каталога $4.224/час за инстанс. Сам GPU-suite занял 355 секунд
плюс загрузку/подготовку; арифметическая стоимость этих 355 секунд около $0.417,
это не итоговый счёт за инстанс. Цена CPU-прогнозирования по указанной в отчёте
Fargate-модели расходов около $0.00188/день с ежемесячным переобучением, без сайта,
хранилища, сети и налогов; это расчёт, не оплаченный облачный замер.
На локальном CPU весь прогноз двух турбин с подготовкой признаков занял около
0.015 секунды для CatBoost, 0.027 секунды для сохранённого GPU-trained трансформера.
Веса: 1 286 984 и 317 162 байта соответственно. GPU для ежедневного запуска не требуется.

Проверено: 46 тестов приложения, 18 тестов моделей; дополнительно реальные скачанные
веса проверены на 96 выходов, неизменность при добавлении будущих данных, фильтрацию
поздно доступных наблюдений, ограничение даты обучения, SHA-256, интервалы и совместимость
с существующим `load_team_predictor`. Brev оставлен работающим по последней просьбе пользователя.
