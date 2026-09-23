# Supplied SCADA files

This profile was computed directly from the two user-supplied files named
`Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 1.csv`
and `Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 2.csv`.
The filenames mention February 28, but the records end on **January 31, 2026 at
23:50:00**. Neither attachment contains February 2026 observations or targets.

Both files use UTF-8 without a BOM, comma separators, decimal points, and five
columns. The timestamp format is `YYYY-MM-DD H:MM:SS`; hours may have one digit.

| Source column | Meaning established by the header | Adapter output |
|---|---|---|
| `ID` | Source row identifier | Retained only through the source hash |
| `Статистическое время` | Measurement timestamp; timezone unspecified | Hour ending in UTC |
| `Средняя скорость ветра(m/s)` | Mean wind speed in m/s | `wind_ms` |
| `Нормализованная активная мощность` | Normalized active power | `power_norm` |
| `Средняя температура окружающей среды(°C)` | Mean ambient temperature in °C | `temp_c` |

The normalization denominator, turbine capacity, timezone, whether timestamps
start or end each interval, and reporting delay are not declared in the files.
The application records the selected timezone, interval label, and delay in its
source report. Confirm them with the organizers before submission. Normalized
power must not be labeled MW or MWh without a confirmed conversion.

## Measured coverage and quality

| Statistic | Turbine 1 | Turbine 2 |
|---|---:|---:|
| Rows excluding header | 142,360 | 149,499 |
| First timestamp | 2023-03-11 00:00 | 2023-03-11 00:00 |
| Last timestamp | 2026-01-31 23:50 | 2026-01-31 23:50 |
| Expected ten-minute slots inside full span | 152,352 | 152,352 |
| Missing ten-minute slots | 9,992 | 2,853 |
| Gaps between adjacent records | 67 | 178 |
| January 2026 records | 4,464 | 4,464 |
| Complete calendar hours in January | 744 | 744 |
| February 2026 records | 0 | 0 |
| Mean-wind column minimum/maximum (m/s) | 0.00 / 22.97 | 0.11 / 21.43 |
| Normalized-power column minimum/maximum | 0.00 / 1.00 | 0.00 / 1.00 |
| Ambient-temperature minimum/maximum (°C) | −19.26 / 43.48 | −19.08 / 43.97 |

Timestamps are increasing and lie on ten-minute boundaries. Both files have no
duplicate timestamps or IDs, empty numeric fields, unparsable numeric values,
or nonfinite numbers. These structural checks do not establish sensor accuracy.
Gaps in earlier months must not be filled with zero production. Turbine 1's
longest gap extends from May 18, 2024 at 03:40 to June 28 at 18:40.

January's 744 complete hours count the source calendar groups before timezone
or interval interpretation. With `interval_label="start"`, the last six samples
of January form an hour ending February 1 at 00:00 in the selected source zone.
That endpoint describes the completed January interval; it does not create a
February target measurement.

Using `source_timezone="UTC"`, `interval_label="start"`, and zero reporting
delay as an explicitly recorded smoke-check configuration, the adapter produces
23,667 hourly observations for turbine 1 and 24,785 for turbine 2. It drops 96
partially populated hours containing 358 samples for turbine 1, and 219 hours
containing 789 samples for turbine 2. Entirely absent hours produce no rows.
These counts validate parsing and aggregation; they do not confirm the timezone.

Source SHA-256 fingerprints:

- Turbine 1: `c4c341582fb2dd348b7187f0128cff265fe055f469413871ebb5db50eef58b5b`
- Turbine 2: `820578cd18bb557cd30c2e102f3ae5a386dfc6c489a5a15743339c2b017305e5`

## Input adapter

`wind_forecast.agent.input_data.load_scada` accepts a path or uploaded CSV bytes,
an explicit turbine ID such as `T1`, an IANA `source_timezone`, an interval label
(`start` or `end`), and a nonnegative `reporting_delay_minutes`.

```python
from wind_forecast.agent.input_data import load_scada

dataset = load_scada(
    "data/local/turbine_1.csv",
    "T1",
    source_timezone="UTC",  # Example assumption: confirm the export timezone.
    interval_label="start",
    reporting_delay_minutes=0,
)
observations = dataset.observations
source_report = dataset.report
```

The adapter maps each timestamp to the end of its ten-minute interval, then
groups six consecutive intervals into an hour ending on a UTC hour boundary.
It averages power, wind speed, and temperature without changing their units.
Only complete six-sample hours become `Observation` records. Each record has
`observed_at` equal to the hour end and `available_at` equal to that hour end
plus the configured reporting delay. Source SHA-256, configuration, coverage,
counts, dropped hours, and warnings are returned in `report`.

Duplicate timestamps, missing required columns, invalid/nonfinite numbers,
negative wind speed, and timestamps off the ten-minute grid are rejected.
Ambiguous or nonexistent local times around timezone changes are also rejected
because the CSV does not contain an offset or fold marker to resolve them.
Select a fixed-offset zone only if that matches the organizer-confirmed export
convention; do not choose one solely to bypass an ambiguity. Finite normalized
power outside `[0, 1]` is preserved and reported for source review.

Before building features or fitting a model for an issue time, the application
must restrict observations to both `observed_at <= issue_time` and
`available_at <= issue_time`, then apply its frozen or rolling observation
policy. The adapter itself preserves the complete uploaded history so the same
dataset can also support separate holdout scoring. Future actual measurements,
including measured wind, must never become forecast-time inputs.

Historical February forecasts need eligible archived forecast weather and a
model fitted before the issue time. February scores additionally need withheld
February targets supplied separately by the organizers.
