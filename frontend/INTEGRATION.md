# Frontend handoff to Person 3

The frontend is an independent React application in `frontend/`. It can be built
and run with its own Docker image, or started alongside the existing Streamlit
application with the optional Compose `backend` profile. Its default mode is an
offline, explicitly synthetic demonstration.

No server endpoint is implemented by this change. The existing `app.py` serves
Streamlit and cannot answer the dashboard's JSON requests. Person 3 owns the
future integration service and any shared-contract or root-packaging decisions.
Person 1's data/weather modules and Person 2's model/evaluation modules remain
their existing interfaces. This document proposes integration work; it does not
assign changes inside another person's modules.

## Display interface

Implement `GET /api/dashboard?period=today` and `?period=yesterday` according to
[`API.md`](API.md). The exact TypeScript types are in [`src/types.ts`](src/types.ts),
and [`src/data.ts`](src/data.ts) contains the client boundary validation.

The payload carries a schema version, snapshot time, provenance, selected local
day, the two configured sites, hourly measurements and predictions, and upcoming
weather events. Use `Asia/Qyzylorda` for the displayed calendar day. Keep all 24
hourly interval-start rows; represent unavailable values as `null`.

The UI compares actual and predicted normalized power over matching, completed
hours and displays the sum in normalized-output hours (`n.u.·h`). It does not
assume MW, MWh, rated capacity, or a normalization scale. Resolve source semantics
before adapting any values. Future and unfinished hourly intervals cannot have
actual observations. The client deliberately rejects malformed, stale-event,
and unverified responses rather than substituting demo values.

Site IDs `turbine_1` and `turbine_2` refer to the coordinates in
[`locations.json`](../src/wind_forecast/weather/locations.json). They have not yet
been matched to real SCADA IDs. Keep the supplied site coordinates; NOAA's sampled
grid coordinate is separate weather-source metadata.

The existing Streamlit fixture uses IDs `T1` and `T2`; the dashboard requires
`turbine_1` and `turbine_2`. Map IDs explicitly using reviewed source metadata,
never by array order or by assuming the names identify the same physical sites.

## Upstream field mapping

These are candidate source fields, subject to confirmed units, interval meaning,
quality flags, availability, and turbine mapping:

| Dashboard field                                                        | Existing Python source                                                         | Adapter requirement                                                                                              |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `history[].time`                                                       | `Observation.observed_at`, `ForecastRow.valid_time`, `WeatherPoint.valid_time` | Resolve interval-start/end semantics before joining; output each selected local hour exactly once                |
| `actualPower`                                                          | `Observation.power_norm`                                                       | Establish hourly mean semantics; retain missing/invalid values as null                                           |
| `actualTemperature`, `actualWindSpeed`                                 | `Observation.temp_c`, `Observation.wind_ms`                                    | Use original observations available by the snapshot; do not fill with forecast weather                           |
| `actualWindDirection`                                                  | No field in the current `Observation` contract                                 | Leave null until an evidenced source or agreed contract extension exists                                         |
| `predictedPower`                                                       | `ForecastRow.prediction`                                                       | Select an eligible original issue and verify target/interval semantics                                           |
| `predictedTemperature`, `predictedWindSpeed`, `predictedWindDirection` | `WeatherPoint.temp_c`, `wind_ms`, `wind_direction_deg`                         | Use the corresponding eligible weather bundle; retain nulls and normalize direction convention                   |
| `status`                                                               | No operational-status field in these contracts                                 | Obtain status evidence; if unavailable, agree an explicit unknown-status extension rather than inventing a state |
| `events[].predictedEnergy`                                             | Numerical forecast rows over each event window                                 | Integrate hourly means under the documented units; return null when a complete event estimate is unavailable     |

Use `Observation.available_at`, forecast issue times, and weather provenance in
the server audit. Never treat `observed_at` alone as proof that a reading was
available at a simulated issue time. Serialize timestamps with seconds or
milliseconds precision as required by `API.md`; Python's default microsecond
precision is not accepted by the current browser validator.

The endpoint currently selects only the current local day and yesterday, not an
arbitrary archived date. January/February artifacts cannot be presented as
current data by changing their timestamps. If historical inspection is needed,
agree and implement an explicit date-selection extension in the API, UI, and
validation tests. Otherwise report the missing current inputs honestly.

## Suggested implementation order

