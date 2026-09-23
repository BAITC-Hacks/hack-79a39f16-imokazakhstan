# Architecture and shared contract

```text
observations ──> data loader ─────────────┐
                                          v
weather provider ──> timestamp/provenance audit ──> numerical predictor
                                                   │
                                                   v
                                      forecast checks and versioned output
                                                   │
                                      agent trace / Streamlit demo
```

The offline fixture uses a bounded Python workflow and a deterministic local provider. Person 3 can add an OpenAI Responses API controller that calls allowlisted local tools. The controller may choose among retrieval, audit, prediction, inspection, and save tools; Python retains all timestamp, provenance, schema, and retry guards. The forecast values are always produced by a numerical predictor. If the API is missing, the fixture workflow still runs.

## Time policy

- Internal datetimes must be timezone-aware and are normalized to UTC at contract boundaries.
- `issue_time` is the simulated moment the prediction is made.
- `run_init_time` is when a weather model initialized. `available_at` is when its output could be obtained. These are distinct.
- For an hourly forecast, valid times are issue time + 1 through issue time + 24 or 48 hours.
- Historical data and model-fitting cutoffs are validated at the issue time. The starter defaults to a frozen-observation policy for February because the brief only promises measurements through January 31.
- All fixture results carry synthetic provenance. Historical mode must reject synthetic or unverified weather.

## Artifact keys

Forecast rows retain issue time, turbine, valid time and lead. Repeated rolling forecasts can overlap; preserve each issue time. Keep weather bundle ID, model ID, input hash, schema version and warnings with every forecast. Append versions instead of overwriting earlier runs.

## Known questions for the organizers

Confirm the timezone and interval labeling of the SCADA file, normalization definition and capacity, whether the scored target is per turbine or farm, February observation availability, issue schedule, required output format, and scoring metric. Keep assumptions in configuration until answered.
