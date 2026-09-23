"""Synthetic, local tests for Person 1's observation boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from wind_forecast.contracts import ForecastRequest
from wind_forecast.data.loader import load_observations
from wind_forecast.data.source_adapter import (
    SourceColumns,
    SourceConfig,
    load_source_config,
    prepare_observations,
    publish_prepared_artifact,
    select_as_of,
)


class SourceAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic_test_input.csv"

    def config(self, **overrides: object) -> SourceConfig:
        settings = {
            "columns": SourceColumns("id", "time", "power", "wind", "temp"),
            "turbine_ids": {"one": "T1", "two": "T2"},
            "timezone_name": "UTC",
            "time_label": "interval_start",
            "interval_minutes": 60,
            "target_unit": "source normalized unit, not yet confirmed",
            "availability_basis": "explicit synthetic test lag only",
            "publication_lag_minutes": 30,
        }
        settings.update(overrides)
        return SourceConfig(**settings)

    def write(self, body: str) -> None:
        self.path.write_text("id,time,power,wind,temp\n" + body, encoding="utf-8")

    def test_interval_start_availability_and_target_preservation(self) -> None:
        self.write("one,2026-01-31T22:00:00,1.25,0,-3\n")
        prepared = prepare_observations(self.path, self.config())
        (row,) = prepared.observations
        self.assertEqual(row.observed_at, datetime(2026, 1, 31, 23, tzinfo=UTC))
        self.assertEqual(row.available_at, datetime(2026, 1, 31, 23, 30, tzinfo=UTC))
        self.assertEqual(row.power_norm, 1.25)
        self.assertEqual(row.wind_ms, 0.0)
        self.assertEqual(row.quality_flag, "ok")

    def test_missing_invalid_and_duplicate_conflicts(self) -> None:
        self.write(
            "one,2026-01-31T20:00:00,0.2,NaN,-3\n"
            "two,2026-01-31T20:00:00,0,-1,-3\n"
            "one,2026-01-31T21:00:00,0.3,7,-3\n"
            "one,2026-01-31T21:00:00,0.3,7,-3\n"
            "two,2026-01-31T22:00:00,0.4,8,-3\n"
            "two,2026-01-31T22:00:00,0.5,8,-3\n"
        )
        prepared = prepare_observations(self.path, self.config())
        self.assertEqual(prepared.report.source_rows, 6)
        self.assertEqual(prepared.report.retained_rows, 3)
        self.assertEqual(prepared.report.duplicate_identical, 1)
        self.assertEqual(prepared.report.quality_counts["invalid_wind"], 2)
        self.assertEqual(
            sum("duplicate_conflict" in problem.reasons for problem in prepared.report.problems), 2
        )
        self.assertEqual(len(prepared.report.source_sha256), 64)

    def test_gap_report_counts_missing_hour_without_imputation(self) -> None:
        self.write("one,2026-01-31T20:00:00,0.2,7,-3\none,2026-01-31T22:00:00,0.4,8,-3\n")
        prepared = prepare_observations(self.path, self.config())
        self.assertEqual(prepared.report.missing_intervals_by_turbine["T1"], 1)
        self.assertEqual(
            prepared.report.missing_examples_by_turbine["T1"],
            ("2026-01-31T22:00:00+00:00",),
        )
        self.assertEqual(len(prepared.observations), 2)

    def test_unresolved_availability_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "availability source"):
            self.config(publication_lag_minutes=None)

    def test_ambiguous_and_nonexistent_local_time_are_quarantined(self) -> None:
        self.write("one,2025-11-02T01:30:00,0.3,7,-3\none,2025-03-09T02:30:00,0.3,7,-3\n")
        prepared = prepare_observations(self.path, self.config(timezone_name="America/New_York"))
        self.assertEqual(len(prepared.observations), 0)
        self.assertEqual(
            {reason for problem in prepared.report.problems for reason in problem.reasons},
            {"ambiguous_local_time", "nonexistent_local_time"},
        )

    def test_cutoff_requires_explicit_freeze_and_both_times(self) -> None:
        self.write("one,2026-01-31T22:00:00,0.2,7,-3\none,2026-01-31T23:00:00,0.4,8,-3\n")
        rows = prepare_observations(self.path, self.config()).observations
        request = ForecastRequest(
            "test", datetime(2026, 2, 1, tzinfo=UTC), ("T1",), mode="historical"
        )
        with self.assertRaisesRegex(ValueError, "explicit aware cutoff"):
            select_as_of(rows, request)
        cutoff = datetime(2026, 2, 1, tzinfo=UTC)
        self.assertEqual(
            len(select_as_of(rows, request, frozen_jan31_end=cutoff, freeze_inclusive=True)), 1
        )
        self.assertEqual(
            len(select_as_of(rows, request, frozen_jan31_end=cutoff, freeze_inclusive=False)), 1
        )
        later_request = ForecastRequest(
            "later", datetime(2026, 2, 2, tzinfo=UTC), ("T1",), mode="historical"
        )
        self.assertEqual(
            len(select_as_of(rows, later_request, frozen_jan31_end=cutoff, freeze_inclusive=True)),
            2,
        )
        self.assertEqual(
            len(select_as_of(rows, later_request, frozen_jan31_end=cutoff, freeze_inclusive=False)),
            1,
        )

    def test_canonical_loader_compatibility_and_nonfinite_rejection(self) -> None:
        self.path.write_text(
            "turbine_id,observed_at,available_at,power_norm,wind_ms,temp_c,quality_flag\n"
            "T1,2026-01-01T01:00:00+01:00,2026-01-01T01:00:00+01:00,0,5,,ok\n",
            encoding="utf-8-sig",
        )
        loaded = load_observations(self.path)
        self.assertEqual(loaded[0].observed_at, datetime(2026, 1, 1, tzinfo=UTC))
        self.assertIsNone(loaded[0].temp_c)
        self.path.write_text(
            "turbine_id,observed_at,available_at,power_norm,wind_ms\n"
            "T1,2026-01-01T00:00:00Z,2026-01-01T00:00:00Z,nan,5\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "line 2"):
            load_observations(self.path)

    def test_versioned_config_and_immutable_temp_artifact(self) -> None:
        self.write("one,2026-01-31T22:00:00,0.4,7,-3\n")
        config = self.config(
            source_verified=True,
            verification_evidence="synthetic unit test only",
            target_definition="synthetic normalized target for unit testing only",
        )
        config_path = Path(self.temp.name) / "synthetic_source_config.json"
        config_path.write_text(json.dumps({"version": 1, **asdict(config)}), encoding="utf-8")
        loaded = load_source_config(config_path)
        prepared = prepare_observations(self.path, loaded)
        output = publish_prepared_artifact(prepared, loaded, Path(self.temp.name) / "processed")
        self.assertEqual(
            load_observations(output / "observations.csv"), list(prepared.observations)
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest["checksums"]),
            {"observations.csv", "quality_report.json", "source_config.json"},
        )
        with self.assertRaises(FileExistsError):
            publish_prepared_artifact(prepared, loaded, Path(self.temp.name) / "processed")

    def test_unverified_source_cannot_publish(self) -> None:
        self.write("one,2026-01-31T22:00:00,0.4,7,-3\n")
        config = self.config()
        prepared = prepare_observations(self.path, config)
        with self.assertRaisesRegex(ValueError, "unverified"):
            publish_prepared_artifact(prepared, config, Path(self.temp.name) / "processed")


if __name__ == "__main__":
    unittest.main()
