"""Bounded, read-only NOAA GFS archive feasibility probes.

S3 object modification timestamps are evidence about this bucket, not a complete
proof of when an unversioned forecast first became publicly available. Probe
results are deliberately never labeled verified_original.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from urllib.parse import urlencode
from urllib.request import urlopen

from wind_forecast.contracts import ForecastRequest, require_utc

S3_BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
_S3_NAMESPACE = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_INDEX_LINE = re.compile(r"^(\d+):(\d+):(.+)$")


@dataclass(frozen=True)
class S3Object:
    key: str
    last_modified: datetime
    size: int
    etag: str = ""


@dataclass(frozen=True)
class IndexField:
    name: str
    byte_start: int
    byte_end: int
    index_line: str


@dataclass(frozen=True)
class LeadEvidence:
    valid_time: datetime
    source_lead_hours: int
    grib: S3Object
    index: S3Object


@dataclass(frozen=True)
class ArchiveProbe:
    run_init_time: datetime
    issue_time: datetime
    leads: tuple[LeadEvidence, ...]
    metadata_available_at: datetime
    provenance_status: str = "unverified"


def gfs_run_prefix(run_init_time: datetime) -> str:
    run = require_utc(run_init_time, "run_init_time")
    if any((run.minute, run.second, run.microsecond)) or run.hour not in (0, 6, 12, 18):
        raise ValueError("GFS run must initialize at 00, 06, 12 or 18 UTC")
    return f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f"


def _get_bytes(url: str, timeout: int = 20) -> bytes:
    with urlopen(url, timeout=timeout) as response:
        return response.read()


def list_run_objects(
    run_init_time: datetime,
    *,
    get_bytes: Callable[[str], bytes] = _get_bytes,
    base_url: str = S3_BASE_URL,
) -> dict[str, S3Object]:
    """List only pgrb2 0.25-degree objects for one cycle; refuse excess pages."""
    prefix = gfs_run_prefix(run_init_time)
    objects: dict[str, S3Object] = {}
    token: str | None = None
    for _ in range(5):
        query: dict[str, str] = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            query["continuation-token"] = token
        document = ET.fromstring(get_bytes(f"{base_url}/?{urlencode(query)}"))
        if document.findtext(f"{_S3_NAMESPACE}Name") != "noaa-gfs-bdp-pds":
            raise ValueError("unexpected archive listing response")
        for entry in document.findall(f"{_S3_NAMESPACE}Contents"):
            key = entry.findtext(f"{_S3_NAMESPACE}Key")
            modified = entry.findtext(f"{_S3_NAMESPACE}LastModified")
            size = entry.findtext(f"{_S3_NAMESPACE}Size")
            if not key or not key.startswith(prefix) or modified is None or size is None:
                raise ValueError("malformed GFS listing entry")
            if key in objects:
                raise ValueError(f"duplicate archive key {key}")
            objects[key] = S3Object(
                key,
                require_utc(datetime.fromisoformat(modified), "LastModified"),
                int(size),
                entry.findtext(f"{_S3_NAMESPACE}ETag") or "",
            )
        token = document.findtext(f"{_S3_NAMESPACE}NextContinuationToken")
        if not token:
            return objects
    raise ValueError("archive listing exceeded five pages")


def probe_run(
    request: ForecastRequest,
    run_init_time: datetime,
    *,
    get_bytes: Callable[[str], bytes] = _get_bytes,
    base_url: str = S3_BASE_URL,
) -> ArchiveProbe:
    """Check exact file/index presence and S3 timestamps for every requested hour."""
    run = require_utc(run_init_time, "run_init_time")
    if run > request.issue_time:
        raise ValueError("run initialized after issue_time")
    prefix = gfs_run_prefix(run)
    objects = list_run_objects(run, get_bytes=get_bytes, base_url=base_url)
    leads: list[LeadEvidence] = []
    for hour in range(1, request.horizon_hours + 1):
        valid = request.issue_time + timedelta(hours=hour)
        source_lead = (valid - run).total_seconds() / 3600
        if not source_lead.is_integer() or source_lead < 0:
            raise ValueError("invalid GFS source lead")
        stem = f"{prefix}{int(source_lead):03d}"
        if stem not in objects or f"{stem}.idx" not in objects:
            raise ValueError(f"missing GFS forecast or index for {valid.isoformat()} ({stem})")
        grib, index = objects[stem], objects[f"{stem}.idx"]
        if grib.size <= 0 or index.size <= 0:
            raise ValueError(f"empty GFS forecast or index for {valid.isoformat()}")
        leads.append(LeadEvidence(valid, int(source_lead), grib, index))
    available_at = max(max(lead.grib.last_modified, lead.index.last_modified) for lead in leads)
    if available_at > request.issue_time:
        raise ValueError(
            f"required GFS object last modified after issue_time: {available_at.isoformat()}"
        )
    return ArchiveProbe(run, request.issue_time, tuple(leads), available_at)


def parse_required_index(index_bytes: bytes, *, grib_size: int) -> tuple[IndexField, ...]:
    """Locate the three exact GRIB2 messages needed for 10-m wind and 2-m temp."""
    text = index_bytes.decode("ascii")
    entries: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _INDEX_LINE.match(line)
        if not match:
            raise ValueError("malformed GFS index line")
        entries.append((int(match.group(2)), line))
    if not entries or entries[0][0] != 0 or any(b <= a for (a, _), (b, _) in pairwise(entries)):
        raise ValueError("invalid GFS index byte offsets")
    targets = {
        "TMP:2 m above ground:": "temp_2m_k",
        "UGRD:10 m above ground:": "u_10m_ms",
        "VGRD:10 m above ground:": "v_10m_ms",
    }
    found: dict[str, IndexField] = {}
    for i, (offset, line) in enumerate(entries):
        for marker, name in targets.items():
            if f":{marker}" in line:
                if name in found:
                    raise ValueError(f"duplicate required GFS field {name}")
                end = entries[i + 1][0] - 1 if i + 1 < len(entries) else grib_size - 1
                if end < offset or end >= grib_size:
                    raise ValueError("invalid GFS message byte range")
                found[name] = IndexField(name, offset, end, line)
    if set(found) != set(targets.values()):
        raise ValueError(
            f"missing required GFS fields: {sorted(set(targets.values()) - set(found))}"
        )
    return tuple(found[name] for name in ("temp_2m_k", "u_10m_ms", "v_10m_ms"))


def fetch_index_fields(
    evidence: LeadEvidence,
    *,
    get_bytes: Callable[[str], bytes] = _get_bytes,
    base_url: str = S3_BASE_URL,
) -> tuple[str, tuple[IndexField, ...]]:
    """Return SHA-256 and selected ranges for one index after size validation."""
    if evidence.index.size > 100_000:
        raise ValueError("GFS index exceeds bounded probe size")
    if get_bytes is _get_bytes:
        from urllib.request import Request
        from email.utils import parsedate_to_datetime
        if not evidence.index.etag: raise ValueError("Index version ETag is missing")
        request = Request(f"{base_url}/{evidence.index.key}", headers={"If-Match": evidence.index.etag})
        with urlopen(request, timeout=20) as response:
            if response.headers.get("ETag") != evidence.index.etag or parsedate_to_datetime(response.headers.get("Last-Modified", "")) != evidence.index.last_modified:
                raise ValueError("Index version changed since availability probe")
            payload = response.read(100_001)
    else:
        payload = get_bytes(f"{base_url}/{evidence.index.key}")
    if len(payload) != evidence.index.size:
        raise ValueError("GFS index size changed since listing")
    return hashlib.sha256(payload).hexdigest(), parse_required_index(
        payload, grib_size=evidence.grib.size
    )
