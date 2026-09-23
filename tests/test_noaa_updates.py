"""Offline checks for NOAA availability and weather revision discovery."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

from wind_forecast.agent.noaa_archive import NoaaArchiveError
from wind_forecast.agent.noaa_updates import _read_listing, _run_objects, discover_noaa_cycle
from wind_forecast.contracts import ForecastRequest

UTC = timezone.utc
ISSUE = datetime(2026, 2, 1, 5, tzinfo=UTC)
NEW = ISSUE.replace(hour=0)
OLD = NEW - timedelta(hours=6)


def listing(run, *, omitted=None, late=None, version="v1", truncated=False, token=None):
    prefix = f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f"
    entries = []
    for lead in range(1, 49):
        hour = int((ISSUE + timedelta(hours=lead) - run).total_seconds() // 3600)
        for suffix in ("", ".idx"):
            name = f"{hour:03d}{suffix}"
            if name == omitted:
                continue
            modified = ISSUE + timedelta(seconds=1) if name == late else run + timedelta(hours=4)
            entries.append(
                f"<Contents><Key>{prefix}{name}</Key><LastModified>{modified.isoformat()}</LastModified>"
                f"<ETag>{escape(chr(34) + version + chr(34))}</ETag><Size>100</Size></Contents>"
            )
    return (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>noaa-gfs-bdp-pds</Name><Prefix>{prefix}</Prefix>"
        f"<IsTruncated>{str(truncated).lower()}</IsTruncated>"
        + (f"<NextContinuationToken>{token}</NextContinuationToken>" if token else "")
        + "".join(entries) + "</ListBucketResult>"
    ).encode()


def cycle_from_url(url):
    prefix = parse_qs(urlsplit(url).query)["prefix"][0]
    day, hour = prefix.split("/")[:2]
    return datetime.strptime(day.removeprefix("gfs.") + hour, "%Y%m%d%H").replace(tzinfo=UTC)


class NoaaUpdateTests(unittest.TestCase):
    def setUp(self):
        self.request = ForecastRequest("discovery", ISSUE, ("T1",), 24, "live")

    def test_newest_published_cycle_selected_without_fixed_six_hour_delay(self):
        with patch("wind_forecast.agent.noaa_updates._read_listing", return_value=listing(NEW)) as http:
            found = discover_noaa_cycle(self.request)
        self.assertEqual(found.run_init_time, NEW)
        self.assertEqual(found.available_at, NEW + timedelta(hours=4))
        self.assertEqual(found.object_count, 48)
        self.assertEqual(found.rejected_cycles, ())
        self.assertEqual(http.call_count, 1)
        self.assertNotIn("retrieved_at", found.to_dict())

    def test_older_cycle_selected_if_any_index_is_late_or_missing(self):
        for kwargs in ({"omitted": "020.idx"}, {"late": "020.idx"}, {"late": "020"}):
            def read(url):
                run = cycle_from_url(url)
                return listing(run, **kwargs) if run == NEW else listing(run)
            with self.subTest(kwargs=kwargs), patch("wind_forecast.agent.noaa_updates._read_listing", side_effect=read):
                found = discover_noaa_cycle(self.request)
            self.assertEqual(found.run_init_time, OLD)
            self.assertEqual(len(found.rejected_cycles), 1)
            self.assertNotIn("rejected_cycles", found.to_dict())

    def test_forty_eight_hour_horizon_requires_every_later_object(self):
        request = ForecastRequest("48-hours", ISSUE, ("T1",), 48, "historical")
        with patch("wind_forecast.agent.noaa_updates._read_listing", return_value=listing(NEW, omitted="053.idx")):
            with self.assertRaisesRegex(NoaaArchiveError, "missing f053.idx"):
                discover_noaa_cycle(request, max_candidate_runs=1)

    def test_stable_signature_changes_for_revision_or_forecast_window(self):
        with patch("wind_forecast.agent.noaa_updates._read_listing", return_value=listing(NEW)):
            first = discover_noaa_cycle(self.request)
            second = discover_noaa_cycle(self.request)
            moved_request = ForecastRequest("later", ISSUE + timedelta(hours=1), ("T1",), 24, "live")
            later = discover_noaa_cycle(moved_request)
        with patch("wind_forecast.agent.noaa_updates._read_listing", return_value=listing(NEW, version="v2")):
            revised = discover_noaa_cycle(self.request)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertNotEqual(first.signature, revised.signature)
        self.assertNotEqual(first.signature, later.signature)

    def test_missing_all_candidates_stops_without_guessing_availability(self):
        def incomplete(url):
            run = cycle_from_url(url)
            return listing(run, omitted=f"{int((ISSUE + timedelta(hours=1) - run).total_seconds() // 3600):03d}")
        with patch("wind_forecast.agent.noaa_updates._read_listing", side_effect=incomplete) as http:
            with self.assertRaisesRegex(NoaaArchiveError, "No complete eligible NOAA cycle"):
                discover_noaa_cycle(self.request, max_candidate_runs=4)
        self.assertEqual(http.call_count, 4)

    def test_missing_time_metadata_never_accepted(self):
        invalid = listing(NEW).replace(b"2026-02-01T04:00:00+00:00", b"2026-02-01T04:00:00")
        with patch("wind_forecast.agent.noaa_updates._read_listing", return_value=invalid):
            with self.assertRaisesRegex(NoaaArchiveError, "version metadata"):
                discover_noaa_cycle(self.request, max_candidate_runs=1)

    def test_bad_bucket_and_truncated_response_are_rejected(self):
        for invalid in (
            listing(NEW).replace(b"<Name>noaa-gfs-bdp-pds</Name>", b"<Name>other</Name>"),
            listing(NEW, truncated=True),
        ):
            with self.subTest(payload=invalid[:50]), patch("wind_forecast.agent.noaa_updates._read_listing", return_value=invalid):
                with self.assertRaises(NoaaArchiveError):
                    _run_objects(NEW)

    def test_body_is_bounded_even_without_content_length(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {}
        url = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/?list-type=2&prefix=example"
        response.geturl.return_value = url
        response.read.return_value = b"x" * (2 * 1024 * 1024 + 1)
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(NoaaArchiveError, "byte limit"):
                _read_listing(url)
        response.read.assert_called_once_with(2 * 1024 * 1024 + 1)

    def test_fixture_discovery_is_blocked_without_network(self):
        request = ForecastRequest("fixture", ISSUE, ("T1",), 24, "fixture")
        with patch("wind_forecast.agent.noaa_updates._read_listing") as http:
            with self.assertRaisesRegex(NoaaArchiveError, "offline mock"):
                discover_noaa_cycle(request)
        http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
