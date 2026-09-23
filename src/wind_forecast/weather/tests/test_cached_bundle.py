"""Synthetic offline tests for bundle replay and tamper detection."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from wind_forecast.contracts import ForecastRequest
from wind_forecast.weather.gfs_extract import attest_origin_s3_manifest, load_cached_bundle
from wind_forecast.weather.noaa_gfs import NoaaGfsProvider


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class CachedBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.coords = {"T1": (43.645150, 78.535604)}
        self.issue = datetime(2026, 2, 1, tzinfo=UTC)
        self.run = self.issue - timedelta(hours=6)
        self.request = ForecastRequest(
            "synthetic-cache-test", self.issue, ("T1",), 24, "historical"
        )
        self.proof = self._write_synthetic_proof()

    def _write_synthetic_proof(self) -> Path:
        payload = b"GRIB synthetic unit test only"
        digest = sha(payload)
        message = self.cache / "messages" / digest[:2] / f"{digest}.grib2"
        message.parent.mkdir(parents=True)
        message.write_bytes(payload)
        records = []
        rows = []
        for hour in range(1, 25):
            valid = self.issue + timedelta(hours=hour)
            stem = f"gfs.20260131/18/atmos/gfs.t18z.pgrb2.0p25.f{hour + 6:03d}"
            records.append(
                {
                    "valid_time": valid.isoformat(),
                    "source_lead_hours": hour + 6,
                    "grib_key": stem,
                    "index_key": f"{stem}.idx",
                    "grib_last_modified": "2026-01-31T22:01:00+00:00",
                    "index_last_modified": "2026-01-31T22:01:00+00:00",
                    "messages": [
                        {"field": name, "sha256": digest}
                        for name in ("temp_2m_k", "u_10m_ms", "v_10m_ms")
                    ],
                }
            )
            rows.append(
                {
                    "turbine_id": "T1",
                    "valid_time": valid.isoformat(),
                    "wind_ms": 5.0,
                    "temp_c": 0.0,
                    "wind_direction_deg": 0.0,
                    "wind_height_m": 10.0,
                }
            )
        source_hash = sha(canonical(records))
        identity = {
            "source_hash": source_hash,
            "coordinates": {"T1": [43.645150, 78.535604]},
            "extraction": "synthetic test only",
            "issue_time": self.issue.isoformat(),
            "horizon_hours": 24,
        }
        bundle_id = f"gfs0p25-{sha(canonical(identity))[:24]}"
        manifest = {
            "format_version": 1,
            "bundle_id": bundle_id,
            "provider": "synthetic unit test",
            "weather_model": "synthetic unit test",
            "run_init_time": self.run.isoformat(),
            "issue_time": self.issue.isoformat(),
            "horizon_hours": 24,
            "turbine_ids": ["T1"],
            "coordinates": identity["coordinates"],
            "retrieved_at": self.issue.isoformat(),
            "metadata_available_at": "2026-01-31T22:01:00+00:00",
            "availability_basis": "synthetic unit test only",
            "source_uri": "https://noaa-gfs-bdp-pds.s3.amazonaws.com",
            "source_hash": source_hash,
            "rows_sha256": sha(canonical(rows)),
            "extraction": identity["extraction"],
            "provenance_status": "unverified",
            "is_synthetic": False,
            "source_records": records,
            "rows": rows,
        }
        directory = self.cache / "bundles"
        directory.mkdir()
        path = directory / f"{bundle_id}.json"
        path.write_bytes(canonical(manifest))
        return path

    def test_unverified_cache_cannot_be_used_for_a_forecast(self) -> None:
        with self.assertRaisesRegex(ValueError, "reviewed, verified"):
            load_cached_bundle(self.proof, self.request, self.coords)

    def test_attested_cache_replays_offline_and_detects_tampering(self) -> None:
        verified = attest_origin_s3_manifest(self.proof, self.request, self.coords)
        self.assertTrue(verified.exists())
        provider = NoaaGfsProvider({**self.coords, "T2": (43.643198, 78.538828)}, self.cache)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network called")):
            bundle = provider.fetch(self.request)
        self.assertEqual(len(bundle.rows), 24)
        self.assertEqual(bundle.provenance_status, "verified_original")
        raw = next((self.cache / "messages").rglob("*.grib2"))
        raw.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            provider.fetch(self.request)
        raw.write_bytes(b"GRIB synthetic unit test only")
        self.proof.write_bytes(self.proof.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "proof-manifest hash mismatch"):
            provider.fetch(self.request)

    def test_cache_only_missing_bundle_does_not_touch_network(self) -> None:
        provider = NoaaGfsProvider(self.coords, self.cache / "empty")
        with (
            patch("urllib.request.urlopen", side_effect=AssertionError("network called")),
            self.assertRaises(FileNotFoundError),
        ):
            provider.fetch(self.request)

    def test_unknown_turbine_is_rejected_before_network(self) -> None:
        provider = NoaaGfsProvider(self.coords, self.cache, allow_network=True)
        request = ForecastRequest("unknown", self.issue, ("T9",), 24, "historical")
        with self.assertRaisesRegex(ValueError, "unknown turbine coordinates"):
            provider.fetch(request)


if __name__ == "__main__":
    unittest.main()
