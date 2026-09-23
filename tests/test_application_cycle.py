"""Service integration with generated test inputs, fake SDK, and temporary outputs.

The verified provider below simulates a real provider's contract for validation
only. It is not real weather, a submission, or evidence of forecast accuracy.
All artifacts are confined to automatically removed temporary directories.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.input_data import ObservationDataset
from wind_forecast.agent.openai_controller import OpenAIController
from wind_forecast.agent.settings import RunConfig
from wind_forecast.contracts import Observation, WeatherBundle, WeatherPoint
from wind_forecast.models.gradient_boosting import HistogramPowerRegressor


ISSUE = datetime(2026, 2, 1, tzinfo=timezone.utc)
STEPS = ("fetch_weather", "prepare_data", "train_model", "audit_inputs",
         "predict_power", "inspect_forecast", "save_forecast")
FAKE_KEY = "unit-test-placeholder-never-a-real-api-key"


def generated_test_dataset(count=240):
    rows = []
    for i in range(count):
        time = ISSUE - timedelta(hours=count - i)
        wind = 3 + i % 12
        rows.append(Observation("T1", time, time, 0.01 * wind * wind, wind,
                                None if i % 6 == 0 else float(i % 20)))
    return {"T1": ObservationDataset(tuple(rows), {
        "source": "Generated service-test measurements; not organizer data",
        "purpose": "temporary integration checks only",
        "synthetic": True,
    })}


class SimulatedVerifiedProvider:
    """Exercise real-mode provenance guards without making network calls."""

    def __init__(self):
        self.fetch_count = 0

    def fetch(self, request):
        self.fetch_count += 1
        return WeatherBundle(
            bundle_id="generated-service-test-weather",
            provider="simulated-verified-provider-for-unit-test-only",
            weather_model="generated-test-values",
            run_init_time=request.issue_time - timedelta(hours=6),
            available_at=request.issue_time - timedelta(hours=1),
            retrieved_at=request.issue_time,
            availability_basis="Simulated provider assertion for a unit test; not real evidence",
            source_uri="fixture://service-test/contract-simulation",
            source_hash="generated-test-values-not-real-data",
            provenance_status="verified_original",
            is_synthetic=False,  # Deliberately simulate the real provider contract under test.
            rows=tuple(WeatherPoint(turbine, request.issue_time + timedelta(hours=lead),
                4 + lead % 8, float(lead % 12), wind_height_m=100)
                for turbine in request.turbine_ids for lead in range(1, request.horizon_hours + 1)),
        )


def tool_response(name=None, *, number=0):
    output = [] if name is None else [SimpleNamespace(
        type="function_call", name=name, arguments="{}", call_id=f"unit-call-{number}")]
    return SimpleNamespace(output=output, output_text="Synthetic integration check completed.",
        status="completed", usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15))


class FakeSDKClient:
    def __init__(self, scripted_responses):
        self.responses = self
        self.remaining = iter(scripted_responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.remaining)
        if isinstance(response, Exception):
            raise response
        return response


class ApplicationCycleTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="wind-synthetic-service-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.provider = SimulatedVerifiedProvider()

    def config(self, **overrides):
        options = dict(issue_time=ISSUE, turbine_ids=("T1",), horizon_hours=48,
            mode="historical", input_format="canonical", assumptions_confirmed=True,
            observation_policy="available", weather_source="bundles", model_kind="auto",
            weather_path="unit-test-provider-is-injected.json", output_root=str(self.root / "runs"),
            cache_dir=str(self.root / "cache"))
        options.update(overrides)
        return RunConfig(**options)

    def assert_complete(self, run):
        self.assertEqual(run.state, "completed", run.error)
        self.assertEqual(run.result.model_id, HistogramPowerRegressor.model_id)
        self.assertEqual(len(run.result.rows), 48)
        self.assertEqual({r.lead_hours for r in run.result.rows}, set(range(1, 49)))
        self.assertEqual(run.report["model"]["target_units"], "normalized_active_power")
        self.assertLessEqual(datetime.fromisoformat(run.report["model"]["trained_through"]), ISSUE)
        validation = run.report["model_validation"]
        self.assertEqual(validation["training_rows"], 240)
        self.assertEqual(validation["by_turbine"]["T1"]["validation"]["state"], "available")
        self.assertIn("not 24–48 hour forecast accuracy", validation["validation_note"])
        successful_steps = [event["step"] for event in run.trace.events
                            if event.get("status") == "ok" and event.get("step") in STEPS]
        self.assertEqual(successful_steps, list(STEPS))
        self.assertTrue((run.run_dir / "forecast.csv").is_file())
        response = json.loads((run.run_dir / "response.json").read_text())
        self.assertEqual(response["state"], "completed")
        self.assertEqual(len(list((self.root / "runs").glob("*/response.json"))), 1)

    def run_fake_ai(self, scripted_responses):
        client = FakeSDKClient(scripted_responses)
        real_fit = HistogramPowerRegressor.fit
        real_init = OpenAIController.__init__

        def inject_fake_client(controller, api_key, model, **kwargs):
            real_init(controller, api_key, model, client=client, **kwargs)

        with patch("wind_forecast.agent.application.runtime_settings",
                   return_value={"api_key": FAKE_KEY, "model": "unit-test-model"}), \
             patch.object(OpenAIController, "__init__", autospec=True,
                          side_effect=inject_fake_client) as construct_controller, \
             patch.object(HistogramPowerRegressor, "fit", autospec=True, side_effect=real_fit) as fit:
            run = run_forecast(self.config(controller="openai"), datasets=generated_test_dataset(),
                               weather_provider=self.provider)
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(self.provider.fetch_count, 1)
        self.assertEqual(construct_controller.call_count, 1)
        for path in run.run_dir.iterdir():
            if path.is_file():
                self.assertNotIn(FAKE_KEY, path.read_text())
        return run, client

    def test_real_mode_contract_runs_ml_and_persists_diagnostics(self):
        run = run_forecast(self.config(), datasets=generated_test_dataset(),
                           weather_provider=self.provider)
        self.assert_complete(run)
        self.assertEqual(self.provider.fetch_count, 1)
        self.assertEqual(run.report["controller"]["used"], "deterministic")
        self.assertFalse(run.report["model"]["trained_on_synthetic"])
        self.assertTrue(run.report["datasets"]["T1"]["synthetic"])

    def test_sparse_history_blocks_explicitly_without_silent_baseline(self):
        run = run_forecast(self.config(), datasets=generated_test_dataset(48),
                           weather_provider=self.provider)
        self.assertEqual(run.state, "blocked")
        self.assertIn("at least 168", run.error)
        self.assertIn("got 48", run.error)
        self.assertIsNone(run.result)
        self.assertFalse((run.run_dir / "forecast.csv").exists())
        response = json.loads((run.run_dir / "response.json").read_text())
        self.assertEqual(response["state"], "blocked")

    def test_full_seven_tool_openai_cycle_uses_fake_sdk(self):
        responses = [tool_response(name, number=i) for i, name in enumerate(STEPS)]
        run, client = self.run_fake_ai(responses + [tool_response()])
        self.assert_complete(run)
        self.assertEqual(run.report["controller"]["used"], "openai")
        self.assertTrue(run.report["controller"]["completed"])
        self.assertEqual(len(client.calls), 8)
        self.assertEqual(client.calls[-1]["tool_choice"], "none")
        executed = [event["tool"] for event in run.trace.events
                    if event.get("event") == "controller_tool" and event["status"] == "ok"]
        self.assertEqual(executed, list(STEPS))
        self.assertIn("AI commentary", run.summary)

    def test_api_failure_after_training_resumes_without_repeat_fetch_or_fit(self):
        responses = [tool_response(name, number=i) for i, name in enumerate(STEPS[:3])]
        run, client = self.run_fake_ai(responses + [TimeoutError("Simulated SDK timeout")])
        self.assert_complete(run)
        self.assertEqual(len(client.calls), 4)
        controller = run.report["controller"]
        self.assertEqual(controller["used"], "openai_with_deterministic_fallback")
        self.assertEqual(controller["fallback_reason"], "api_error:TimeoutError")
        self.assertFalse(controller["completed"])


if __name__ == "__main__":
    unittest.main()
