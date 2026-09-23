"""Discover eligible NOAA forecast versions using small public metadata listings.

A listing is a change detector, not a verified weather bundle. The archive
provider still checks source HEAD metadata, indices, field bytes and GRIB origin
when it downloads the selected version.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode

from wind_forecast.agent.noaa_archive import BASE_URL, NoaaArchiveError
from wind_forecast.contracts import ForecastRequest, require_utc

_NAMESPACE = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_MAX_LISTING_BYTES = 2 * 1024 * 1024
_MAX_LISTING_PAGES = 3


@dataclass(frozen=True)
class NoaaObjectVersion:
    key: str
    last_modified: datetime
    etag: str
    size: int


@dataclass(frozen=True)
class NoaaCycleSnapshot:
    run_init_time: datetime
    available_at: datetime
    signature: str
    source_uri: str
    object_count: int
    rejected_cycles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Stable monitoring identity, excluding retrieval times and error messages."""
        return {
            "run_init_time": self.run_init_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "signature": self.signature,
            "source_uri": self.source_uri,
            "object_count": self.object_count,
        }


def source_version_signature(run_init: datetime, objects: list[NoaaObjectVersion]) -> str:
    """Hash source identity consistently for discovery and downloaded manifests."""
    identity = {
        "run_init_time": run_init.isoformat(),
        "objects": [
            {"key": item.key, "last_modified": item.last_modified.isoformat(),
             "etag": item.etag, "size": item.size}
            for item in sorted(objects, key=lambda item: item.key)
        ],
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_listing(url: str) -> bytes:
    if not url.startswith(BASE_URL + "/?list-type=2&"):
        raise NoaaArchiveError("Only the public NOAA GFS S3 listing is allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "HackAlem-Wind-Forecast/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200 or response.geturl() != url:
                    raise NoaaArchiveError("Unexpected NOAA listing status or redirect")
                size_header = response.headers.get("Content-Length")
                if size_header is not None:
                    try:
                        size = int(size_header)
                    except ValueError as exc:
                        raise NoaaArchiveError("Invalid NOAA listing response length") from exc
                    if not 0 <= size <= _MAX_LISTING_BYTES:
                        raise NoaaArchiveError("NOAA metadata listing exceeds its byte limit")
                payload = response.read(_MAX_LISTING_BYTES + 1)
                if len(payload) > _MAX_LISTING_BYTES:
                    raise NoaaArchiveError("NOAA metadata listing exceeds its byte limit")
                if size_header is not None and len(payload) != size:
                    raise NoaaArchiveError("Incomplete NOAA metadata listing")
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise NoaaArchiveError(f"NOAA metadata listing returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == 2:
                raise NoaaArchiveError("NOAA metadata listing could not be reached") from exc
        time.sleep(0.5 * (attempt + 1))
    raise NoaaArchiveError("NOAA metadata listing retries exhausted")


def _run_objects(run_init: datetime) -> dict[str, NoaaObjectVersion]:
    prefix = f"gfs.{run_init:%Y%m%d}/{run_init:%H}/atmos/gfs.t{run_init:%H}z.pgrb2.0p25.f"
    objects: dict[str, NoaaObjectVersion] = {}
    token: str | None = None
    seen_tokens: set[str] = set()
    for _ in range(_MAX_LISTING_PAGES):
        query = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            query["continuation-token"] = token
        try:
            root = ET.fromstring(_read_listing(BASE_URL + "/?" + urlencode(query)))
        except ET.ParseError as exc:
            raise NoaaArchiveError("Malformed NOAA metadata listing") from exc
        if (root.tag != _NAMESPACE + "ListBucketResult"
                or root.findtext(_NAMESPACE + "Name") != "noaa-gfs-bdp-pds"
                or root.findtext(_NAMESPACE + "Prefix") != prefix):
            raise NoaaArchiveError("NOAA metadata listing has an unexpected bucket or prefix")
        for entry in root.findall(_NAMESPACE + "Contents"):
            key = entry.findtext(_NAMESPACE + "Key")
            modified = entry.findtext(_NAMESPACE + "LastModified")
            etag = entry.findtext(_NAMESPACE + "ETag")
            size = entry.findtext(_NAMESPACE + "Size")
            if not key or not key.startswith(prefix) or key in objects:
                raise NoaaArchiveError("NOAA metadata listing has an invalid or duplicate object key")
            try:
                timestamp = require_utc(datetime.fromisoformat(modified), "LastModified")
                byte_count = int(size)
                if not etag or byte_count <= 0:
                    raise ValueError("absent ETag or object size")
            except (TypeError, ValueError) as exc:
                raise NoaaArchiveError("NOAA object lacks valid version metadata") from exc
            objects[key] = NoaaObjectVersion(key, timestamp, etag, byte_count)
        truncated = root.findtext(_NAMESPACE + "IsTruncated")
        if truncated == "false":
            return objects
        token = root.findtext(_NAMESPACE + "NextContinuationToken")
        if truncated != "true" or not token or token in seen_tokens:
            raise NoaaArchiveError("NOAA metadata listing has invalid pagination")
        seen_tokens.add(token)
    raise NoaaArchiveError("NOAA metadata listing exceeded its page limit")


def _snapshot(request: ForecastRequest, run_init: datetime) -> NoaaCycleSnapshot:
    objects = _run_objects(run_init)
    prefix = f"gfs.{run_init:%Y%m%d}/{run_init:%H}/atmos/gfs.t{run_init:%H}z.pgrb2.0p25.f"
    selected: list[NoaaObjectVersion] = []
    for lead in range(1, request.horizon_hours + 1):
        valid_time = request.issue_time + timedelta(hours=lead)
        forecast_hour = int((valid_time - run_init).total_seconds() // 3600)
        if not 1 <= forecast_hour <= 120:
            raise NoaaArchiveError("NOAA cycle does not provide the required hourly forecast range")
        for suffix in ("", ".idx"):
            key = prefix + f"{forecast_hour:03d}" + suffix
            source = objects.get(key)
            if source is None:
                raise NoaaArchiveError(f"Incomplete NOAA cycle: missing f{forecast_hour:03d}{suffix}")
            if not run_init <= source.last_modified <= request.issue_time:
                raise NoaaArchiveError(f"NOAA f{forecast_hour:03d}{suffix} was unavailable at issue_time")
            selected.append(source)
    return NoaaCycleSnapshot(
        run_init_time=run_init,
        available_at=max(item.last_modified for item in selected),
        signature=source_version_signature(run_init, selected),
        source_uri=f"{BASE_URL}/gfs.{run_init:%Y%m%d}/{run_init:%H}/atmos/",
        object_count=len(selected),
    )


def discover_noaa_cycle(
    request: ForecastRequest, *, max_candidate_runs: int = 4,
) -> NoaaCycleSnapshot:
    """Return the newest fully published eligible cycle, or fail explicitly.

    One bounded S3 listing per candidate normally suffices. This downloads no
    forecast field bytes and requires neither ecCodes nor API credentials.
    Changes to selected object ETags, sizes or timestamps change the signature.
    The request window is part of the identity through its selected object keys.
    """
    if request.mode == "fixture":
        raise NoaaArchiveError("Fixture forecasts must use the offline mock provider")
    if type(max_candidate_runs) is not int or not 1 <= max_candidate_runs <= 8:
        raise ValueError("max_candidate_runs must be between 1 and 8")
    newest = request.issue_time.replace(hour=(request.issue_time.hour // 6) * 6)
    rejected: list[str] = []
    for offset in range(max_candidate_runs):
        run_init = newest - timedelta(hours=6 * offset)
        try:
            snapshot = _snapshot(request, run_init)
        except NoaaArchiveError as exc:
            rejected.append(f"{run_init.isoformat()}: {exc}")
            continue
        return replace(snapshot, rejected_cycles=tuple(rejected))
    raise NoaaArchiveError("No complete eligible NOAA cycle: " + "; ".join(rejected))
