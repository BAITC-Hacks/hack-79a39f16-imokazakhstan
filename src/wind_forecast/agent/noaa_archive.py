"""Original NOAA GFS archive adapter; no guessed historical availability or fallback.

Only three individual GRIB fields per forecast hour are downloaded. ecCodes is
optional until this provider is used. See docs/weather_archive.md for provenance
semantics, costs, and the independent Person 1 provider integration boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING

from wind_forecast.contracts import ForecastRequest, WeatherBundle, WeatherPoint

if TYPE_CHECKING:
    from wind_forecast.agent.noaa_updates import NoaaCycleSnapshot

BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
MAX_FIELD_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
_DECODE_LOCK = threading.Lock()


class NoaaArchiveError(ValueError):
    """The original archive cannot supply an eligible, verifiable forecast."""


@dataclass(frozen=True)
class FieldRange:
    parameter: str
    height_m: int
    start: int
    end: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def select_cycle(issue_time: datetime) -> datetime:
    """Use the latest six-hour cycle initialized at least six hours ago."""
    cutoff = issue_time - timedelta(hours=6)
    return cutoff.replace(hour=(cutoff.hour // 6) * 6, minute=0, second=0, microsecond=0)


def parse_index(
    text: str, object_size: int, run_init: datetime, forecast_hour: int, wind_height_m: int
) -> tuple[FieldRange, ...]:
    """Read exact instantaneous level matches, using the next message as range end."""
    records: list[tuple[int, list[str]]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) < 6 or not parts[1].isdigit():
            raise NoaaArchiveError("Malformed NOAA GRIB index")
        offset = int(parts[1])
        if offset < 0 or offset >= object_size or (records and offset <= records[-1][0]):
            raise NoaaArchiveError("NOAA index offsets are inconsistent with the GRIB object")
        records.append((offset, parts))
    expected = (("UGRD", wind_height_m), ("VGRD", wind_height_m), ("TMP", 2))
    result: list[FieldRange] = []
    for parameter, height in expected:
        matches = []
        for position, (start, parts) in enumerate(records):
            if parts[3:5] != [parameter, f"{height} m above ground"]:
                continue
            if parts[2] != f"d={run_init:%Y%m%d%H}" or parts[5] != f"{forecast_hour} hour fcst":
                raise NoaaArchiveError("NOAA field has an unexpected initialization or lead time")
            end = records[position + 1][0] - 1 if position + 1 < len(records) else object_size - 1
            if not 0 < end - start + 1 <= MAX_FIELD_BYTES:
                raise NoaaArchiveError("Requested NOAA field exceeds the bounded download size")
            matches.append(FieldRange(parameter, height, start, end))
        if len(matches) != 1:
            raise NoaaArchiveError(f"Expected exactly one {parameter} field at {height} m")
        result.extend(matches)
    return tuple(result)


def _http(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
    max_bytes: int = MAX_INDEX_BYTES,
) -> tuple[dict[str, str], bytes]:
    """Bounded HTTPS read. A server ignoring Range is rejected before body reading."""
    request_headers = {"User-Agent": "HackAlem-Wind-Forecast/1.0", **(headers or {})}
    if not url.startswith(BASE_URL + "/gfs."):
        raise NoaaArchiveError("Only the original NOAA GFS public archive is allowed")
    request = urllib.request.Request(url, headers=request_headers, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                expected_status = 206 if "Range" in request_headers else 200
                if response.status != expected_status or response.geturl() != url:
                    raise NoaaArchiveError("Unexpected NOAA response status or redirect")
                metadata = {key.lower(): value for key, value in response.headers.items()}
                if method == "HEAD":
                    return metadata, b""
                length = int(metadata.get("content-length", "-1"))
                if not 0 <= length <= max_bytes:
                    raise NoaaArchiveError("NOAA response length is absent or exceeds byte cap")
                payload = response.read(max_bytes + 1)
                if len(payload) != length or len(payload) > max_bytes:
                    raise NoaaArchiveError("Incomplete or oversized NOAA response")
                return metadata, payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise NoaaArchiveError(f"NOAA archive HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == 2:
                raise NoaaArchiveError(f"NOAA archive could not be reached: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
        time.sleep(0.5 * (attempt + 1))
    raise NoaaArchiveError("NOAA retries exhausted")


def _last_modified(metadata: dict[str, str], run_init: datetime, issue_time: datetime) -> datetime:
    try:
        modified = parsedate_to_datetime(metadata["last-modified"])
        if modified.tzinfo is None:
            raise ValueError("missing timezone")
        modified = modified.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise NoaaArchiveError("NOAA source lacks a trustworthy Last-Modified timestamp") from exc
    if not run_init <= modified <= issue_time:
        raise NoaaArchiveError(
            f"NOAA object Last-Modified {modified.isoformat()} is outside "
            f"the eligible interval {run_init.isoformat()} to {issue_time.isoformat()}"
        )
    return modified


def _check_grib(payload: bytes) -> None:
    if (len(payload) < 20 or payload[:4] != b"GRIB" or payload[7] != 2
            or payload[-4:] != b"7777" or int.from_bytes(payload[8:16], "big") != len(payload)):
        raise NoaaArchiveError("Range payload is not exactly one complete GRIB2 message")


def _decode_field(
    payload: bytes, field: FieldRange, run_init: datetime, valid_time: datetime,
    coordinates: dict[str, tuple[float, float]],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    try:
        import eccodes
    except ImportError as exc:
        raise NoaaArchiveError('Install the original-weather decoder: pip install -e ".[weather]"') from exc
    with _DECODE_LOCK:
        handle = eccodes.codes_new_from_message(payload)
        try:
            def get(name: str):
                return eccodes.codes_get(handle, name)
            parameter_number = {"UGRD": 2, "VGRD": 3, "TMP": 0}[field.parameter]
            expected = {
                "edition": 2, "centre": "kwbc", "discipline": 0,
                "parameterCategory": 0 if field.parameter == "TMP" else 2,
                "parameterNumber": parameter_number, "typeOfLevel": "heightAboveGround",
                "level": field.height_m, "stepType": "instant",
                "dataDate": int(run_init.strftime("%Y%m%d")), "dataTime": run_init.hour * 100,
                "validityDate": int(valid_time.strftime("%Y%m%d")),
                "validityTime": valid_time.hour * 100, "gridType": "regular_ll",
            }
            for name, value in expected.items():
                if get(name) != value:
                    raise NoaaArchiveError(f"Decoded NOAA GRIB metadata mismatch: {name}")
            if float(get("iDirectionIncrementInDegrees")) != 0.25:
                raise NoaaArchiveError("Expected the original 0.25 degree GFS grid")
            values: dict[str, float] = {}
            samples: dict[str, dict[str, float]] = {}
            missing_value = float(get("missingValue"))
            for turbine, (latitude, longitude) in coordinates.items():
                point = eccodes.codes_grib_find_nearest(
                    handle, latitude, longitude % 360, npoints=1
                )[0]
                value = float(point["value"])
                if not math.isfinite(value) or value == missing_value:
                    raise NoaaArchiveError("NOAA nearest grid cell has a missing/non-finite value")
                if field.parameter == "TMP" and not 150 < value < 350:
                    raise NoaaArchiveError("NOAA temperature is outside plausible Kelvin bounds")
                if field.parameter != "TMP" and abs(value) > 150:
                    raise NoaaArchiveError("NOAA wind component is outside plausible m/s bounds")
                values[turbine] = value
                samples[turbine] = {
                    "latitude": float(point["lat"]), "longitude": float(point["lon"]),
                    "distance_km": float(point["distance"]),
                }
            return values, samples
        except NoaaArchiveError:
            raise
        except Exception as exc:
            raise NoaaArchiveError(f"Cannot decode NOAA GRIB field: {exc}") from exc
        finally:
            eccodes.codes_release(handle)


class NoaaArchiveProvider:
    """Read GFS 0.25-degree original hourly forecasts at configured turbine locations."""

    def __init__(
        self, coordinates: dict[str, tuple[float, float]], cache_dir: Path | str,
        wind_height_m: int = 100, max_workers: int = 4, max_candidate_runs: int = 4,
    ) -> None:
        if wind_height_m not in (10, 100):
            raise ValueError("wind_height_m must be 10 or 100")
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be between 1 and 8")
        if type(max_candidate_runs) is not int or not 1 <= max_candidate_runs <= 8:
            raise ValueError("max_candidate_runs must be between 1 and 8")
        for turbine, (latitude, longitude) in coordinates.items():
            if (not turbine or not math.isfinite(latitude) or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
                raise ValueError("Each turbine requires finite latitude/longitude coordinates")
        self.coordinates = dict(coordinates)
        self.cache_dir = Path(cache_dir)
        self.wind_height_m = wind_height_m
        self.max_workers = max_workers
        self.max_candidate_runs = max_candidate_runs

    def probe(self, request: ForecastRequest) -> NoaaCycleSnapshot:
        """Metadata-only version check for the same eligible cycle used by fetch."""
        from wind_forecast.agent.noaa_updates import discover_noaa_cycle

        missing = set(request.turbine_ids) - set(self.coordinates)
        if missing:
            raise NoaaArchiveError(f"Missing turbine coordinates: {', '.join(sorted(missing))}")
        return discover_noaa_cycle(request, max_candidate_runs=self.max_candidate_runs)

    def _hour(
        self, request: ForecastRequest, run_init: datetime, valid_time: datetime,
    ) -> tuple[tuple[WeatherPoint, ...], dict[str, object], datetime]:
        forecast_hour = int((valid_time - run_init).total_seconds() // 3600)
        url = (
            f"{BASE_URL}/gfs.{run_init:%Y%m%d}/{run_init:%H}/atmos/"
            f"gfs.t{run_init:%H}z.pgrb2.0p25.f{forecast_hour:03d}"
        )
        metadata, _ = _http(url, method="HEAD")
        modified = _last_modified(metadata, run_init, request.issue_time)
        try:
            object_size = int(metadata["content-length"])
            etag = metadata["etag"]
            if not etag or not 20 <= object_size <= 2_000_000_000:
                raise ValueError("bad size or ETag")
        except (KeyError, ValueError) as exc:
            raise NoaaArchiveError("NOAA object lacks consistent length/ETag metadata") from exc
        index_metadata, index_data = _http(url + ".idx")
        index_modified = _last_modified(index_metadata, run_init, request.issue_time)
        try:
            fields = parse_index(
                index_data.decode("ascii"), object_size, run_init, forecast_hour, self.wind_height_m
            )
        except UnicodeDecodeError as exc:
            raise NoaaArchiveError("NOAA index is not ASCII text") from exc
        index_hash = _sha256(index_data)
        index_path = self.cache_dir / "indices" / f"{index_hash}.idx"
        if not index_path.exists():
            _atomic_write(index_path, index_data)
        field_values: dict[str, dict[str, float]] = {}
        source_fields = []
        coords = {turbine: self.coordinates[turbine] for turbine in request.turbine_ids}
        for field in fields:
            descriptor = {
                "url": url, "etag": etag, "last_modified": modified.isoformat(),
                "object_bytes": object_size, "parameter": field.parameter,
                "height_m": field.height_m, "byte_start": field.start, "byte_end": field.end,
            }
            cache_key = _sha256(_json_bytes(descriptor))
            payload_path = self.cache_dir / "fields" / f"{cache_key}.grib2"
            metadata_path = payload_path.with_suffix(".json")
            if payload_path.exists() and metadata_path.exists():
                try:
                    cached = json.loads(metadata_path.read_text())
                    if cached["source"] != descriptor:
                        raise ValueError("metadata mismatch")
                    payload = payload_path.read_bytes()
                    if _sha256(payload) != cached["sha256"]:
                        raise ValueError("SHA256 mismatch")
                except (KeyError, ValueError, OSError) as exc:
                    raise NoaaArchiveError(f"Corrupt NOAA cache entry: {payload_path}") from exc
            else:
                headers, payload = _http(
                    url, headers={"Range": f"bytes={field.start}-{field.end}", "If-Match": etag},
                    max_bytes=field.end - field.start + 1,
                )
                expected_range = f"bytes {field.start}-{field.end}/{object_size}"
                if headers.get("content-range") != expected_range or headers.get("etag") != etag:
                    raise NoaaArchiveError("NOAA range or ETag changed during retrieval")
                if _last_modified(headers, run_init, request.issue_time) != modified:
                    raise NoaaArchiveError("NOAA object timestamp changed during retrieval")
                _check_grib(payload)
                _atomic_write(payload_path, payload)
                _atomic_write(metadata_path, _json_bytes({
                    "source": descriptor, "sha256": _sha256(payload),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }))
            if len(payload) != field.end - field.start + 1:
                raise NoaaArchiveError("Cached NOAA field has an unexpected length")
            _check_grib(payload)
            values, samples = _decode_field(payload, field, run_init, valid_time, coords)
            if set(values) != set(coords) or any(not math.isfinite(value) for value in values.values()):
                raise NoaaArchiveError("NOAA decoder returned incomplete/non-finite turbine values")
            field_values[field.parameter] = values
            source_fields.append({
                **descriptor, "sha256": _sha256(payload), "sampled_grid_cells": samples,
                "cache_file": str(payload_path.resolve()),
            })
        rows = []
        for turbine in request.turbine_ids:
            u, v = field_values["UGRD"][turbine], field_values["VGRD"][turbine]
            rows.append(WeatherPoint(
                turbine_id=turbine, valid_time=valid_time, wind_ms=math.hypot(u, v),
                temp_c=field_values["TMP"][turbine] - 273.15,
                wind_direction_deg=(math.degrees(math.atan2(-u, -v)) + 360) % 360,
                wind_height_m=float(self.wind_height_m),
            ))
        source = {
            "forecast_hour": forecast_hour, "valid_time": valid_time.isoformat(),
            "index": {"url": url + ".idx", "last_modified": index_modified.isoformat(),
                      "sha256": index_hash, "etag": index_metadata.get("etag"),
                      "object_bytes": len(index_data),
                      "cache_file": str(index_path.resolve())},
            "fields": source_fields,
        }
        return tuple(rows), source, max(modified, index_modified)

    def fetch(self, request: ForecastRequest) -> WeatherBundle:
        if request.mode == "fixture":
            raise NoaaArchiveError("Use the offline mock provider for fixture requests")
        missing = set(request.turbine_ids) - set(self.coordinates)
        if missing:
            raise NoaaArchiveError(f"Missing turbine coordinates: {', '.join(sorted(missing))}")
        # Fail before any downloads when the optional decoder is unavailable.
        try:
            import eccodes  # noqa: F401
        except ImportError as exc:
            raise NoaaArchiveError('Install the original-weather decoder: pip install -e ".[weather]"') from exc
        snapshot = self.probe(request)
        run_init = snapshot.run_init_time
        valid_times = [
            request.issue_time + timedelta(hours=lead)
            for lead in range(1, request.horizon_hours + 1)
        ]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            results = list(pool.map(lambda valid: self._hour(request, run_init, valid), valid_times))
        rows = tuple(row for points, _, _ in results for row in points)
        available_at = max(available for _, _, available in results)
        retrieved_at = datetime.now(timezone.utc)
        # Keep absolute local paths and retrieval time outside the identity hash.
        sources = [source for _, source, _ in results]
        from wind_forecast.agent.noaa_updates import NoaaObjectVersion, source_version_signature

        actual_versions = []
        for source in sources:
            for obj in (source["fields"][0], source["index"]):
                actual_versions.append(NoaaObjectVersion(
                    key=obj["url"].removeprefix(BASE_URL + "/"),
                    last_modified=datetime.fromisoformat(obj["last_modified"]),
                    etag=obj["etag"], size=obj["object_bytes"],
                ))
        if source_version_signature(run_init, actual_versions) != snapshot.signature:
            raise NoaaArchiveError("NOAA source version changed after discovery; retry the forecast")
        identity_sources = json.loads(json.dumps(sources))
        for source in identity_sources:
            source["index"].pop("cache_file")
            for field in source["fields"]:
                field.pop("cache_file")
        identity = {
            "adapter_version": 1, "run_init_time": run_init.isoformat(),
            "coordinates": {t: self.coordinates[t] for t in request.turbine_ids},
            "wind_height_m": self.wind_height_m, "sources": identity_sources,
        }
        source_hash = _sha256(_json_bytes(identity))
        bundle_id = f"noaa-gfs-{run_init:%Y%m%dT%H}-{source_hash[:16]}"
        manifest_path = self.cache_dir / "manifests" / f"{bundle_id}.json"
        _atomic_write(manifest_path, _json_bytes({
            "schema_version": "noaa-source-manifest-v1", "bundle_id": bundle_id,
            "source_hash": source_hash, "hash_input": identity,
            "issue_time": request.issue_time.isoformat(), "retrieved_at": retrieved_at.isoformat(),
            "available_at": available_at.isoformat(), "sources": sources,
            "availability_policy": "max original S3 GRIB and index Last-Modified; must be <= issue_time",
            "cycle_selection": {**snapshot.to_dict(), "rejected_cycles": list(snapshot.rejected_cycles)},
            "spatial_method": "nearest grid cell; derived values from original NOAA GRIB2",
        }))
        return WeatherBundle(
            bundle_id=bundle_id, provider="NOAA GFS original AWS archive",
            weather_model="GFS 0.25 degree", run_init_time=run_init,
            available_at=available_at, retrieved_at=retrieved_at,
            availability_basis=(
                "Maximum original NOAA S3 Last-Modified across every GRIB and index; "
                "all <= issue_time. Original fields checked against GRIB run/valid time and NCEP centre. "
                f"Local source manifest: {manifest_path.resolve()}"
            ),
            source_uri=f"{BASE_URL}/gfs.{run_init:%Y%m%d}/{run_init:%H}/atmos/",
            source_hash=source_hash, provenance_status="verified_original", is_synthetic=False,
            rows=rows,
        )
