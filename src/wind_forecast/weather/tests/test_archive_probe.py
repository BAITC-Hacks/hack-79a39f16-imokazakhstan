"""Offline synthetic tests of the real archive's eligibility guards."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring

from wind_forecast.contracts import ForecastRequest
from wind_forecast.weather.archive_probe import parse_required_index, probe_run


def synthetic_listing(
    run: datetime, *, omitted: int | None = None, late: int | None = None
) -> bytes:
    root = Element("ListBucketResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
    SubElement(root, "Name").text = "noaa-gfs-bdp-pds"
    for source_lead in range(7, 55):
        for suffix in ("", ".idx"):
            if source_lead == omitted and not suffix:
                continue
            item = SubElement(root, "Contents")
            SubElement(
                item, "Key"
            ).text = f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{source_lead:03d}{suffix}"
            stamp = (
                "2026-02-01T00:01:00.000Z"
                if source_lead == late and suffix
                else "2026-01-31T22:01:00.000Z"
            )
            SubElement(item, "LastModified").text = stamp
            SubElement(item, "Size").text = "40000" if suffix else "500000000"
    return tostring(root)


class ArchiveProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issue = datetime(2026, 2, 1, tzinfo=UTC)
        self.run = datetime(2026, 1, 31, 18, tzinfo=UTC)
        self.request = ForecastRequest(
            "synthetic-unit-test", self.issue, ("T1", "T2"), mode="historical"
        )

    def test_all_source_leads_are_checked(self) -> None:
        payload = synthetic_listing(self.run)
        probe = probe_run(self.request, self.run, get_bytes=lambda _: payload)
        self.assertEqual(len(probe.leads), 48)
        self.assertEqual(
            [probe.leads[0].source_lead_hours, probe.leads[-1].source_lead_hours], [7, 54]
        )
        self.assertEqual(probe.provenance_status, "unverified")
        self.assertLessEqual(probe.metadata_available_at, self.issue)

    def test_missing_middle_file_rejected(self) -> None:
        payload = synthetic_listing(self.run, omitted=28)
        with self.assertRaisesRegex(ValueError, "missing GFS forecast"):
            probe_run(self.request, self.run, get_bytes=lambda _: payload)

    def test_last_index_publication_controls_availability(self) -> None:
        payload = synthetic_listing(self.run, late=54)
        with self.assertRaisesRegex(ValueError, "after issue_time"):
            probe_run(self.request, self.run, get_bytes=lambda _: payload)

    def test_future_run_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "after issue_time"):
            probe_run(self.request, self.issue + timedelta(hours=6), get_bytes=lambda _: b"")

    def test_exact_field_ranges_and_missing_field(self) -> None:
        content = (
            b"1:0:d=2026013118:TMP:2 m above ground:7 hour fcst:\n"
            b"2:20:d=2026013118:UGRD:10 m above ground:7 hour fcst:\n"
            b"3:40:d=2026013118:VGRD:10 m above ground:7 hour fcst:\n"
            b"4:60:d=2026013118:TMP:surface:7 hour fcst:\n"
        )
        fields = parse_required_index(content, grib_size=80)
        self.assertEqual(
            [(field.name, field.byte_start, field.byte_end) for field in fields],
            [("temp_2m_k", 0, 19), ("u_10m_ms", 20, 39), ("v_10m_ms", 40, 59)],
        )
        with self.assertRaisesRegex(ValueError, "missing required GFS fields"):
            parse_required_index(content.replace(b"VGRD", b"OTHER"), grib_size=80)


if __name__ == "__main__":
    unittest.main()
