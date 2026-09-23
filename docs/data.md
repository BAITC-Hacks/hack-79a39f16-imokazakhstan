# Data and weather integration

## Person 1 owns

- Mapping original columns into `Observation` without silently changing target units.
- Resolving the two supplied map links to numeric latitude/longitude and recording them in configuration.
- Handling duplicate, missing and invalid SCADA values with a documented quality flag.
- Implementing an original weather forecast provider and caching its source files and extracted point data.
- Recording model run initialization, public availability evidence, retrieval time, source URI and input hash.

## Weather archive feasibility gate

Prove one real run for both turbine coordinates and the full 48-hour horizon before bulk retrieval. Check January validation and February test dates for the same provider. A historical/reanalysis API may provide weather after the fact without preserving the exact forecast issue available at the time. Do not use it for scored replay unless organizers approve that interpretation. Record any interpolation from three-hourly weather to hourly.

`src/wind_forecast/weather/noaa_gfs.py` is intentionally a provider placeholder. Implement only after a smoke download establishes archive coverage and fields. The local mock provider is for UI and integration only.
