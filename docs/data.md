# Data and weather integration

## Person 1 owns

- Mapping original columns into `Observation` without silently changing target units.
- Resolving the two supplied map links to numeric latitude/longitude and recording them in configuration.
- Handling duplicate, missing and invalid SCADA values with a documented quality flag.
- Implementing an original weather forecast provider and caching its source files and extracted point data.
- Recording model run initialization, public availability evidence, retrieval time, source URI and input hash.

## Weather archive feasibility gate

Prove one real run for both turbine coordinates and the full 48-hour horizon before bulk retrieval. Check January validation and February test dates for the same provider. A historical/reanalysis API may provide weather after the fact without preserving the exact forecast issue available at the time. Do not use it for scored replay unless organizers approve that interpretation. Record any interpolation from three-hourly weather to hourly.

`src/wind_forecast/weather/noaa_gfs.py` began as a placeholder. The bounded
smoke proof described below now supports a real GFS provider; the local mock
provider remains for UI and integration only.

## Person 1 implementation and evidence (2026-09-23)

The first weather feasibility gate passed for **one probe issue time only**. The
original SCADA file is still absent from this checkout, so no source-specific
observation mapping, historical model input pair, January validation run, or
February coverage claim is complete. The earlier plan below remains the guide
for expanding the work after the missing source facts arrive.

### Confirmed locations and mapping boundary

The two [brief-supplied map links](../docs/HackAlem%20AI_%20Agentic%20AI%20%D0%B4%D0%BB%D1%8F%20%D0%BF%D1%80%D0%BE%D0%B3%D0%BD%D0%BE%D0%B7%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%B2%D1%8B%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D0%BA%D0%B8%20%D0%92%D0%AD%D0%A1.pdf)
redirected to Google Maps searches containing these latitude/longitude pairs.
The exact source and resolved search coordinates are recorded in
`src/wind_forecast/weather/locations.json`.

