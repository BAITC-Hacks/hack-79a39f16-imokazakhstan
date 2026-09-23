"""Monitor behavior: changed inputs, retries, durable results and no duplicate work."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock

from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.monitor import ForecastMonitor
from wind_forecast.agent.settings import RunConfig


class MonitorTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime(2026, 2, 1, tzinfo=UTC)
        self.data = self.root / "measurements.csv"
        self.data.write_text("first input")
        self.model = self.root / "model.json"
        self.model.write_text("model 1")
        self.metadata = self.root / "metadata.json"
        self.metadata.write_text("metadata 1")
        self.config = RunConfig(
            issue_time=self.now, mode="historical", weather_source="noaa_gfs",
            data_paths={"T1": str(self.data), "T2": str(self.data)},
            assumptions_confirmed=True, output_root=str(self.root / "runs"),
            model_path=str(self.model), model_metadata_path=str(self.metadata),
        )
        self.probe = Mock(return_value={"eligible_version": "weather 1"})
        self.runner = Mock(side_effect=self._run)
        self.monitor = ForecastMonitor(self.root / "monitor", poll_interval_seconds=30,
                                       weather_probe=self.probe, runner=self.runner)

    def _run(self, config, state="completed"):
        run_dir = self.root / "runs" / str(self.runner.call_count)
        request = config.request()
        response = {"state": state, "run_dir": str(run_dir),
                    "request": {"request_id": request.request_id,
                                "issue_time": request.issue_time.isoformat()},
                    "result": {"rows": [{"prediction": 0.5}]}}
        return SimpleNamespace(state=state, request=request, run_dir=run_dir,
                               error=None if state == "completed" else "Weather unavailable",
                               to_dict=lambda: response)

    def poll(self, seconds=0, config=None):
        return self.monitor.poll(config or self.config, now=self.now + timedelta(seconds=seconds))

    def test_initial_unchanged_and_restart_do_not_repeat_forecast(self):
        first = self.poll()
        self.assertEqual(first.state, "completed")
        self.assertEqual(first.reasons, ("initial_forecast",))
        self.assertEqual(self.poll(1).state, "waiting")
        self.assertEqual(self.probe.call_count, 2)
        self.assertEqual(self.poll(30).state, "unchanged")
        self.assertEqual(self.runner.call_count, 1)
        restarted = ForecastMonitor(self.root / "monitor", poll_interval_seconds=30,
                                    weather_probe=self.probe, runner=self.runner)
        after_restart = restarted.poll(self.config, now=self.now + timedelta(seconds=60))
        self.assertEqual(after_restart.state, "unchanged")
        self.assertEqual(after_restart.last_success["result"]["rows"], [{"prediction": 0.5}])
        self.assertEqual(self.runner.call_count, 1)
        history = [json.loads(line) for line in self.monitor.history_path.read_text().splitlines()]
        self.assertEqual([e["state"] for e in history], ["completed", "unchanged", "unchanged"])
        self.assertEqual(history[0]["response_path"], str(first.run.run_dir / "response.json"))

    def test_data_model_metadata_weather_and_config_each_trigger(self):
        self.poll()
        self.data.write_text("new eligible observation")
        self.assertEqual(self.poll(30).reasons, ("measurements_changed",))
        self.model.write_text("model 2")
        self.assertEqual(self.poll(60).reasons, ("model_changed",))
        self.metadata.write_text("metadata 2")
        self.assertEqual(self.poll(90).reasons, ("model_changed",))
        self.probe.return_value = {"eligible_version": "weather 2"}
        self.assertEqual(self.poll(120).reasons, ("weather_updated",))
        self.assertEqual(self.poll(150, replace(self.config, horizon_hours=24)).reasons,
                         ("configuration_changed",))
        self.assertEqual(self.runner.call_count, 6)

    def test_failed_update_keeps_previous_forecast_and_retries_same_signature(self):
        first = self.poll()
        old_signature = self.monitor.load_state()["successful_signature"]
        self.data.write_text("new measurements")
        self.runner.side_effect = lambda config: self._run(config, state="blocked")
        failed = self.poll(30)
        self.assertEqual(failed.state, "blocked")
        self.assertEqual(failed.last_success, first.last_success)
        self.assertEqual(self.monitor.load_state()["successful_signature"], old_signature)
        self.runner.side_effect = self._run
        retried = self.poll(60)
        self.assertEqual(retried.state, "completed")
        self.assertEqual(retried.reasons, ("measurements_changed",))
        self.assertEqual(self.runner.call_count, 3)
        self.assertNotEqual(self.monitor.load_state()["successful_signature"], old_signature)

    def test_probe_failure_is_sanitized_preserves_result_and_retries(self):
        first = self.poll()
        self.probe.side_effect = RuntimeError("Bearer private-credential-do-not-store")
        failed = self.poll(30)
        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.last_success, first.last_success)
        self.assertNotIn("private-credential", self.monitor.state_path.read_text())
        self.assertEqual(self.runner.call_count, 1)
        self.probe.side_effect = None
        self.probe.return_value = {"eligible_version": "weather 2"}
        self.assertEqual(self.poll(60).state, "completed")

    def test_two_workers_for_same_monitor_cannot_run_concurrently(self):
        second = ForecastMonitor(self.root / "monitor", poll_interval_seconds=30,
                                 weather_probe=self.probe, runner=self.runner)
        statuses = []

        def nested(config):
            statuses.append(second.poll(config, now=self.now).state)
            return self._run(config)

        self.runner.side_effect = nested
        self.assertEqual(self.poll().state, "completed")
        self.assertEqual(statuses, ["busy"])
        self.assertEqual(self.runner.call_count, 1)

    def test_input_change_during_run_is_not_consumed_and_triggers_retry(self):
        def changed_while_running(config):
            self.data.write_text("replaced during calculation")
            return self._run(config)

        self.runner.side_effect = changed_while_running
        first = self.poll()
        self.assertEqual(first.state, "completed")
        self.assertIn("inputs_changed_during_run", first.reasons)
        self.assertIsNone(self.monitor.load_state()["successful_signature"])
        self.assertIsNotNone(first.last_success)
        self.runner.side_effect = self._run
        self.assertEqual(self.poll(30).state, "completed")
        self.assertEqual(self.poll(60).state, "unchanged")
        self.assertEqual(self.runner.call_count, 2)

    def test_failed_post_run_probe_keeps_completed_result_but_retries(self):
        self.probe.side_effect = [{"eligible_version": "weather 1"}, TimeoutError("private detail")]
        first = self.poll()
        self.assertEqual(first.state, "completed")
        self.assertIn("inputs_recheck_failed", first.reasons)
        self.assertIsNone(self.monitor.load_state()["successful_signature"])
        self.assertIsNotNone(first.last_success)
        self.probe.side_effect = None
        retried = self.poll(30)
        self.assertEqual(retried.state, "completed")
        self.assertEqual(retried.reasons, ("retry_after_input_change",))
        self.assertEqual(self.runner.call_count, 2)

    def test_live_utc_hour_rolls_and_historical_issue_stays_fixed(self):
        live = replace(self.config, mode="live")
        self.monitor.rolling_live = True
        self.poll(120, live)
        used = self.runner.call_args.args[0]
        self.assertEqual(used.issue_time, self.now)
        self.assertEqual(used.observation_policy, "available")
        self.assertEqual(self.poll(180, live).state, "unchanged")
        rolled = self.poll(3600, live)
        self.assertEqual(rolled.reasons, ("issue_time_changed",))
        self.assertEqual(self.runner.call_args.args[0].issue_time, self.now + timedelta(hours=1))
        self.monitor.rolling_live = False
        self.poll(3660)
        self.assertEqual(self.runner.call_args.args[0].issue_time, self.now)
        self.assertEqual(self.poll(7200).state, "unchanged")

    def test_poll_interval_and_live_mode_are_enforced(self):
        for invalid in (0, 29, 86401, True, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                ForecastMonitor(self.root / "invalid", poll_interval_seconds=invalid)
        self.monitor.rolling_live = True
        outcome = self.poll()
        self.assertEqual(outcome.state, "failed")
        self.assertEqual(self.runner.call_count, 0)

    def test_full_offline_forecast_is_persisted_and_second_poll_does_no_work(self):
        config = RunConfig(issue_time=self.now, output_root=str(self.root / "fixture-runs"))
        runner = Mock(wraps=run_forecast)
        monitor = ForecastMonitor(self.root / "fixture-monitor", poll_interval_seconds=30, runner=runner)
        first = monitor.poll(config, now=self.now)
        self.assertEqual(first.state, "completed")
        self.assertEqual(len(first.last_success["result"]["rows"]), 96)
        self.assertTrue(Path(first.last_success["run_dir"], "response.json").is_file())
        self.assertEqual(monitor.poll(config, now=self.now + timedelta(seconds=30)).state, "unchanged")
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    main()
