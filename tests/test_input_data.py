from __future__ import annotations

import csv
import io
import unittest
from datetime import datetime, timedelta, timezone

from wind_forecast.agent.input_data import SOURCE_COLUMNS, load_scada


def source_csv(
    start: datetime,
    *,
    count: int = 6,
    omit: set[int] | None = None,
    wind: str = "6",
    power: str = "0.4",
    temperature: str = "-2",
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(SOURCE_COLUMNS)
    for index in range(count):
        if index not in (omit or set()):
            stamp = start + timedelta(minutes=10 * index)
            writer.writerow(
                [index + 1, stamp.strftime("%Y-%m-%d %H:%M:%S"), wind, power, temperature]
            )
    return stream.getvalue().encode("utf-8")


class InputDataTests(unittest.TestCase):
    def test_start_labels_complete_at_next_hour_and_apply_reporting_delay(self) -> None:
        data = load_scada(
            source_csv(datetime(2026, 1, 31, 23)),
            "T1",
            source_timezone="UTC",
            reporting_delay_minutes=15,
        )
        row, = data.observations
        self.assertEqual(row.observed_at, datetime(2026, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(row.available_at, datetime(2026, 2, 1, 0, 15, tzinfo=timezone.utc))
        self.assertAlmostEqual(row.power_norm, 0.4)
        self.assertAlmostEqual(row.wind_ms, 6)
        self.assertAlmostEqual(row.temp_c, -2)
        cutoff = datetime(2026, 2, 1, tzinfo=timezone.utc)
        eligible = [
            o for o in data.observations if o.observed_at <= cutoff and o.available_at <= cutoff
        ]
        self.assertEqual(eligible, [])
        self.assertEqual(data.report["input_rows"], 6)
        self.assertEqual(data.report["hourly_rows"], 1)

    def test_end_labels_use_current_hour_including_hour_boundary(self) -> None:
        data = load_scada(
            source_csv(datetime(2026, 1, 1, 0, 10)),
            "T2",
            source_timezone="UTC",
            interval_label="end",
        )
        row, = data.observations
        self.assertEqual(row.observed_at, datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(row.available_at, row.observed_at)

    def test_explicit_timezone_converts_to_utc(self) -> None:
        data = load_scada(
            source_csv(datetime(2026, 1, 1, 5)), "T1", source_timezone="Etc/GMT-5"
        )
        self.assertEqual(
            data.observations[0].observed_at, datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(data.report["source_timezone"], "Etc/GMT-5")

    def test_missing_sample_drops_only_incomplete_hour(self) -> None:
        data = load_scada(
            source_csv(datetime(2026, 1, 1), count=12, omit={2}),
            "T1",
            source_timezone="UTC",
        )
        self.assertEqual(len(data.observations), 1)
        self.assertEqual(data.report["dropped_incomplete_hours"], 1)
        self.assertEqual(data.report["dropped_incomplete_samples"], 5)
        self.assertEqual(data.report["missing_10min_slots"], 1)
        self.assertEqual(data.observations[0].observed_at.hour, 2)

    def test_duplicate_timestamp_is_rejected(self) -> None:
        raw = source_csv(datetime(2026, 1, 1))
        duplicate = raw.splitlines(keepends=True)[1]
        with self.assertRaisesRegex(ValueError, "duplicate timestamp"):
            load_scada(raw + duplicate, "T1", source_timezone="UTC")

    def test_nonfinite_or_blank_numeric_values_are_rejected(self) -> None:
        for bad in ("NaN", "inf", "-inf", ""):
            with self.subTest(value=bad), self.assertRaisesRegex(ValueError, "numeric value"):
                load_scada(
                    source_csv(datetime(2026, 1, 1), power=bad), "T1", source_timezone="UTC"
                )

    def test_negative_wind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "negative wind"):
            load_scada(
                source_csv(datetime(2026, 1, 1), wind="-1"), "T1", source_timezone="UTC"
            )

    def test_ambiguous_and_nonexistent_local_times_are_rejected(self) -> None:
        cases = (
            (datetime(2025, 11, 2, 1), "ambiguous local time"),
            (datetime(2025, 3, 9, 2), "nonexistent local time"),
        )
        for stamp, message in cases:
            with self.subTest(timestamp=stamp), self.assertRaisesRegex(ValueError, message):
                load_scada(source_csv(stamp), "T1", source_timezone="America/New_York")

    def test_configuration_and_alignment_are_validated(self) -> None:
        raw = source_csv(datetime(2026, 1, 1))
        for kwargs in (
            {"source_timezone": ""},
            {"source_timezone": "Not/A_Timezone"},
            {"source_timezone": "UTC", "interval_label": "middle"},
            {"source_timezone": "UTC", "reporting_delay_minutes": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                load_scada(raw, "T1", **kwargs)
        with self.assertRaisesRegex(ValueError, "ten-minute boundary"):
            load_scada(source_csv(datetime(2026, 1, 1, 0, 5)), "T1", source_timezone="UTC")

    def test_utf8_bom_single_digit_hour_and_numeric_means(self) -> None:
        raw = source_csv(datetime(2026, 1, 1)).replace(b" 00:", b" 0:")
        data = load_scada(b"\xef\xbb\xbf" + raw, "T1", source_timezone="UTC")
        self.assertEqual(len(data.observations), 1)
        self.assertEqual(len(str(data.report["source_sha256"])), 64)


if __name__ == "__main__":
    unittest.main()
