"""Original NOAA GFS GRIB extraction and immutable local evidence cache.

The ecCodes dependency is imported only when extracting a new GRIB message.
Cached manifests can be audited without that optional decoder installed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from urllib.request import Request, urlopen

from wind_forecast.contracts import ForecastRequest, WeatherBundle, WeatherPoint
from wind_forecast.weather.archive_probe import (
    S3_BASE_URL,
    LeadEvidence,
    fetch_index_fields,
    probe_run,
)

_FIELD_KEYS = {
    "temp_2m_k": ("2t", 2),
    "u_10m_ms": ("10u", 10),
    "v_10m_ms": ("10v", 10),
}
_DECODE_LOCK = Lock()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_cache_path(cache_dir: Path, digest: str) -> Path:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("invalid cache digest")
    return cache_dir / "messages" / digest[:2] / f"{digest}.grib2"


def _cache_bytes(cache_dir: Path, payload: bytes) -> str:
    digest = _sha256(payload)
    target = _safe_cache_path(cache_dir, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _sha256(target.read_bytes()) != digest:
            raise ValueError("cached GRIB message failed SHA-256 verification")
        return digest
    temporary = target.with_name(f".{digest}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            if _sha256(target.read_bytes()) != digest:
                raise ValueError("existing cache content differs")
        else:
            temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def _read_range(url: str, start: int, end: int, *, timeout: int = 30, evidence=None) -> bytes:
    if end < start or end - start + 1 > 4_000_000:
        raise ValueError("invalid or excessive GRIB message range")
    if evidence is None or not evidence.etag:
        raise ValueError("Version-bound GRIB availability evidence is required")
    request = Request(url, headers={"Range": f"bytes={start}-{end}", "If-Match": evidence.etag})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 206:
            raise ValueError("GRIB source did not honor byte-range request")
        from email.utils import parsedate_to_datetime
        if (response.headers.get("ETag") != evidence.etag
            or response.headers.get("Content-Range") != f"bytes {start}-{end}/{evidence.size}"
            or parsedate_to_datetime(response.headers.get("Last-Modified", "")) != evidence.last_modified):
            raise ValueError("GRIB object version or range changed since availability evidence")
        payload = response.read(4_000_001)
    if len(payload) != end - start + 1:
        raise ValueError("GRIB byte range length mismatch")
    return payload


def _decode_message(
    payload: bytes,
    field_name: str,
    valid_time: datetime,
    coordinates: dict[str, tuple[float, float]],
    run_init_time: datetime | None = None,
) -> dict[str, dict[str, float]]:
    if run_init_time is None: raise ValueError("Forecast initialization is required")
    expected_short_name, expected_height = _FIELD_KEYS[field_name]
    # The pip-provided ecCodes DLL on Windows may be built without thread safety.
    # Network transfers run concurrently, while C-library decoding is serialized.
    with _DECODE_LOCK:
        try:
            import eccodes
        except ImportError as exc:
            raise RuntimeError(
                "GFS GRIB extraction needs optional ecCodes; ask Person 3 to package it"
            ) from exc
        handle = eccodes.codes_new_from_message(payload)
        if handle is None:
            raise ValueError("ecCodes could not decode GRIB message")
        try:
            short_name = eccodes.codes_get(handle, "shortName")
            level_type = eccodes.codes_get(handle, "typeOfLevel")
            height = eccodes.codes_get(handle, "level")
            date = eccodes.codes_get(handle, "validityDate")
            time = eccodes.codes_get(handle, "validityTime")
            if (short_name, level_type, height) != (
                expected_short_name,
                "heightAboveGround",
                expected_height,
            ):
                raise ValueError(f"GRIB field metadata mismatch for {field_name}")
            if (date, time) != (
                int(valid_time.strftime("%Y%m%d")),
                int(valid_time.strftime("%H%M")),
            ):
                raise ValueError(f"GRIB validity time mismatch for {field_name}")
            expected = {"edition":2,"centre":"kwbc","discipline":0,"stepType":"instant",
                "dataDate":int(run_init_time.strftime("%Y%m%d")),"dataTime":int(run_init_time.strftime("%H%M")),
                "gridType":"regular_ll","iDirectionIncrementInDegrees":.25,"jDirectionIncrementInDegrees":.25,
                "parameterCategory":0 if field_name=='temp_2m_k' else 2,
                "parameterNumber":{"temp_2m_k":0,"u_10m_ms":2,"v_10m_ms":3}[field_name]}
            for key,value in expected.items():
                if eccodes.codes_get(handle,key)!=value:raise ValueError(f"GRIB source/grid metadata mismatch: {key}")
            missing=float(eccodes.codes_get(handle,"missingValue"))
            output: dict[str, dict[str, float]] = {}
            for turbine_id, (latitude, longitude) in coordinates.items():
                (match,) = eccodes.codes_grib_find_nearest(handle, latitude, longitude)
                value = float(match["value"])
                if not math.isfinite(value) or value == missing or (not 150 < value < 350 if field_name == "temp_2m_k" else abs(value) > 150):
                    raise ValueError(f"non-finite GRIB value for {turbine_id}")
                if any(not math.isfinite(float(match[k])) for k in ('lat','lon','distance')) or not 0<=float(match['distance'])<=50:
                    raise ValueError("Invalid nearest grid geometry")
                output[turbine_id] = {
                    "value": value,
                    "grid_latitude": float(match["lat"]),
                    "grid_longitude": float(match["lon"]),
                    "grid_distance_km": float(match["distance"]),
                }
            return output
        finally:
            eccodes.codes_release(handle)


def _extract_lead(
    evidence: LeadEvidence,
    coordinates: dict[str, tuple[float, float]],
    cache_dir: Path,
    *,
    read_range: Callable[[str, int, int], bytes] = _read_range,
) -> tuple[dict[str, object], list[WeatherPoint]]:
    index_hash, fields = fetch_index_fields(evidence)
    field_values: dict[str, dict[str, dict[str, float]]] = {}
    messages: list[dict[str, object]] = []
    for field in fields:
        run_init = evidence.valid_time - timedelta(hours=evidence.source_lead_hours)
        from wind_forecast.weather.archive_probe import gfs_run_prefix
        if evidence.grib.key != f"{gfs_run_prefix(run_init)}{evidence.source_lead_hours:03d}":
            raise ValueError("GRIB source key does not match forecast initialization")
        payload = read_range(f"{S3_BASE_URL}/{evidence.grib.key}", field.byte_start, field.byte_end,
                             **({"evidence": evidence.grib} if read_range is _read_range else {}))
        digest = _cache_bytes(cache_dir, payload)
        field_values[field.name] = _decode_message(
            payload, field.name, evidence.valid_time, coordinates, run_init
        )
        messages.append(
            {
                "field": field.name,
                "start": field.byte_start,
                "end": field.byte_end,
                "sha256": digest,
                "index_line": field.index_line,
            }
        )
    rows: list[WeatherPoint] = []
    for turbine_id in sorted(coordinates):
        temp = field_values["temp_2m_k"][turbine_id]["value"] - 273.15
        u = field_values["u_10m_ms"][turbine_id]["value"]
        v = field_values["v_10m_ms"][turbine_id]["value"]
        speed = math.hypot(u, v)
        direction = (270 - math.degrees(math.atan2(v, u))) % 360 if speed else None
        rows.append(WeatherPoint(turbine_id, evidence.valid_time, speed, temp, direction, 10.0))
    record: dict[str, object] = {
        "valid_time": evidence.valid_time.isoformat(),
        "source_lead_hours": evidence.source_lead_hours,
        "grib_key": evidence.grib.key,
        "grib_size": evidence.grib.size,
        "grib_etag": evidence.grib.etag,
        "index_etag": evidence.index.etag,
        "grib_last_modified": evidence.grib.last_modified.isoformat(),
        "index_key": evidence.index.key,
        "index_size": evidence.index.size,
        "index_last_modified": evidence.index.last_modified.isoformat(),
        "index_sha256": index_hash,
        "messages": messages,
        "grid_points": {
            turbine_id: field_values["u_10m_ms"][turbine_id] for turbine_id in sorted(coordinates)
        },
    }
    return record, rows


def _validate_coordinates(
    coordinates: dict[str, tuple[float, float]], request: ForecastRequest
) -> None:
    if set(coordinates) != set(request.turbine_ids):
        raise ValueError("coordinates must map exactly the requested turbine IDs")
    for turbine_id, (latitude, longitude) in coordinates.items():
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"invalid coordinates for {turbine_id}")


def extract_probe(
    request: ForecastRequest,
    run_init_time: datetime,
    coordinates: dict[str, tuple[float, float]],
    cache_dir: str | Path,
    *,
    max_workers: int = 6,
) -> Path:
    """Fetch and decode one complete run; write an immutable proof manifest.

    This operation never marks the result verified. Publication evidence and
    source identity must be reviewed before historical mode can consume it.
    """
    _validate_coordinates(coordinates, request)
    if max_workers < 1 or max_workers > 8:
        raise ValueError("max_workers must be between 1 and 8")
    cache = Path(cache_dir)
    probe = probe_run(request, run_init_time)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        extracted = list(
            pool.map(lambda lead: _extract_lead(lead, coordinates, cache), probe.leads)
        )
    records = [record for record, _ in extracted]
    rows = sorted(
        (row for _, chunk in extracted for row in chunk),
        key=lambda row: (row.turbine_id, row.valid_time),
    )
    expected = {
        (turbine, lead.valid_time) for turbine in request.turbine_ids for lead in probe.leads
    }
    actual = {(row.turbine_id, row.valid_time) for row in rows}
    if len(rows) != len(expected) or actual != expected:
        raise ValueError("GFS extraction did not produce exact turbine/hour coverage")
    source_hash = _sha256(_json_bytes(records))
    canonical_rows = [
        {
            "turbine_id": row.turbine_id,
            "valid_time": row.valid_time.isoformat(),
            "wind_ms": row.wind_ms,
            "temp_c": row.temp_c,
            "wind_direction_deg": row.wind_direction_deg,
            "wind_height_m": row.wind_height_m,
        }
        for row in rows
    ]
    identity = {
        "source_hash": source_hash,
        "coordinates": {turbine: list(coordinates[turbine]) for turbine in sorted(coordinates)},
        "extraction": "nearest-grid u/v 10m; temp 2m K-to-C; v1",
        "issue_time": request.issue_time.isoformat(),
        "horizon_hours": request.horizon_hours,
    }
    bundle_id = f"gfs0p25-{_sha256(_json_bytes(identity))[:24]}"
    manifest = {
        "format_version": 1,
        "bundle_id": bundle_id,
        "provider": "NOAA GFS NODD S3",
        "weather_model": "GFS pgrb2 0.25 degree",
        "run_init_time": probe.run_init_time.isoformat(),
        "issue_time": request.issue_time.isoformat(),
        "horizon_hours": request.horizon_hours,
        "turbine_ids": sorted(request.turbine_ids),
        "coordinates": identity["coordinates"],
        "retrieved_at": datetime.now(UTC).isoformat(),
        "metadata_available_at": probe.metadata_available_at.isoformat(),
        "availability_basis": "NOAA-origin public S3 object LastModified for every GRIB and .idx; original public release still requires review",
        "source_uri": S3_BASE_URL,
        "source_hash": source_hash,
        "rows_sha256": _sha256(_json_bytes(canonical_rows)),
        "extraction": identity["extraction"],
        "provenance_status": "unverified",
        "is_synthetic": False,
        "source_records": records,
        "rows": canonical_rows,
    }
    target = cache / "bundles" / f"{bundle_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(manifest)
    if target.exists():
        existing = json.loads(target.read_bytes())
        # Retrieval time is not part of the immutable bundle's numerical identity.
        existing["retrieved_at"] = manifest["retrieved_at"]
        if _json_bytes(existing) != payload:
            raise ValueError("immutable bundle identity collides with different source content")
        return target
    temporary = target.with_name(f".{bundle_id}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def attest_origin_s3_manifest(
    manifest_path: str | Path,
    request: ForecastRequest,
    coordinates: dict[str, tuple[float, float]],
) -> Path:
    """Review original NOAA S3 evidence and publish a distinct verified manifest.

    This uses NOAA's public, operational NODD bucket and each current object's
    LastModified timestamp as evidence that those exact unversioned bytes were
    already present by issue time. It does not infer availability from model
    initialization or retrieval time. The documented public-bucket history is
    an explicit assumption and belongs in the handoff.
    """
    path = Path(manifest_path)
    if path.name.endswith(".verified.json"):
        raise ValueError("input to attestation must be the original proof manifest")
    manifest = json.loads(path.read_text(encoding="ascii"))
    if (
        manifest.get("provenance_status") != "unverified"
        or manifest.get("source_uri") != S3_BASE_URL
    ):
        raise ValueError("only original unverified NOAA S3 proofs can be attested")
    load_cached_bundle(
        path, request, coordinates, verify_messages=True, allow_unverified_for_audit=True
    )
    records = manifest["source_records"]
    if len(records) != request.horizon_hours:
        raise ValueError("source record count does not match requested horizon")
    all_available: list[datetime] = []
    for hour, record in enumerate(records, start=1):
        expected_valid = request.issue_time + timedelta(hours=hour)
        if record["valid_time"] != expected_valid.isoformat():
            raise ValueError("source record valid-time sequence is incomplete")
        if (
            not record["grib_key"].startswith("gfs.")
            or record["index_key"] != f"{record['grib_key']}.idx"
        ):
            raise ValueError("source object identity is inconsistent")
        if {message["field"] for message in record["messages"]} != set(_FIELD_KEYS):
            raise ValueError("source record lacks required original fields")
        for key in ("grib_last_modified", "index_last_modified"):
            stamp = datetime.fromisoformat(record[key])
            if stamp > request.issue_time:
                raise ValueError("a required original object was modified after issue time")
            all_available.append(stamp)
    if max(all_available).isoformat() != manifest["metadata_available_at"]:
        raise ValueError("bundle availability does not match source metadata")
    verified = dict(manifest)
    verified["provenance_status"] = "verified_original"
    verified["availability_basis"] = (
        "Direct NOAA NODD public origin S3 GRIB and .idx objects; every current "
        "object LastModified at or before issue_time. Public bucket publication "
        "is inferred from the NOAA/AWS open-data registry update policy; exact "
        "per-object historical access logs are not available."
    )
    verified["availability_reference"] = "https://registry.opendata.aws/noaa-gfs-bdp-pds/"
    verified["proof_manifest_sha256"] = _sha256(path.read_bytes())
    target = path.with_name(f"{manifest['bundle_id']}.verified.json")
    payload = _json_bytes(verified)
    if target.exists():
        if target.read_bytes() != payload:
            raise ValueError("verified manifest already exists with different content")
        return target
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_cached_bundle(
    manifest_path: str | Path,
    request: ForecastRequest,
    coordinates: dict[str, tuple[float, float]],
    *,
    verify_messages: bool = True,
    allow_unverified_for_audit: bool = False,
) -> WeatherBundle:
    """Load a cached proof without network access or optional GRIB dependencies."""
    _validate_coordinates(coordinates, request)
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="ascii"))
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported GFS cache manifest version")
    if (
        manifest["issue_time"] != request.issue_time.isoformat()
        or manifest["horizon_hours"] != request.horizon_hours
    ):
        raise ValueError("cached bundle request does not match")
    if manifest["turbine_ids"] != sorted(request.turbine_ids):
        raise ValueError("cached turbine IDs do not match")
    if manifest["coordinates"] != {
        turbine: list(coordinates[turbine]) for turbine in sorted(coordinates)
    }:
        raise ValueError("cached turbine coordinates do not match")
    if _sha256(_json_bytes(manifest["source_records"])) != manifest["source_hash"]:
        raise ValueError("source manifest hash mismatch")
    if _sha256(_json_bytes(manifest["rows"])) != manifest["rows_sha256"]:
        raise ValueError("weather rows hash mismatch")
    identity = {
        "source_hash": manifest["source_hash"],
        "coordinates": manifest["coordinates"],
        "extraction": manifest["extraction"],
        "issue_time": manifest["issue_time"],
        "horizon_hours": manifest["horizon_hours"],
    }
    if manifest["bundle_id"] != f"gfs0p25-{_sha256(_json_bytes(identity))[:24]}":
        raise ValueError("cached bundle identity mismatch")
    if verify_messages:
        cache = path.parent.parent
        for record in manifest["source_records"]:
            for message in record["messages"]:
                digest = message["sha256"]
                payload = _safe_cache_path(cache, digest).read_bytes()
                if _sha256(payload) != digest:
                    raise ValueError("cached GRIB message hash mismatch")
    rows = tuple(
        WeatherPoint(
            row["turbine_id"],
            datetime.fromisoformat(row["valid_time"]),
            row["wind_ms"],
            row["temp_c"],
            row["wind_direction_deg"],
            row["wind_height_m"],
        )
        for row in manifest["rows"]
    )
    expected = {
        (turbine, request.issue_time + timedelta(hours=hour))
        for turbine in request.turbine_ids
        for hour in range(1, request.horizon_hours + 1)
    }
    if len(rows) != len(expected) or {(row.turbine_id, row.valid_time) for row in rows} != expected:
        raise ValueError("cached GFS bundle has incomplete or duplicate rows")
    available_at = datetime.fromisoformat(manifest["metadata_available_at"])
    run_init = datetime.fromisoformat(manifest["run_init_time"])
    if run_init > available_at or available_at > request.issue_time:
        raise ValueError("cached GFS run was unavailable at issue_time")
    status = manifest["provenance_status"]
    if status not in ("verified_original", "unverified") or manifest["is_synthetic"]:
        raise ValueError("invalid cached GFS provenance")
    if status == "verified_original":
        proof_path = path.with_name(f"{manifest['bundle_id']}.json")
        proof_bytes = proof_path.read_bytes()
        if _sha256(proof_bytes) != manifest.get("proof_manifest_sha256"):
            raise ValueError("verified GFS proof-manifest hash mismatch")
        proof = json.loads(proof_bytes)
        if proof.get("provenance_status") != "unverified":
            raise ValueError("verified GFS origin proof is invalid")
        for key in (
            "bundle_id",
            "source_hash",
            "rows_sha256",
            "metadata_available_at",
            "source_records",
            "rows",
            "coordinates",
        ):
            if proof.get(key) != manifest.get(key):
                raise ValueError(f"verified GFS manifest disagrees with origin proof: {key}")
        if (
            manifest.get("availability_reference")
            != "https://registry.opendata.aws/noaa-gfs-bdp-pds/"
        ):
            raise ValueError("verified GFS manifest lacks NOAA public-source reference")
    if status != "verified_original" and not allow_unverified_for_audit:
        raise ValueError("historical mode requires reviewed, verified original weather")
    return WeatherBundle(
        bundle_id=manifest["bundle_id"],
        provider=manifest["provider"],
        weather_model=manifest["weather_model"],
        run_init_time=run_init,
        available_at=available_at,
        retrieved_at=datetime.fromisoformat(manifest["retrieved_at"]),
        availability_basis=manifest["availability_basis"],
        source_uri=manifest["source_uri"],
        source_hash=manifest["source_hash"],
        provenance_status=status,
        is_synthetic=False,
        rows=rows,
    )