1. **Confirm inputs and semantics.** Obtain the original SCADA file and the
   reviewed mapping, timezone, interval labels, observation availability rule,
   target definition, and forecast issue schedule. The current weather handoff
   proves one historical run; it is not a current 48-hour forecast or a complete
   observation/model dataset. Review [`docs/data.md`](../docs/data.md) and the
   existing Person 1 artifact manifests before selecting inputs.
2. **Create a small server adapter.** Add the endpoint within Person 3's approved
   integration scope. Read immutable observation, weather, and numerical forecast
   artifacts through existing interfaces. Validate the server's output against
   the frontend contract. Keep secrets and source credentials only on the server.
   Return an informative error until the requested day can be assembled honestly.
3. **Join forecasts without look-ahead.** Map each site's original identifier and
   valid interval explicitly. Select a documented forecast issue that was
   available before the compared interval. Preserve issue time, model ID, weather
   bundle ID, artifact hashes, and availability evidence in server audit records;
   retain overlapping original forecast issues. Never replace historical
   predictions with a newer run or derive them from later observations.
4. **Populate measured fields and provenance.** Supply observed power,
   temperature, wind speed, and direction only where original observations exist.
   The shared `Observation` record currently lacks wind direction, so leave
   `actualWindDirection` null until an evidenced source or backward-compatible
   extension is agreed. Forecast weather may supply predicted fields; it does not
   establish the corresponding observed measurement. Label the entire response
   `verified_original` only when all presented measurements, weather, and model
   outputs meet the documented verification policy.
5. **Supply upcoming weather events.** The current weather extraction contains
   wind and temperature; it does not establish precipitation, snow, or icing.
   Acquire the necessary forecast fields and define documented classification
   rules before emitting those event types. Return an empty event list or null
   energy estimates when evidence is unavailable. Event generation figures come
   from the numerical forecast over each event's hourly window; weather labels
   alone are not a percentage penalty. Keep weather windows current even when the
   user selects yesterday, and remove expired events.
6. **Connect and verify the deployment.** Add the adapter service in Person 3's
   integration configuration and place it on a Docker network shared with the
   frontend. Configure the environment below, then complete the acceptance checks.

The original source cache and small weather handoff are Git-ignored and are not
inside either image. Transfer required versioned artifacts separately, verify
their manifests, and mount them read-only into the integration service. The
frontend only needs the returned JSON. Root dependencies for new data decoders,
API frameworks, or model runtimes remain Person 3's integration decision.

## Runtime connection

Set these public deployment settings on the frontend container:

```dotenv
DASHBOARD_DATA_MODE=api
DASHBOARD_API_UPSTREAM=http://api:8000
```

`api` is an example service name; it must exist on the shared network. nginx
preserves `/api/dashboard` and its query string when proxying to that origin.
There is no API-key variable in the frontend. Use the existing
`DASHBOARD_BACKEND_URL` setting only for an optional browser navigation link to
Streamlit. It does not configure the data endpoint.

The proxy resolves service DNS at request time, allowing the frontend to start
before the adapter. A temporarily unavailable adapter produces a visible API
error; it does not switch the dashboard to synthetic mode. The frontend's
`/healthz` endpoint checks only static-server health, so monitor adapter readiness
separately. See [`README.md`](README.md) for ports, Compose commands, and custom
DNS configuration for non-Docker deployments.

## Acceptance checks for integration

- Build both images and run the optional Streamlit profile. Confirm health on
  frontend port 8080 and Streamlit port 8501, including the existing fixture flow.
- Start the API container after the frontend; check that API requests recover
  without rebuilding the frontend and retain the requested period/query.
- Load both days from a known, verified artifact and check matching hourly totals
  independently. Exercise a missing measurement, zero predicted total, and an
  unfinished current hour; these must not become fabricated observations.
- Check site selection, all four history charts, weather-window values, and
  browser timezone independence. Preserve explicit synthetic labeling in demo
  mode and verify an API outage remains visible.
- Confirm source issue/availability evidence survives in server records and that
  unverified or future observations cannot be presented as verified history.
- Exercise rainfall/snow/frost classification with actual supporting forecast
  fields; absent evidence must not create an event or output estimate. Do not sum
  overlapping event windows into an energy total.
- Run the frontend unit tests, production build, and Playwright browser suite
  documented in [`README.md`](README.md), followed by container health checks.
