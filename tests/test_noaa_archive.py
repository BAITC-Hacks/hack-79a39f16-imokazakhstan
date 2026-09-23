"""No network or installed ecCodes required for the original-archive safety checks."""

import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from wind_forecast.agent.noaa_archive import (
    BASE_URL,
    FieldRange,
    NoaaArchiveError,
    NoaaArchiveProvider,
    _decode_field,
    _http,
    parse_index,
    select_cycle,
)
from wind_forecast.contracts import ForecastRequest
from wind_forecast.agent.noaa_updates import NoaaCycleSnapshot, NoaaObjectVersion, source_version_signature

UTC = timezone.utc
RUN = datetime(2026, 1, 31, 18, tzinfo=UTC)
ISSUE = datetime(2026, 2, 1, tzinfo=UTC)
MODIFIED = "Sat, 31 Jan 2026 21:34:10 GMT"
INDEX_MODIFIED = "Sat, 31 Jan 2026 21:34:34 GMT"
COORDINATES = {"T1": (43.645150, 78.535604), "T2": (43.643198, 78.538828)}
PAYLOAD = b"GRIB" + b"\x00\x00\x00\x02" + (24).to_bytes(8, "big") + b"test7777"


def index_for(hour: int) -> bytes:
    return "\n".join(
        f"{i + 1}:{i * 24}:d=2026013118:{parameter}:{height} m above ground:{hour} hour fcst:"
        for i, (parameter, height) in enumerate((("UGRD", 100), ("VGRD", 100), ("TMP", 2)))
    ).encode()


def source_http(url, *, method="GET", headers=None, max_bytes=None):
    if url.endswith(".idx"):
        hour = int(url.removesuffix(".idx")[-3:])
        return {"last-modified": INDEX_MODIFIED, "etag": '"index-etag"'}, index_for(hour)
    metadata = {"last-modified": MODIFIED, "etag": '"grib-etag"', "content-length": "72"}
    if method == "HEAD":
        return metadata, b""
    if headers is None or "Range" not in headers:
        raise AssertionError("Full object download attempted")
    metadata["content-range"] = headers["Range"].replace("=", " ") + "/72"
    metadata["content-length"] = "24"
    return metadata, PAYLOAD


def decode_values(payload, field, run_init, valid_time, coordinates):
    value = {"UGRD": 3.0, "VGRD": 4.0, "TMP": 273.15}[field.parameter]
    return ({t: value for t in coordinates}, {
        t: {"latitude": 43.75, "longitude": 78.5, "distance_km": 12.0} for t in coordinates
    })


class NoaaArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.provider = NoaaArchiveProvider(COORDINATES, self.directory.name, max_workers=1)
        self.request = ForecastRequest("noaa-test", ISSUE, ("T1", "T2"), 24, "historical")
        versions = []
        for hour in range(7, 31):
            key = f"gfs.20260131/18/atmos/gfs.t18z.pgrb2.0p25.f{hour:03d}"
            versions.extend((
                NoaaObjectVersion(key, RUN + timedelta(hours=3, minutes=34, seconds=10), '"grib-etag"', 72),
                NoaaObjectVersion(key + ".idx", RUN + timedelta(hours=3, minutes=34, seconds=34), '"index-etag"', len(index_for(hour))),
            ))
        discovery_patch = patch(
            "wind_forecast.agent.noaa_updates.discover_noaa_cycle",
            return_value=NoaaCycleSnapshot(RUN, RUN + timedelta(hours=4), source_version_signature(RUN, versions), BASE_URL, 48),
        )
        self.discovery = discovery_patch.start()
        self.addCleanup(discovery_patch.stop)

    def test_cycle_rounds_down_with_six_hour_delay(self):
        self.assertEqual(select_cycle(ISSUE), RUN)
        self.assertEqual(select_cycle(ISSUE + timedelta(hours=5)), RUN)
        self.assertEqual(select_cycle(ISSUE + timedelta(hours=6)), ISSUE)

    def test_index_exact_height_and_next_message_boundaries(self):
        index = (
            "1:0:d=2026013118:UGRD:100 mb:7 hour fcst:\n"
            "2:20:d=2026013118:UGRD:100 m above ground:7 hour fcst:\n"
            "3:50:d=2026013118:VGRD:100 m above ground:7 hour fcst:\n"
            "4:85:d=2026013118:TMP:2 m above ground:7 hour fcst:\n"
        )
        fields = parse_index(index, 100, RUN, 7, 100)
        self.assertEqual([(f.start, f.end) for f in fields], [(20, 49), (50, 84), (85, 99)])

    def test_index_rejects_missing_or_wrong_forecast_time(self):
        for index in (index_for(8).decode(), index_for(7).decode().replace("VGRD", "UNKNOWN")):
            with self.subTest(index=index), self.assertRaises(NoaaArchiveError):
                parse_index(index, 72, RUN, 7, 100)

    @patch("wind_forecast.agent.noaa_archive._decode_field", side_effect=decode_values)
    @patch("wind_forecast.agent.noaa_archive._http", side_effect=source_http)
    def test_real_contract_extracts_both_turbines_and_caches_sha256(self, http, decode):
        with patch.dict("sys.modules", {"eccodes": MagicMock()}):
            bundle = self.provider.fetch(self.request)
        self.assertEqual(len(bundle.rows), 48)
        self.assertEqual(bundle.provenance_status, "verified_original")
        self.assertFalse(bundle.is_synthetic)
        self.assertEqual(bundle.available_at, datetime(2026, 1, 31, 21, 34, 34, tzinfo=UTC))
        row = bundle.rows[0]
        self.assertEqual(row.wind_ms, 5)
        self.assertEqual(row.temp_c, 0)
        self.assertEqual(row.wind_height_m, 100)
        self.assertAlmostEqual(row.wind_direction_deg, 216.86989764584402)
        manifest = json.loads(next(Path(self.directory.name).glob("manifests/*.json")).read_text())
        self.assertEqual(manifest["source_hash"], bundle.source_hash)
        source = manifest["sources"][0]["fields"][0]
        self.assertEqual(source["sha256"], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertTrue(source["url"].startswith(BASE_URL))
        self.assertEqual(http.call_count, 24 * 5)
        http.reset_mock()
        with patch.dict("sys.modules", {"eccodes": MagicMock()}):
            cached = self.provider.fetch(self.request)
        self.assertEqual(cached.source_hash, bundle.source_hash)
        self.assertEqual(http.call_count, 24 * 2)  # HEAD + small index, no repeated field bodies.

    def test_late_or_missing_source_timestamp_rejected_before_field_download(self):
        for bad_time in ("Sun, 01 Feb 2026 00:00:01 GMT", None):
            def bad_http(url, **kwargs):
                metadata, payload = source_http(url, **kwargs)
                if kwargs.get("method") == "HEAD":
                    if bad_time is None:
                        metadata.pop("last-modified")
                    else:
                        metadata["last-modified"] = bad_time
                return metadata, payload
            with patch("wind_forecast.agent.noaa_archive._http", side_effect=bad_http) as http:
                with self.assertRaises(NoaaArchiveError):
                    self.provider._hour(self.request, RUN, ISSUE + timedelta(hours=1))
                self.assertEqual(http.call_count, 1)

    @patch("wind_forecast.agent.noaa_archive._decode_field", side_effect=decode_values)
    @patch("wind_forecast.agent.noaa_archive._http", side_effect=source_http)
    def test_version_change_between_discovery_and_fetch_is_rejected(self, http, decode):
        self.discovery.return_value = NoaaCycleSnapshot(RUN, RUN + timedelta(hours=4), "outdated-signature", BASE_URL, 48)
        with patch.dict("sys.modules", {"eccodes": MagicMock()}):
            with self.assertRaisesRegex(NoaaArchiveError, "version changed after discovery"):
                self.provider.fetch(self.request)

    def test_late_index_also_rejected(self):
        def late_index(url, **kwargs):
            metadata, payload = source_http(url, **kwargs)
            if url.endswith(".idx"):
                metadata["last-modified"] = "Sun, 01 Feb 2026 00:10:00 GMT"
            return metadata, payload
        with patch("wind_forecast.agent.noaa_archive._http", side_effect=late_index):
            with self.assertRaises(NoaaArchiveError):
                self.provider._hour(self.request, RUN, ISSUE + timedelta(hours=1))

    def test_range_ignored_is_rejected_before_reading_body(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        url = BASE_URL + "/gfs.20260131/18/atmos/gfs.t18z.pgrb2.0p25.f007"
        response.geturl.return_value = url
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(NoaaArchiveError):
                _http(url, headers={"Range": "bytes=0-23"}, max_bytes=24)
        response.read.assert_not_called()

    @patch("wind_forecast.agent.noaa_archive._http", side_effect=source_http)
    def test_nonfinite_decoded_values_rejected(self, http):
        with patch("wind_forecast.agent.noaa_archive._decode_field", return_value=({"T1": math.nan, "T2": 1.0}, {})):
            with self.assertRaises(NoaaArchiveError):
                self.provider._hour(self.request, RUN, ISSUE + timedelta(hours=1))

    def test_decoded_wrong_valid_time_rejected(self):
        eccodes = MagicMock()
        metadata = {
            "edition": 2, "centre": "kwbc", "discipline": 0, "parameterCategory": 2,
            "parameterNumber": 2, "typeOfLevel": "heightAboveGround", "level": 100,
            "stepType": "instant", "dataDate": 20260131, "dataTime": 1800,
            "validityDate": 20260201, "validityTime": 200,
        }
        eccodes.codes_get.side_effect = lambda handle, name: metadata[name]
        with patch.dict("sys.modules", {"eccodes": eccodes}):
            with self.assertRaisesRegex(NoaaArchiveError, "validityTime"):
                _decode_field(PAYLOAD, FieldRange("UGRD", 100, 0, 23), RUN,
                              ISSUE + timedelta(hours=1), COORDINATES)
        eccodes.codes_release.assert_called_once()

    @patch("wind_forecast.agent.noaa_archive._decode_field", side_effect=decode_values)
    @patch("wind_forecast.agent.noaa_archive._http", side_effect=source_http)
    def test_corrupt_cached_field_is_rejected(self, http, decode):
        self.provider._hour(self.request, RUN, ISSUE + timedelta(hours=1))
        cached = next(Path(self.directory.name).glob("fields/*.grib2"))
        cached.write_bytes(b"corrupt")
        with self.assertRaisesRegex(NoaaArchiveError, "Corrupt NOAA cache"):
            self.provider._hour(self.request, RUN, ISSUE + timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
