# Dashboard data contract

The React frontend defaults to a deterministic, offline **synthetic demonstration**. All power, temperature, wind, event timing, status, and event energy values in this mode are authored display fixtures. They are not measurements or model results. Coordinates come from `src/wind_forecast/weather/locations.json`; the labels “Demo site 01” and “Demo site 02” do not establish a mapping to real SCADA turbine IDs.

The existing Python application is Streamlit and does **not** expose this dashboard REST endpoint. Set `dataMode` to `api` only when an adapter implementing this contract is available. The frontend requests `GET <apiUrl>?period=today` or `?period=yesterday`, preserving other endpoint query parameters. API failures remain visible; the frontend never silently substitutes demonstration data. `backendUrl` is a navigation link to the existing application, not a data endpoint.

## Units and interval rules

- Every timestamp is ISO 8601 with seconds and an explicit timezone (`Z` or an offset). Fractional seconds are optional and limited to three digits. For Python serialization, use `value.isoformat(timespec="seconds")` or `value.isoformat(timespec="milliseconds")`; the default six-digit microsecond representation is rejected by the current validator.
- The site display timezone is `Asia/Qyzylorda` (UTC+05:00). `date` is the selected site's calendar day, independent of the user's browser timezone.
- Each turbine has exactly 24 history rows, sorted from local 00:00 through 23:00. `time` labels the **start** of the one-hour interval `[time, time + 1 hour)`. Pad missing values with `null`; do not remove intervals or replace missing data with zero.
- Power is an hourly mean in the source's normalized units, displayed as `n.u.`. A sum of hourly means multiplied by one hour is displayed as `n.u.·h`. It is not MW or MWh. No capacity, normalization range, farm aggregation, or conversion to physical energy is assumed.
- Actual values must be `null` for any interval that has not finished by `generatedAt` and the browser's current time. Yesterday normally has 24 completed intervals, but measurements may still be missing.
- Temperatures use °C, wind speed uses m/s (nonnegative), and wind direction uses meteorological degrees in `[0, 360)`. The API adapter must normalize direction to this convention before delivery. Direction is a circular variable: 359° and 1° are close, and a chart crossing north is not a large physical rotation.
- Every numeric field is required and must contain a finite JSON number or `null`. Numeric strings, omitted fields, `NaN`, and infinity are invalid.
- Energy comparisons use only intervals that contain **both** actual and predicted power. `deviationPercent = 100 × (actualEnergy − predictedEnergy) / predictedEnergy`. An empty comparison or zero predicted total produces no percentage. An incomplete day is compared over its completed matched intervals; future predictions are excluded from the comparison total.

## Response

Return HTTP 200 and `Content-Type: application/json`. Types are defined in [`src/types.ts`](src/types.ts) and validated in [`src/data.ts`](src/data.ts).

| Field           | Meaning                                                                                |
| --------------- | -------------------------------------------------------------------------------------- |
| `schemaVersion` | Exactly `1`                                                                            |
| `provenance`    | `synthetic` or `verified_original`; `unverified` and unknown values are rejected       |
| `generatedAt`   | When the dashboard snapshot was built; cannot be over five minutes in the future       |
| `timezone`      | Exactly `Asia/Qyzylorda`                                                               |
| `date`          | `YYYY-MM-DD`, matching the current site day or its preceding day according to `period` |
| `period`        | Exactly the requested `today` or `yesterday`                                           |
| `turbines`      | The two unique configured sites and their hourly histories                             |
| `events`        | Upcoming event windows during the 48 hours after `generatedAt`; an empty list is valid |

Each turbine supplies `id`, `name`, `latitude`, `longitude`, `status`, and `history`. IDs and coordinates are:

| Site ID     |  Latitude | Longitude |
| ----------- | --------: | --------: |
| `turbine_1` | 43.645150 | 78.535604 |
| `turbine_2` | 43.643198 | 78.538828 |

Coordinates are checked to a tolerance of 0.000001 degrees. Site IDs must be explicitly mapped to the real SCADA source before verified data can be served. `status` is one of `operating`, `attention`, or `offline`; the adapter must derive it from appropriate operational evidence.

Every history row has:

```ts
{
  time: string;
  actualPower: number | null;
  predictedPower: number | null;
  actualTemperature: number | null;
  predictedTemperature: number | null;
  actualWindSpeed: number | null;
  predictedWindSpeed: number | null;
  actualWindDirection: number | null;
  predictedWindDirection: number | null;
}
```

Every event has a unique `id`, `type` (`rain`, `frost`, `snow`, `clear`, or `storm`), `severity` (`low`, `moderate`, or `high`), `title`, `description`, `recommendation`, `startsAt`, `endsAt`, and `predictedEnergy`. Event windows start and end on hourly boundaries, start no earlier than `generatedAt`, and end within its next 48 hours. `endsAt` is exclusive and must be after `startsAt`. Responses containing events already ended at request time are rejected; refresh the snapshot and remove expired events. The client requests API responses with `cache: 'no-store'`. `predictedEnergy` maps each site ID exactly once to a number in `n.u.·h` or `null`. The forecast energy covers that event's window for that site. Events may overlap; do not sum event cards into a total because overlapping hours would be counted more than once.

The selected day affects the history and comparison. Upcoming weather always refers to the current snapshot's next 48 hours, including when Yesterday is selected.

## Adapter responsibilities and ownership

Person 3 should own the future integration adapter. This frontend does not modify the Python shared contracts, data pipeline, model, or Streamlit application.

Before exposing `verified_original`, the adapter must verify the source observations and original weather provenance, the SCADA site mapping, timestamp and interval conventions, normalization, and the numerical forecast artifacts. The browser validates shape and time consistency; it cannot independently certify provenance from a response label.

Join actual observations and forecast rows by turbine and valid interval. Preserve upstream issue time, weather bundle ID, model ID, and original availability evidence in the adapter's auditable records. Select a documented forecast issue for each interval that was available before the interval began. Do not compare a historical observation to the newest overwritten forecast or a forecast that used later observations. Preserve each original issue separately upstream. Convert interval-end labels to the dashboard interval-start convention explicitly, after the source convention is confirmed. Do not shift the timestamps by guesswork.

Historical provenance requirements from the Python contracts remain in force. An eligible original-weather payload alone does not establish that paired power measurements or forecasts are verified. If only some inputs have been verified, do not label the assembled dashboard `verified_original`. Use a clearly synthetic demonstration separately, or return an informative API error while verified integration is incomplete.

Weather event classification and event-window energy must come from the server's numerical forecasting workflow and documented event rules. Rain, frost, snow, and storms do not by themselves determine a universal percentage penalty. The client displays supplied event energy; it does not compute a power forecast from weather. Missing forecast values are `null`.

Keep provider credentials in the backend environment. Runtime frontend config is public JavaScript and must contain no API keys or secrets. For deployment, prefer the container's same-origin `/api/` proxy; a direct cross-origin endpoint must deliberately permit the frontend origin through CORS. See [`README.md`](README.md) for container configuration.
