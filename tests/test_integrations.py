from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from wind_forecast.agent.integrations import (
    BundleWeatherProvider,
    load_team_predictor,
    weather_bundle_dict,
)
from wind_forecast.contracts import ForecastRequest

ISSUE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def bundle_payload(bundle_id: str = "weather-one", *, available_hours_ago: int = 2) -> dict:
    return {
        "bundle_id": bundle_id,
        "provider": "team-archive",
        "weather_model": "example-provider-model",
        "run_init_time": (ISSUE - timedelta(hours=8)).isoformat(),
        "available_at": (ISSUE - timedelta(hours=available_hours_ago)).isoformat(),
        "retrieved_at": (ISSUE + timedelta(days=10)).isoformat(),
        "availability_basis": "Provider publication log retained with original archive.",
        "source_uri": "https://weather.example/archive/original",
        "source_hash": "source-bytes-sha256",
        "provenance_status": "verified_original",
        "is_synthetic": False,
        "rows": [
            {
                "turbine_id": turbine,
                "valid_time": (ISSUE + timedelta(hours=lead)).isoformat(),
                "wind_ms": 7.5,
                "temp_c": -2.0,
                "wind_height_m": 100,
            }
            for turbine in ("T1", "T2")
            for lead in range(1, 49)
        ],
    }


class WeatherIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.request = ForecastRequest("request", ISSUE, ("T2",), 24, "historical")

    def write_bundle(self, filename: str, payload: dict) -> Path:
        path = self.root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_selects_latest_available_and_slices_exact_requested_keys(self) -> None:
        self.write_bundle("older.json", bundle_payload("older", available_hours_ago=4))
        self.write_bundle("latest.json", bundle_payload("latest", available_hours_ago=1))
        self.write_bundle("future.json", bundle_payload("future", available_hours_ago=-1))
        result = BundleWeatherProvider(self.root).fetch(self.request)
        self.assertEqual(result.bundle_id, "latest")
        self.assertEqual(len(result.rows), 24)
        self.assertEqual({row.turbine_id for row in result.rows}, {"T2"})
        self.assertEqual(result.rows[0].valid_time, ISSUE + timedelta(hours=1))
        self.assertEqual(result.rows[-1].valid_time, ISSUE + timedelta(hours=24))

    def test_tie_uses_latest_run_initialization(self) -> None:
        older = bundle_payload("older")
        newer = bundle_payload("newer")
        newer["run_init_time"] = (ISSUE - timedelta(hours=3)).isoformat()
        self.write_bundle("older.json", older)
        self.write_bundle("newer.json", newer)
        self.assertEqual(BundleWeatherProvider(self.root).fetch(self.request).bundle_id, "newer")

    def test_future_only_and_missing_hours_fail(self) -> None:
        future = self.write_bundle("future.json", bundle_payload(available_hours_ago=-1))
        with self.assertRaisesRegex(ValueError, "unavailable_at_issue=1"):
            BundleWeatherProvider(future).fetch(self.request)
        missing = bundle_payload()
        missing["rows"] = missing["rows"][:-30]
        path = self.write_bundle("missing.json", missing)
        with self.assertRaisesRegex(ValueError, "missing_hours=1"):
            BundleWeatherProvider(path).fetch(self.request)

    def test_historical_rejects_synthetic_and_unverified_weather(self) -> None:
        for status, synthetic in (("synthetic", True), ("unverified", False)):
            with self.subTest(status=status):
                payload = bundle_payload()
                payload.update(provenance_status=status, is_synthetic=synthetic)
                path = self.write_bundle("weather.json", payload)
                with self.assertRaisesRegex(ValueError, "unverified_or_synthetic=1"):
                    BundleWeatherProvider(path).fetch(self.request)

    def test_wrapped_bundle_and_complete_roundtrip(self) -> None:
        path = self.write_bundle("wrapped.json", {"weather": bundle_payload()})
        original = BundleWeatherProvider(path).fetch(self.request)
        exported = weather_bundle_dict(original)
        self.assertEqual(len(exported["rows"]), 24)
        replay_path = self.write_bundle("replay.json", exported)
        self.assertEqual(BundleWeatherProvider(replay_path).fetch(self.request), original)

    def test_malformed_evidence_types_duplicates_and_numbers_fail(self) -> None:
        payloads = []
        for key, bad in (("availability_basis", " "), ("is_synthetic", "false")):
            payload = bundle_payload()
            payload[key] = bad
            payloads.append(payload)
        for bad in ("7.5", float("inf"), True, -1):
            payload = bundle_payload()
            payload["rows"][0]["wind_ms"] = bad
            payloads.append(payload)
        duplicate = bundle_payload()
        duplicate["rows"].append(dict(duplicate["rows"][0]))
        payloads.append(duplicate)
        naive = bundle_payload()
        naive["available_at"] = "2026-01-31T22:00:00"
        payloads.append(naive)
        for index, payload in enumerate(payloads):
            with self.subTest(index=index), self.assertRaises(ValueError):
                path = self.write_bundle("invalid.json", payload)
                BundleWeatherProvider(path).fetch(self.request)


class ModelIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.artifact = self.root / "model.bin"
        self.artifact.write_bytes(b"team-owned-native-artifact")
        self.metadata_path = self.root / "model.json"
        self.metadata = {
            "model_id": "team-model-v1",
            "trained_through": (ISSUE - timedelta(days=1)).isoformat(),
            "features": ["wind_ms", "temp_c"],
            "target_units": "normalized_active_power",
            "trained_on_synthetic": False,
            "training_data_hash": "known-training-dataset-hash",
            "artifact_sha256": hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
        }
        self.predictor = types.SimpleNamespace(model_id="team-model-v1", predict=Mock())
        self.factory = Mock(return_value=self.predictor)
        module = types.ModuleType("test_team_factory")
        module.load = self.factory
        self.module_patch = patch.dict(sys.modules, {"test_team_factory": module})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def load(self, *, training_limit: datetime = ISSUE):
        self.metadata_path.write_text(json.dumps(self.metadata), encoding="utf-8")
        return load_team_predictor(
            "test_team_factory:load",
            str(self.artifact),
            str(self.metadata_path),
            issue_time=ISSUE,
            training_limit=training_limit,
            mode="historical",
        )

    def test_verified_artifact_calls_factory_with_path_and_metadata(self) -> None:
        predictor, metadata = self.load()
        self.assertIs(predictor, self.predictor)
        self.factory.assert_called_once_with(model_path=self.artifact, metadata=metadata)

    def test_future_training_and_frozen_cutoff_are_rejected_before_factory(self) -> None:
        self.metadata["trained_through"] = (ISSUE + timedelta(hours=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "trained_through exceeds"):
            self.load()
        self.metadata["trained_through"] = (ISSUE - timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "trained_through exceeds"):
            self.load(training_limit=ISSUE - timedelta(days=2))
        self.factory.assert_not_called()

    def test_artifact_hash_mismatch_is_rejected_before_factory(self) -> None:
        self.artifact.write_bytes(b"modified-artifact")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.load()
        self.factory.assert_not_called()

    def test_metadata_requirements_and_synthetic_training_are_rejected(self) -> None:
        original = dict(self.metadata)
        for field, value in (
            ("training_data_hash", ""),
            ("features", []),
            ("trained_on_synthetic", True),
            ("trained_on_synthetic", "false"),
            ("trained_through", "2026-01-01T00:00:00"),
            ("target_units", "MW"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.metadata = dict(original)
                self.metadata[field] = value
                self.load()
        self.factory.assert_not_called()

    def test_predictor_contract_must_match_metadata(self) -> None:
        self.predictor.model_id = "wrong-model"
        with self.assertRaisesRegex(ValueError, "model_id does not match"):
            self.load()


if __name__ == "__main__":
    unittest.main()
