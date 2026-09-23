# Automatic weather sources

The application uses **NOAA GFS**, a public global numerical weather forecast,
for both current runs and historical replay. It needs internet access and the
installed ecCodes decoder, but no weather API key, paid subscription or GPU.

Useful source pages:

- [NOAA's GFS description](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gfs.php): four forecasts each day, hourly output for the first 120 forecast hours.
- [NCEP product inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/): the `gfs.tCCz.pgrb2.0p25.fFFF` GRIB2 product used by this application.
- [NOAA's public GFS archive on AWS](https://registry.opendata.aws/noaa-gfs-bdp-pds/): open access, no AWS account needed; links to the source bucket and new-object notifications.

The application's source files are at:

```text
https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF
```

`HH` is the initialization hour in UTC (00, 06, 12 or 18); `FFF` is the
forecast lead from that initialization. The companion `.idx` file identifies
byte ranges for the requested variables. The app downloads only wind components
at 100 m above ground (10 m is configurable) and temperature at 2 m. It derives
wind speed/direction and temperature in °C, samples the nearest 0.25° grid cell
for each turbine coordinate, and preserves source metadata with the result.
These derived values and the resulting power predictions are application
outputs; NOAA does not endorse the application.

## How the agent selects a forecast

`NoaaArchiveProvider.probe(request)` inspects small source-bucket listings before
any weather-field downloads. It starts with the latest initialization at or
before the requested issue hour and checks up to four cycles, newest first.
For every requested hour, both the GRIB object and its index must exist and have
valid `LastModified` timestamps between the initialization and issue time.
A partly published or late cycle is rejected; the next earlier eligible cycle
is tried. If all candidates fail, the application reports the missing input.

Publication is determined from object evidence, rather than a guessed fixed
six-hour delay. For example, a 00Z cycle published completely before a 05Z issue
can be used. For a 00Z issue, that day's 00Z cycle will normally be too late,
so a previous cycle is selected. Each request independently checks availability.

The full fetch rechecks object HEAD metadata, index identity, ETags, byte ranges,
GRIB initialization/valid times, source center and variable levels. A metadata
probe alone does not verify field contents or produce a `WeatherBundle`.
The selected version signature must match the actual fetched objects; a revision
during retrieval fails explicitly and can be retried by the monitor.
See [weather_archive.md](weather_archive.md) for decoding, cache and provenance
checks. Its source manifest now also records the discovered cycle and rejected
candidate reasons.

Historical replay uses original forecast files with source-bucket availability
at or before the simulated issue time. It does not use reanalysis or a current
forecast as a replacement for missing historical forecasts. `LastModified` is
evidence of availability in this public mirror, not a claim about the precise
first publication time at NCEP. A subsequently overwritten object whose current
timestamp is too late is rejected, even if an older version may once have existed.

## Detecting updates without repeatedly downloading weather

```python
from datetime import datetime, timezone
from wind_forecast.agent.noaa_archive import NoaaArchiveProvider
from wind_forecast.contracts import ForecastRequest

provider = NoaaArchiveProvider(
    {"T1": (43.645150, 78.535604), "T2": (43.643198, 78.538828)},
    cache_dir="data/cache/noaa",
)
request = ForecastRequest(
    request_id="weather-check",
    issue_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
    turbine_ids=("T1", "T2"), horizon_hours=24, mode="historical",
)
snapshot = provider.probe(request)   # small metadata request, no GRIB download
weather_version = snapshot.to_dict()
# The monitor compares this identity with its last successful forecast input.
# When changed, it calls the complete application workflow again.
weather = provider.fetch(request)   # validated source fields, then local cache
```

`to_dict()` returns initialization, latest source availability, source URI,
object count and a SHA-256 `signature`. The signature covers all required object
keys, ETags, sizes and timestamps. It changes for a new eligible cycle, a revised
selected object, or a shifted forecast window. Retrieval time and transient
candidate failures are excluded, so polling unchanged weather has a stable
identity. The application also monitors its measurement/model inputs separately.

A normal listing needs one request per candidate cycle, with no model-field
payloads. Reads are capped at 2 MiB per page and three pages per cycle; requests
have timeouts and bounded retries. Full source downloads retain their cache and
range-download limits. Do not poll faster than needed for four daily model cycles;
10–15 minutes is sufficient for a small demonstration service.

## Checks performed

Offline checks cover newest eligible cycle selection, partially published and
late objects/indices, complete 24/48-hour coverage, stable and changed source
versions, invalid metadata/pagination, bounded network reads, and the fixture
path's refusal to access the network. Existing archive integrity and decoding
checks remain in place.

A real metadata-only check performed during this implementation for a
**2026-02-01 00:00 UTC** issue with a 24-hour horizon found:

- Selected initialization: **2026-01-31 18:00 UTC**.
- Required objects: **48** (24 GRIB files and 24 indices).
- Latest source availability: **2026-01-31 21:54:38 UTC**.
- The 2026-02-01 00Z initialization was rejected because it was published after
  the issue time.

This check establishes the metadata discovery path for that window; forecast
accuracy must be evaluated separately using measured target power.

A live metadata check for **2026-09-23 11:00 UTC**, covering the next 48 hours,
selected that day's **06:00 UTC** initialization with all **96** required objects
available by **10:00:01 UTC**. This confirms selection can use a published cycle
less than six hours old. No weather field bodies were transferred in either
metadata check.
