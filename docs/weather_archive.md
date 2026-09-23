# Original historical NOAA weather adapter

`wind_forecast.agent.noaa_archive.NoaaArchiveProvider` implements the shared
`WeatherProvider.fetch(ForecastRequest) -> WeatherBundle` interface. It is an
integration adapter owned by Person 3, so Person 1 can continue developing
`src/wind_forecast/weather/` independently. No API key or Brev GPU is required.

Install the optional GRIB decoder with `python -m pip install -e ".[weather]"`.
The fixture path does not import ecCodes and works offline.

```python
from datetime import datetime, timezone
from wind_forecast.agent.noaa_archive import NoaaArchiveProvider
from wind_forecast.contracts import ForecastRequest

provider = NoaaArchiveProvider(
    coordinates={
        "T1": (43.645150, 78.535604),
        "T2": (43.643198, 78.538828),
    },
    cache_dir="data/cache/noaa",
    wind_height_m=100,
    max_workers=4,
)
request = ForecastRequest(
    request_id="feb01-example",
    issue_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
    turbine_ids=("T1", "T2"),
    horizon_hours=24,
    mode="historical",
)
# Performs bounded public-network downloads; can take a few minutes on first use.
weather = provider.fetch(request)
assert len(weather.rows) == 48
```

The coordinates above come from the two map links in the case PDF. Verify the
station mapping with the team. Both turbines can fall in the same coarse GFS
grid cell; a weather grid is not a measurement at the turbine.

## Selection and numerical output

The provider selects the most recent 00Z/06Z/12Z/18Z initialization at least six
hours before `issue_time`. For a 2026-02-01 00:00Z issue, this is 2026-01-31 18Z.
Valid times are strictly issue +1 through issue +24 or +48 hours. The GFS forecast
lead therefore begins at f007 in this example.

Each hour reads the original public NOAA file at:

```text
https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF
```

The small `.idx` file determines exact byte ranges for three instantaneous fields:

| Field | Source level | Output |
|---|---|---|
| UGRD and VGRD | 100 m above ground by default; configurable to 10 m | Wind speed `hypot(u,v)` in m/s, meteorological direction in degrees |
| TMP | 2 m above ground | Temperature in °C, converted from Kelvin |

Nearest grid cell sampling uses ecCodes. The source manifest records the selected
grid latitude/longitude and distance from each configured turbine. Wind height is
returned explicitly as `WeatherPoint.wind_height_m`; no hub-height correction is
silently applied. Model developers must train/calibrate for the weather height
they use. Wind speed, temperature and direction are derived quantities from
original NOAA fields, rather than unaltered NOAA products.

## Eligibility and source evidence

Every original GRIB object is queried with HEAD. Each GRIB object and its index
must have an HTTP `Last-Modified` timestamp between the chosen initialization and
the simulated issue time, inclusive. A missing, invalid or later timestamp stops
the forecast. `available_at` is the maximum timestamp across all required GRIB
objects and indices. This treats availability at the public NOAA AWS mirror as
the conservative operational availability; it does not claim the exact first
publication time at NCEP.

Range responses must be HTTP 206 with the requested byte range, original ETag,
matching object timestamp and exact byte count. A server returning the complete
object is rejected before reading its body. Each field must be one complete
GRIB2 message. The decoder checks NCEP origin, forecast initialization, valid
time, variable, level, instantaneous step and grid. Missing or non-finite values
are rejected. Only after all checks pass does the bundle carry
`provenance_status="verified_original"` and `is_synthetic=False`.

No observations, reanalysis, synthetic data, newer weather run or today's
forecast are substituted when the archive is unavailable. The selected six-hour
cycle is conservative; if its publication was delayed, the request fails instead
of silently relaxing the as-of requirement. The app can display the reason and
let the operator retry or supply a separately verified compatible bundle.

## Local cache and audit files

The cache directory receives selected `.grib2` field payloads, the original index
text, field metadata JSON and a source manifest. Each source entry includes URL,
timestamp, ETag, byte range, SHA256 and sampled cells. The aggregate `source_hash`
is the SHA256 of the manifest's canonical `hash_input` JSON (sorted keys, compact
separators, UTF-8). Local file paths and retrieval time do not affect that identity.

On a repeated request, the provider rechecks source HEAD and index metadata and
reuses cached field bytes only when their descriptor and SHA256 agree. Corrupted
cache entries fail explicitly. Cache manifests are local audit artifacts; keep
them together with forecast outputs when transferring evidence to teammates.

Requests use at most four workers by default (configurable from one to eight), a
20-second timeout per HTTP operation and three attempts for transient transport
errors. Individual field downloads are capped at 16 MiB and indices at 2 MiB.
Only the requested 24 or 48 hours are fetched. The timeout is per request, so an
entire uncached forecast can take several minutes.

## Feasibility evidence and transfer size

A small real archive check on 2026-09-23 accessed the 2026-01-31 18Z f007 object:

| Check | Observed result |
|---|---|
| GRIB HEAD | HTTP 200; 536,265,471 bytes; Last-Modified 2026-01-31 21:34:10Z |
| Index GET | 40,472 bytes; Last-Modified 2026-01-31 21:34:34Z |
| UGRD 100 m range | HTTP 206; bytes 494,833,375–495,804,850; 971,476 bytes |
| ecCodes decoding | Source origin, run, valid time, level and 0.25° grid checks passed |
| VGRD 100 m indexed size | 963,249 bytes; body not downloaded in this check |
| TMP 2 m indexed size | 873,501 bytes; body not downloaded in this check |

These sources were available before the illustrative 2026-02-01 00Z issue time.
The check retrieved one wind-component field, not a full weather bundle or a
forecast. Whole-month availability has not been asserted. Each actual request
performs its own source validation.

The downloaded field's SHA256 was
`4c7e33e16fcce43f4e37d9e5b6b7edafb71d42cf6b516e50b0088e4fa926edfa`.
ecCodes returned a zonal 100 m wind component of -8.05953857421875 m/s at both
locations. Both sampled the same grid cell (43.75°N, 78.5°E), approximately
12.005 km from T1 and 12.280 km from T2. This is one vector component, so it is
not a complete wind-speed estimate or a turbine-power prediction. The local
decoder used Python package ecCodes 2.48.0 with native library 2.49.0.

The three required fields total about 2.8 MB for this one hour. Expect roughly
67 MB for 24 hours or 135 MB for 48 hours on first use, plus small index files;
sizes vary by lead. Both turbine locations are extracted from the same fields,
so choosing two turbines does not double downloads. There is no paid API charge,
but normal network/hosting transfer and storage limits still apply.

## Sources

- [NOAA GFS archive in the AWS Open Data Registry](https://registry.opendata.aws/noaa-gfs-bdp-pds/) identifies the public NOAA bucket and six-hour cycles.
- [NCEP GFS product inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/) documents the 0.25-degree GRIB2 product and file naming.
- [ECMWF ecCodes byte-stream decoding](https://confluence.ecmwf.int/spaces/UDOC/pages/185077310/How+do+I+decode+messages+from+a+byte+stream+-+ecCodes+FAQ) documents message handles from downloaded bytes.
- [ECMWF ecCodes Python nearest-point API](https://confluence.ecmwf.int/download/attachments/97363968/eccodes_grib_python_2018.pdf) documents `codes_grib_find_nearest`.

NOAA data are public. Attribute NOAA and avoid implying NOAA endorsement.