| Brief label | Latitude | Longitude | Source link |
| --- | ---: | ---: | --- |
| turbine 1 | 43.645150 | 78.535604 | [map shortlink](https://maps.app.goo.gl/iN6svMt69D5qRpFU9) |
| turbine 2 | 43.643198 | 78.538828 | [map shortlink](https://maps.app.goo.gl/8UQMwsYavY6nLvFY8) |

`turbine_1` and `turbine_2` are location labels for the weather proof. Their
mapping to SCADA turbine IDs is not confirmed and must be configured once the
actual file is available. Both locations select NOAA GFS nearest grid point
`43.75, 78.5` for the probed run; recorded distances are approximately 12.0
and 12.3 km. This grid selection is not a turbine-coordinate replacement.

### One original GFS weather run

The bounded probe used simulated issue `2026-02-01T00:00:00Z` and original NOAA
GFS 0.25-degree run initialization `2026-01-31T18:00:00Z`. This issue time is
a probe choice, not a confirmed organizer schedule. The exact 48 source leads
`f007` through `f054` exist in the [public NOAA GFS S3 bucket](https://registry.opendata.aws/noaa-gfs-bdp-pds/).
Each required GRIB and `.idx` object has an S3 `LastModified` no later than
`2026-01-31T22:01:29Z`, before the probe issue time. The extraction downloaded
the three original GRIB messages needed at each lead: `UGRD` and `VGRD` at
10 m above ground and `TMP` at 2 m above ground. ecCodes validated the field
identity and valid time in each message. Wind speed is the magnitude of those
two components; temperature is converted from kelvin to Celsius. Wind height
remains 10 m; no hub-height adjustment was made. All 96
`(location label, valid time)` keys from issue+1 through issue+48 are present
exactly once. The [NOAA GFS field inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/)
documents the source product; actual `.idx` and GRIB metadata were checked too.

Additional metadata-only spot checks found all f007-f054 GRIB and index objects
before these other probe issue times. These are **unverified leads**, not decoded
weather bundles or a whole-period coverage ledger:

| Probe issue UTC | Selected run UTC | Latest object `LastModified` UTC |
| --- | --- | --- |
| 2023-03-01 00:00 | 2023-02-28 18:00 | 2023-02-28 21:47:15 |
| 2026-01-15 00:00 | 2026-01-14 18:00 | 2026-01-14 22:02:23 |
| 2026-02-28 00:00 | 2026-02-27 18:00 | 2026-02-27 22:04:31 |

Under the provider's explicit evidence rule, the bundle is
`verified_original`: exact original NOAA-origin S3 objects and byte ranges were
hashed, their forecast validity was decoded, their current `LastModified`
timestamps precede issue time, and the NOAA/AWS registry documents the bucket
as public and updated each cycle. **Historical per-object access logs are not
available**, so public access at the exact past minute is inferred from this
origin-bucket evidence rather than observed directly. The basis and limitation
are carried in the verified manifest. If the team requires independent
historical release logs, retain that caveat and obtain them before treating
this as sufficient for scored replay.

| Evidence | Value |
| --- | --- |
| Bundle ID | `gfs0p25-2ad228edf892aabfc6783edc` |
| Source-content SHA-256 | `0b1549d6a00d3b3a50ad291e9423d1ee1f4e227d776c7ef0745449ffb8c87c` |
| Verified cache manifest SHA-256 | `f23c41df371aec8237a686cf233f4ad6982411108e94c6a5c0e8002a8ae24d89` |
| Small handoff manifest SHA-256 | `04d92e2f6362b1da71bbaa04903d4fc25132608be38e6ad1cc3dd0c7186b931b` |
| Rows / coverage | 96/96; 48 hours x 2 location labels; no gaps or duplicates |
| Missing inputs | Real SCADA file and confirmed observation semantics |

The immutable source cache is under
`data/cache/person1-noaa-gfs/` (144 selected GRIB messages, about 128 MiB).
The small, weather-only handoff is under
`data/processed/weather-gfs0p25-2ad228edf892aabfc6783edc/` (about 174 KB):
`weather.csv`, `origin_proof.json`, `verified_archive_manifest.json`, and
`handoff_manifest.json`. The last file lists SHA-256 checksums for the other
files. Both directories are ignored by Git; share the handoff and, if an
independent original-byte check is needed, the cache separately with the team.
The small handoff is not a complete raw-source cache. Do not rename it as a
combined observation/model artifact.

### Reproduction and source preparation

These commands were run from the repository root. The package can be installed
editable, or `PYTHONPATH=src` can be set in the active shell. The bounded
metadata probe needs only Python's standard library. A new GRIB extraction
needs the optional ECMWF `eccodes` Python package and its working binary
library; it was installed into an external temporary runtime for this probe,
not added to root packaging. Ask Person 3 to add a reproducible optional
dependency if the team adopts this provider.

```powershell
$env:PYTHONPATH = 'src'
python -m wind_forecast.weather probe --issue-time 2026-02-01T00:00:00Z --run-init 2026-01-31T18:00:00Z
python -m wind_forecast.weather fetch --issue-time 2026-02-01T00:00:00Z
python -m wind_forecast.weather export --issue-time 2026-02-01T00:00:00Z
```

`fetch` defaults to cache-only and rechecks all cached message hashes without
network or ecCodes. On a fresh machine with an approved decoder installed,
append `--network` to `fetch` for a bounded original-source acquisition. The
provider tests candidate cycles newest first, at most four by default, and
accepts only a complete eligible run. Re-running `export` against the same
immutable directory raises rather than overwriting it. Transfer or remove the
previous handoff intentionally before republishing; never overwrite a changed
version under the same identity.

`python -m wind_forecast.data profile <actual-scada-path>` reports raw file
SHA-256, headers and row count without assuming timezone or target units. For
preparation, supply a reviewed JSON config with `version: 1`, source column
names, source-to-canonical turbine mapping, named timezone, `time_label`
(`instant`, `interval_start`, or `interval_end`), interval duration where
needed, target unit/definition, and either an `available_at` column or an
explicitly supported publication lag. `availability_basis` must explain that
rule. The config's `source_verified` remains false until its mapping and
normalization are evidenced; in that state, `prepare` reports quality but does
not publish a usable artifact. A verified config also needs
`verification_evidence` and `target_definition`:

```powershell
python -m wind_forecast.data prepare <actual-scada-path> --config <reviewed-config.json>
```

The canonical CSV loader signature remains unchanged. Usable complete rows
retain `quality_flag="ok"`, matching Person 2's baseline. Other stable flags
include `missing_power`, `missing_wind`, `invalid_power`, `invalid_wind`, and
temperature diagnostics; rows with an invalid timestamp or unknown turbine are
quarantined. Identical duplicate keys collapse with a count; conflicting keys
are quarantined. The quality report includes per-turbine time ranges and
missing-interval counts/examples without filling gaps. The source adapter does not clip target values, fabricate
availability, resolve unknown normalization, or borrow future measurements.
An explicit `select_as_of` helper enforces both timestamps at issue time and
requires the caller to supply the precise January freeze cutoff and inclusive
convention. Person 3 must decide where integration invokes that helper.

Owned checks completed: 9 data and 9 weather offline unit tests, NOAA original
48-lead/96-row extraction, cache-only replay, checked small handoff hashes,
and the unchanged synthetic demo (96 rows) with socket creation blocked. Tests
do not depend on NOAA access.
The remaining blockers are the actual SCADA file, confirmed timezone and
interval convention, observation availability evidence, target normalization,
source turbine-ID mapping, and the team's issue schedule. The January validation
and full February coverage ledger are pending those decisions; no performance
or scored forecast claim is made.

## Person 1 implementation plan for amoe6a

Prepared on 2026-09-23 against starter commit `b63bb09`. This section is a plan,
not a claim that real inputs or an eligible archive have already been obtained.
Implementation branch: `codex/person-1-data-weather`.

### Scope and repository findings

Read together: `AGENTS.md`, `README.md`, `docs/architecture.md`,
`src/wind_forecast/contracts.py`, and `codex_prompts/person_1.md`. The original
case brief is `docs/HackAlem AI_ Agentic AI для прогнозирования выработки ВЭС.pdf`.
The other participants' prompts were consulted only to identify boundaries.

| Area | Current state | Person 1 action |
| --- | --- | --- |
| Raw measurements | `data/raw/` contains only `.gitkeep`; no supplied SCADA file is present | Obtain the actual input path and profile its schema without modifying raw bytes |
| Observations | `data/loader.py` loads canonical CSV columns and ISO timestamps | Preserve `load_observations(path) -> list[Observation]`; add a separate source adapter, configuration and validation |
| Weather interface | `WeatherProvider.fetch(request) -> WeatherBundle` | Preserve the interface and existing dataclasses |
| Real weather | `NoaaGfsProvider.fetch` raises `NotImplementedError` | Prove a source first; then implement retrieval, extraction, auditing and cache |
| Mock weather | Deterministic and explicitly synthetic | Preserve offline behavior and synthetic labels |
| Tests and dependencies | No tests are present; core dependencies are empty | Use standard-library tests inside the owned packages; propose decoder dependencies to Person 3 |
| Downstream quality handling | The current baseline skips every observation whose `quality_flag != "ok"` | Define flags explicitly; keep usable complete records exactly `ok` |
| Runtime artifacts | `data/raw/`, `data/cache/`, `data/processed/` are ignored | Store local artifacts there, exchange them separately with checksums, and never force-add raw/cache files |

Tracked edits stay in `src/wind_forecast/data/`,
`src/wind_forecast/weather/`, and this document. Do not edit `codex_prompts/`,
shared fixtures, `contracts.py`, root packaging/configuration, README, scripts,
the app, agent, models, evaluation, or `docs/model.md`. Read-only compatibility
checks against those modules are appropriate. Generated, ignored data/cache
artifacts are local runtime outputs, not changes to shared fixtures.

Person 2 owns training, feature/model choices, chronological split selection,
metrics and model artifacts. Person 3 owns shared interfaces, dependencies,
workflow policy enforcement, scripts, UI and integration. Record narrowly scoped
requests for them below; do not implement their work or change their plans.

### Facts, unresolved inputs and decisions

The brief states that measurements run from March 2023 through January 31, 2026,
inclusive. The test period is February 1-28, 2026, with rolling 24-48 hour
forecasts beginning on January 31. January validation is suggested by the
repository, but Person 2 must supply the actual validation issue times.

The brief names statistical time, average wind speed in m/s, normalized active
power on the line side, and average ambient temperature in degrees Celsius.
These are descriptions, not verified file headers or confirmed normalization.

| Needed input | Required evidence / decision | Behavior while unresolved |
| --- | --- | --- |
| Real SCADA file | Local path, format, encoding, original headers, turbine-ID mapping, file SHA-256 | Build parser/report infrastructure using clearly labeled temporary test data; do not claim a real mapping or real-data artifact |
| Time convention | Source timezone, interval start/end/instant labeling, interval length, timestamp precision | Do not silently apply `.env.example`'s UTC value or infer semantics from a column name |
| Observation availability | Original publication/ingestion timestamp, or a documented and accepted conservative delay policy | Keep records in an unverified staging/report form; do not invent `available_at = observed_at` |
| Target semantics | Normalization definition, valid range, turbine/farm mapping, capacity only if supplied | Preserve values and source units; no clipping to [0,1], conversion to MW/MWh, or aggregation |
| Turbine 1 location | Resolve the target pin in [the supplied map link](https://maps.app.goo.gl/iN6svMt69D5qRpFU9), retain final URL and evidence | No guessed coordinates or use of map viewport center |
| Turbine 2 location | Resolve the target pin in [the supplied map link](https://maps.app.goo.gl/8UQMwsYavY6nLvFY8), retain final URL and evidence | No guessed coordinates; keep identity distinct even if both turbines use one weather grid cell |
| Issue schedule | Explicit aware timestamps agreed with the team; contracts currently require UTC-hour alignment | Use a clearly labeled probe time only; do not present the fixture's issue time as the organizer's schedule |
| February observation policy | Whether any February SCADA is available at simulated issue times | Keep the repository's frozen-January default; do not ingest future observations |
| Weather archive | Exact original run files, fields/levels, spatial/temporal coverage and historical release evidence | Remain `unverified`; no historical bundle if the proof fails |

Keep these decisions in a small versioned JSON configuration owned by the
data/weather packages or in an ignored runtime input config, with status,
evidence and interpretation. Validate configurations explicitly. Use named
historical timezone rules when appropriate; never use one current UTC offset
for an entire multi-year dataset without evidence. If timezone data is missing
on Windows, report the environment dependency to Person 3 rather than silently
falling back to UTC. Do not log credentials or credential-bearing URLs.

### Ordered implementation milestones

#### 1. Establish the input and coordinate contract

First inspect the real file if supplied; list original columns, row counts,
per-turbine time ranges, cadence, null tokens, duplicated timestamps, numeric
representations and units. Hash raw bytes before processing and preserve them.
Resolve both map pins and retain numeric latitude/longitude, coordinate order,
source URL, precision and evidence. Validate latitude/longitude bounds and
explicitly map source turbine IDs to the configured coordinates.

Write the confirmed mapping and unresolved assumptions in this document. Do not
build format-specific XLSX or delimiter support speculatively before seeing the
file. If a format needs another dependency, supply Person 3 with a minimal
justification and a dependency-free intermediate CSV option where practical.

Exit criterion: a reproducible profile and configuration with no guessed
semantics. Missing inputs block certification, not independent validation and
cache infrastructure work.

#### 2. Prove one archived forecast before implementing the real provider

Perform this gate early, alongside source profiling, because archive access or
publication evidence can determine feasibility. Start with the existing NOAA
GFS candidate; its filename in the repo does not establish archive suitability.
Consult current primary provider documentation and make a bounded smoke download.

1. Select an explicit historical issue time relevant to January 31 / February
   replay, both confirmed coordinates, and the 48-hour horizon.
2. Identify one original model run and all files needed to produce
   `issue_time + 1h` through `issue_time + 48h`. Source forecast lead is relative
   to `run_init_time`, not `issue_time`: a run six hours old may need lead 54.
3. Inspect actual fields, units, wind reference height, grid, forecast lead
   spacing and whether values are instantaneous or interval averages. Prefer
   native hourly fields if available; do not treat forecast accumulations as
   instantaneous values.
4. Establish original public availability for every required file using
   documented evidence. A run initialization time, today's download time, an
   archive directory, or a later mirror's modification time is insufficient.
   A release-lag bound requires evidence and explicit acceptance; it is not
   automatically proof. Record the exact policy and evidence for any bound.
5. Set bundle availability to the latest availability of all required inputs
   and require `run_init_time <= available_at <= issue_time`. Retrieval today
   may be later than the simulated issue time. It must not replace historical
   availability evidence.
6. Extract both points and check the exact 96 unique `(turbine_id, valid_time)`
   keys. Confirm finite required wind values, no gaps, no duplicates and no
   unrequested rows. Preserve wind height and all extraction transformations.
7. Save a small immutable proof artifact with source files or documented byte
   ranges, SHA-256 hashes, evidence, extraction settings and a coverage report.
   Check archive listings/sample files at the January and February boundaries
   before scheduling bulk retrieval. Do not claim whole-month coverage from one
   successful date.

If this fails, record the attempted source/date, precise failure, missing
evidence, scope of coverage and next candidate or organizer decision required.
Do not substitute reanalysis, observed weather, a current forecast or synthetic
values. Archive presence without release evidence remains `unverified`. Do not
begin bulk acquisition or claim a historical-ready provider until the gate
passes. Early generic validators and injected test transports are still useful.

Primary-source leads checked during planning (documentation only; the smoke gate
has not passed):

| Lead | What to verify next |
| --- | --- |
| [NOAA GFS on AWS](https://registry.opendata.aws/noaa-gfs-bdp-pds/) | The public bucket is a candidate, not proof of retention. Probe exact January/February 2026 keys and required leads; probe March 2023 separately if Person 2 needs that training archive. |
| [NOAA/NCEI GFS access](https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast) | Retention/access descriptions do not establish the required files. The documented 0.5-degree alternative has three-hourly leads and needs explicit temporal interpolation and access checks. |
| [NCAR historical GFS archive](https://gdex.ucar.edu/datasets/d084001/) | An alternative operational forecast archive with three-hourly leads. Check actual 2026 files and update notices rather than relying on a headline date range. |
| [NOAA GFS product inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/) and [sample forecast fields](https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p25.f003.shtml) | Inspect the chosen GRIB2 files for matching U/V components, height/level, temperature units and native lead spacing. Evaluate the smallest decoder requirement with Person 3. |

No archive object coverage or historical publication time was verified during
planning. Neither map shortlink yielded verified numeric coordinates. Keep both
items explicitly unresolved; the implementation must obtain evidence before
certifying a real bundle. These documentation leads do not justify bulk retrieval.

#### 3. Implement deterministic SCADA preparation

Preserve the existing canonical CSV entry point. Add source-specific parsing
separately and use typed configuration for column mapping, turbine IDs, time
semantics, missing tokens, units and availability policy. Sensible module
boundaries are `data/config.py`, `data/source_adapter.py`,
`data/validation.py`, and a thin `data/__main__.py` for Person 1 commands;
combine modules if that keeps the implementation smaller.

- Parse timestamps explicitly and normalize to aware UTC at contract boundaries.
  Reject ambiguous/nonexistent local times unless an explicit policy resolves
  them. For interval-start measurements, availability cannot precede the end of
  the measurement interval. Retain interval semantics in artifact metadata.
- Distinguish blank/missing, malformed and non-finite numbers. Do not pass
  `NaN`/infinity downstream. Negative wind speed is invalid; power constraints
  must come from confirmed target semantics. Do not silently discard genuine
  zero power or classify all low power as equipment failure.
- Retain original target values and units in the audit record. Convert only
  explicitly configured physical units and record the conversion; do not
  renormalize targets or infer capacity.
- Deduplicate on turbine and canonical time with a documented deterministic
  policy. Collapse byte/semantic-equivalent duplicates only with an audit count;
  conflicting duplicates fail or go to quarantine by default, never arbitrary
  last-row-wins. Retain source-row references.
- Separate structural errors from row quality issues. Use a documented stable
  vocabulary such as `missing_power`, `missing_wind`, `invalid_wind`, and
  `duplicate_conflict`; define how multiple reasons are represented. `ok` means
  eligible under the documented policy. Record missing optional temperature in
  the audit without excluding otherwise usable wind/power pairs unless required
  by the agreed policy. Communicate all flags to Person 2.
- Report counts by turbine and reason, time ranges, missing intervals, cadence,
  duplicate/conflict counts, and retained/quarantined counts. Reconcile totals.
  Do not impute targets, fill observations from future records or bridge gaps
  without a documented causal policy.
- Provide an explicitly invoked eligibility helper that enforces both
  `observed_at <= issue_time` and `available_at <= issue_time`, requested turbine
  IDs, and the configured observation freeze. Leave the canonical loader's
  signature unchanged. Report exclusions rather than hiding them.
- Represent the January freeze as a precise timestamp/interval rule once source
  semantics are confirmed. Do not accidentally drop all of January 31 by using
  its midnight as an inclusive end, or admit later measurements. Even within
  January, a row cannot precede its availability at the simulated issue time.

Exit criterion: repeated preparation of the same source and configuration gives
the same sorted canonical content and audit counts; raw bytes remain unchanged;
every emitted historical-eligible observation has defensible time semantics.
Until target semantics are confirmed, a staging export is not training-ready.

#### 4. Implement the verified provider and immutable cache

Only after milestone 2 succeeds, implement `NoaaGfsProvider.fetch(request)`
with injected configuration and separable transport, decoding and validation.
Possible helper modules are `weather/config.py`, `weather/provenance.py`,
`weather/cache.py`, and `weather/__main__.py`. Keep `base.py` and the mock API
compatible. Decoder imports must be lazy so importing the mock or running the
demo does not require a GRIB stack, network access, or API keys.

- Select the newest complete, eligible run deterministically using documented
  availability, not merely the nearest run initialization. Bound the number of
  candidate runs, requests, retries, bytes and timeouts. An older eligible run
  is acceptable if the policy allows it and still covers the whole horizon;
  record the selection. Never fall back to mocks.
- Use explicit turbine-coordinate lookup. Record requested and selected grid
  coordinates, spatial selection/interpolation method, distance, resolution,
  variables/levels and extraction version. Two turbine IDs can share a grid
  value without losing their identity.
- Preserve the source wind height. Do not invent hub-height scaling. If wind
  components are supplied, document vector magnitude/direction conventions.
  Prefer no temporal interpolation. If needed, interpolate only within the same
  eligible run, record source endpoints and method, avoid extrapolation, and
  interpolate components or handle circular directions correctly. An endpoint
  beyond a target hour is allowed only because it was a forecast available by
  issue time, not a later observation. Include its file in availability checks.
- Validate all fields used by the consumer, missing-value sentinels, units,
  finite values and exact requested key coverage before returning a bundle.
  Require 24 or 48 hours for each requested turbine. Unknown IDs, corrupt data,
  missing wind, conflicting provenance or insufficient coverage fail clearly.
- Keep original source bytes/ranges and extracted canonical data with a
  versioned sidecar manifest. Record per-file identities and SHA-256 hashes;
  define how their canonical manifest produces `source_hash`. A signed URL or
  credential is never a manifest field. Do not treat an S3 ETag as SHA-256.
- Derive stable content IDs from source hashes and transformation/configuration
  versions. Keep retrieval timestamps outside the stable numerical content
  identity. Include coordinates, field/height choices and interpolation in cache
  keys so incompatible extractions cannot collide.
- Verify hashes on cache reads; use temporary writes and atomic publication;
  never overwrite different content under an existing immutable identity.
  Cache-only mode must fail clearly when missing and must never invoke a
  network transport. Record acquisition events separately from stable artifacts.
- Keep the richer metadata in sidecars without extending shared dataclasses.
  Preserve all required `WeatherBundle` provenance fields and distinguish
  `verified_original`, `unverified`, and `synthetic` truthfully. Historical
  requests reject the latter two and any inputs unavailable at issue time.

Exit criterion: the proof artifact can be replayed from a verified cache without
network access, reproducing the same canonical rows and content hashes; failure
cases cannot produce a success bundle or silently relax historical eligibility.

#### 5. Deliver one usable input bundle, then expand coverage

Publish a small versioned handoff under an ignored, content-addressed directory
such as `data/processed/<input-id>/`. Suggested contents:

- `observations.csv`: canonical rows and documented target units;
- `weather.csv`: canonical weather points for a specific issue time and horizon;
- `manifest.json`: schema/config versions, code revision, source identities and
  hashes, transformations, coordinates, units/height, UTC/time conventions,
  availability policy/evidence, issue/horizon, and provenance status;
- `quality_report.json`: reconciled row counts, exclusions, gaps and conflicts;
- `coverage.csv`: expected/actual coverage per issue and turbine, with explicit
  missing keys and reasons;
- `checksums.json`: SHA-256 of exported payload files, excluding itself.

Specify serialization, stable row order, null representation and hash rules.
Avoid self-referential hashes. Provide load/replay commands and an agreed
artifact-transfer location; the Git commit alone does not transfer ignored data.
Do not put predictions, model metrics or synthetic real-looking result files in
this handoff. Clearly identify any unverified staging artifact as ineligible.

Only then expand retrieval to the validation issue list supplied by Person 2
and the confirmed February replay schedule. Preserve each issue time even when
valid times overlap. Weather may be needed beyond February 28 to complete a
late-February 48-hour horizon; output truncation/scoring is for Person 3 and
Person 2 to decide. March 2023-January 2026 weather feature retrieval is a
separate, potentially expensive need to agree with Person 2; it is not necessary
to start their existing SCADA wind-to-power baseline. Report availability gaps
before promising that longer archive.

Exit criterion: Persons 2 and 3 can load a small verified input artifact with
exact commands and hashes; the expanded coverage ledger makes every missing or
ineligible issue explicit. An incomplete ledger is not full-period completion.

### Quality checks and acceptance matrix

Place tests under `src/wind_forecast/data/tests/` and
`src/wind_forecast/weather/tests/`. Use standard-library `unittest`, temporary
directories and injected transports/decoders. Temporary synthetic test inputs
must be labeled as such and never published as real datasets. Do not edit
`demo_data.py` or shared fixtures. Test guards and invariants rather than merely
mirroring implementation details.

| Area | Required checks |
| --- | --- |
| Loader compatibility | Existing canonical CSV call, UTF-8 BOM, blank optional values, aware offsets converted to UTC, missing headers and useful source-row errors |
| Time and availability | Naive times require configuration; configured historical offsets; ambiguous/invalid local times; interval end/latency; equality at issue allowed; either timestamp later than issue excluded; precise January freeze boundary |
| Data quality | NaN/infinity, malformed values, negative wind, unknown turbine, genuine zero power, target preservation, identical/conflicting duplicates, stable order, reconciled quality totals and correct `ok` meaning |
| Weather provenance | Run initialized before issue but released after issue rejected; unverified/synthetic historical input rejected; latest required file controls availability; a downloaded-today original run can still qualify with proper original evidence |
| Coverage and extraction | 24/48 hours, both turbines, exact keys, duplicate/missing/extra rows, source leads beyond run+48, missing fields, documented units/height, spatial lookup and interpolation boundaries when implemented |
| Cache and transport | Hash tampering, partial downloads/writes, cache-key configuration changes, repeated-content stability, bounded retries, clear missing-decoder errors, cache-only execution with a transport that raises on any call |
| Offline compatibility | Existing mock imports and demo run without keys, network or optional decoder packages; real-provider failures do not invoke mocks |

Keep archive smoke checks separate and opt-in: ordinary tests must not download
weather or depend on remote service state. A network mock cannot establish real
archive provenance; retain the smoke evidence as a separate acceptance gate.

These are intended verification commands after the test modules exist, using a
Python 3.11+ environment with this repository installed (or `PYTHONPATH=src`):

```powershell
python -m unittest discover -s src/wind_forecast/data/tests -p 'test_*.py' -v
python -m unittest discover -s src/wind_forecast/weather/tests -p 'test_*.py' -v
python scripts/run_demo.py --output runs/person1-offline-check
git diff --check
git diff --name-only
```

The demo command alone does not prove network isolation: run the compatibility
test with network access blocked and optional decoding imports unavailable.
Confirm 96 synthetic forecast rows and explicit synthetic status/warnings.
Do not present these test results as forecast accuracy. Document actual commands
and outcomes when implementation runs; none of these future tests has passed
merely because it is listed here.

### Coordination requests and handoff requirements

Record these as proposals; this plan does not assign work to the other people:

- For Person 3: the smallest decoder/timezone dependency set demonstrated by the
  smoke probe; any contract metadata gap that sidecars cannot solve; how the
  existing workflow should invoke Person 1's observation cutoff helper; the
  exact frozen-January policy; and a request to propagate real input hashes into
  final artifacts (the current baseline has a placeholder hash). Person 1 must
  not patch those integration/model files.
- For Person 2: verified target units and mapping, usable/excluded quality flags,
  wind source height, known weather/SCADA measurement differences, artifact
  availability, and a request for their issue-time/coverage needs. Do not choose
  their model, train/validation split, imputation features or scoring metric.
- For the user/team: actual SCADA location, confirmed time/normalization facts,
  resolved pin evidence if automated resolution fails, issue schedule and a
  transfer location for ignored artifacts. Ask once for missing essentials and
  continue independent work; do not repeatedly request an already supplied fact.

Use small commits on the assigned branch: input configuration/validation;
archive feasibility evidence; provider/cache after the gate; and verified
handoff/coverage. Before each commit, inspect the diff and stage only owned
paths. Include commit hash, exact environment and commands, artifact paths and
hashes, source URLs and availability evidence, coverage/missing rows, unresolved
assumptions and an explicit readiness status in the handoff. Preserve existing
user changes. Do not push, publish, or message teammates unless instructed.

### Ready-to-use Sol implementation prompt

Copy the following prompt into Sol in this repository. It requests implementation,
while this planning change itself changes documentation only.

```text
You are implementing Person 1's data and archived-weather track for amoe6a
(GitHub account). Work in this repository on codex/person-1-data-weather.

Read AGENTS.md, README.md, docs/architecture.md, src/wind_forecast/contracts.py,
codex_prompts/person_1.md, and all of docs/data.md before editing. The section
"Person 1 implementation plan for amoe6a" is your implementation plan; follow
its evidence gates, acceptance matrix, and coordination boundaries. Recheck
the current checkout because its state may have advanced since the plan.

Your tracked ownership is ONLY:
- src/wind_forecast/data/
- src/wind_forecast/weather/
- docs/data.md

Do not edit shared contracts/fixtures, codex_prompts, root dependencies/config,
README, app, scripts, agent, model/evaluation code, or Person 2/3 plans. You may
read other modules and run the existing demo as compatibility checks. Generated
ignored runtime artifacts may use data/raw, data/cache and data/processed; keep
raw inputs read-only and never force-add those artifacts to Git. Communicate
shared changes as proposals in docs/data.md, not edits to others' modules.

Start with git status and branch inspection. Preserve uncommitted user work.
Use the assigned branch, creating it only if absent and safe; do not reset or
silently replace an existing branch. Do not change Git identity just because
the task names a GitHub account. Commit focused, reviewed changes only within
your ownership. Do not push or contact teammates without user instruction.

The starter inspection found no real SCADA file, only a canonical CSV loader,
a NotImplementedError NOAA provider, and no tests. Recheck these facts. The
brief covers March 2023-Jan 31 2026 measurements and Feb 1-28 2026 testing.
Both original map URLs and the missing decisions are recorded in docs/data.md.
Do not infer actual coordinates, timezone, interval semantics, reporting lag,
target normalization, capacity, issue schedule or February observation access
from synthetic fixtures or .env.example.

First actions:
1. Locate supplied inputs without reading/printing secrets. Profile and hash raw
   files without changing them. Resolve the two map target pins with evidence.
   Record confirmed mappings and unresolved decisions. Ask for essential missing
   facts once, while continuing independent parser, audit and test work.
2. Investigate current official archive documentation and run a bounded smoke
   probe for one relevant issue time, both coordinates and a full 48h horizon.
   Inspect actual fields/levels and coverage. Prove original public availability
   for all required source files; archive presence or run time alone is not proof.
   Report failures promptly. Do not implement the real network provider or bulk
   download before this gate passes. Build generic offline helpers meanwhile.
3. Implement the source-specific SCADA adapter/configuration and deterministic
   validation beside the compatible canonical loader. Keep target values/units,
   explicit UTC/interval/availability policies, source-row diagnostics, stable
   duplicate policy, non-finite guards and reconciled quality reports. Preserve
   quality_flag="ok" for usable rows: Person 2's current baseline excludes every
   other flag. Provide an explicit issue-time/frozen-January selection helper;
   do not retrofit Person 3's workflow yourself.
4. Once the archive proof passes, implement the provider behind the existing
   fetch(ForecastRequest) -> WeatherBundle interface, with lazy optional imports,
   injected transport/decoder, bounded retrieval, deterministic eligible-run
   selection, exact requested keys, documented extraction and immutable cache.
   Request dependency changes from Person 3; do not modify root packaging.
5. Export one small verified, versioned observation/weather input handoff with
   hashes, manifests, quality/coverage reports, source/availability evidence and
   exact replay commands. Confirm cache-only replay. Only then expand dates
   according to the agreed validation and February issue lists. Do not train
   models, pick validation splits, calculate forecast metrics or integrate UI.

Non-negotiable correctness rules:
- Preserve load_observations(path) -> list[Observation], WeatherProvider.fetch,
  existing contracts and the mock's offline behavior.
- Enforce both observation timestamps <= simulated issue time and the precise
  configured freeze. Never invent observation availability. Unresolved inputs
  stay in staging and cannot be labeled historical/training-ready.
- Require weather run_init_time <= available_at <= issue_time; bundle availability
  is the maximum across all required files. Retrieval today is separate. Record
  release evidence/bounds honestly; missing proof stays unverified.
- Historical mode rejects synthetic or unverified weather. Never substitute
  today's forecast, observed weather, reanalysis or mocks to make a replay pass.
- Cover issue+1 through issue+24/48 for every requested turbine exactly once.
  Include required source leads beyond run+48 when the selected run is older.
- Preserve source wind height/units. Record grid extraction and any interpolation;
  no invented hub-height adjustment, extrapolation or mixing of later runs.
- Validate finite required values. Do not clamp/renormalize unknown targets,
  assume [0,1], infer MW/MWh or combine turbines without evidence.
- Hash original bytes and canonical artifacts; verify on cache read, publish
  atomically, keep content IDs independent of retrieval timestamps, and never
  overwrite differing content under one immutable identity. Cache-only means
  no network calls. Keep credentials in local environment only; redact sensitive
  URLs and never print/store keys in errors, manifests or traces.

Write meaningful standard-library unittest tests in the owned data/tests and
weather/tests packages. Use temporary, explicitly synthetic test inputs and
injected transports. Follow the acceptance matrix in docs/data.md, including
cutoff boundaries, unit preservation, duplicate conflicts, late publication,
96-key coverage, corrupt cache and network-disabled replay. Keep real archive
smoke checks opt-in and distinguish their evidence from unit-test results.
Run the owned tests, existing synthetic demo compatibility check and git diff
--check. No model performance claims from fixture outputs. Record actual
commands/results, limitations and dependency requirements in docs/data.md.

If a missing real file, unresolved semantic decision, archive permission, decoder
dependency or unavailable publication evidence blocks certification, finish the
independent owned work and tests. Clearly state the precise blocker and next
required input; do not fabricate success or weaken guards. End with an honest
completed/blocked list, commit hash, owned files changed, test evidence, artifact
paths/hashes when present, coverage/missing rows, and proposed Person 3 decisions.
Begin the work now; do not merely return another plan.
```
