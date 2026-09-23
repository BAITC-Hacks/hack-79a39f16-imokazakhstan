from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from wind_forecast.contracts import ForecastRequest, Observation, WeatherBundle, WeatherPoint
from wind_forecast.models.gradient_boosting import HistogramPowerRegressor

ISSUE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def training_rows(count=360, turbines=("T1", "T2")):
    rows = []
    for turbine in turbines:
        for index in range(count):
            time = ISSUE - timedelta(hours=count - index)
            wind = 2 + (index * 7 % 120) / 10
            temperature = None if index % 7 == 0 else -5 + index % 20
            power = 5 + 3 * wind * wind + (5 if turbine == "T2" else 0)
            rows.append(Observation(turbine, time, time, power, wind, temperature))
    return rows


def weather(*, horizon=24, turbines=("T1", "T2")):
    return WeatherBundle("synthetic-unit-test", "mock", "unit-test", ISSUE - timedelta(hours=6),
        ISSUE - timedelta(hours=2), ISSUE, "Synthetic unit test only", "fixture://ml-test", "test",
        "synthetic", True, tuple(WeatherPoint(turbine, ISSUE + timedelta(hours=lead),
            4 + lead % 7, None if lead % 3 == 0 else 5) for turbine in turbines for lead in range(1, horizon + 1)))


class MLModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = training_rows()
        cls.model = HistogramPowerRegressor(max_iter=40).fit(cls.rows, issue_time=ISSUE)

    def test_full_horizon_contract_and_original_scale(self):
        request = ForecastRequest("test-ml", ISSUE, ("T1", "T2"), 48)
        result = self.model.predict(request, [], weather(horizon=48))
        self.assertEqual(len(result.rows), 96)
        self.assertEqual({r.lead_hours for r in result.rows}, set(range(1, 49)))
        self.assertTrue(all(math.isfinite(r.prediction) and r.prediction > 1 for r in result.rows))
        self.assertGreater(max(r.prediction for r in result.rows) - min(r.prediction for r in result.rows), 50)
        self.assertTrue(result.is_synthetic)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(len(result.input_hash), 64)

    def test_report_is_json_serializable_and_holdout_is_chronological(self):
        report = json.loads(json.dumps(self.model.training_report, allow_nan=False))
        self.assertEqual(report["training_rows"], 720)
        self.assertGreater(report["missing_temperature_rows"], 0)
        for turbine in ("T1", "T2"):
            valid = report["by_turbine"][turbine]["validation"]
            self.assertLess(valid["training_end"], valid["holdout_start"])
            self.assertEqual(valid["holdout_rows"], 72)
            self.assertEqual(valid["ml"]["n"], 72)
            self.assertIn("mae", valid["empirical_baseline"])
            self.assertIn("not 24–48 hour forecast accuracy", valid["note"])
        self.assertFalse(report["hyperparameters"]["early_stopping"])

    def test_future_unavailable_and_invalid_targets_are_excluded(self):
        base = training_rows(turbines=("T1",))
        bad_rows = [
            Observation("T1", ISSUE + timedelta(hours=1), ISSUE + timedelta(hours=1), 999999, 7),
            Observation("T1", ISSUE, ISSUE + timedelta(hours=2), 999999, 7),
            replace(base[0], observed_at=ISSUE - timedelta(hours=500), available_at=ISSUE - timedelta(hours=500), power_norm=float("nan")),
        ]
        fit = HistogramPowerRegressor(max_iter=12).fit(base + bad_rows, issue_time=ISSUE)
        clean = HistogramPowerRegressor(max_iter=12).fit(base, issue_time=ISSUE)
        self.assertEqual(fit.training_report["training_data_hash"], clean.training_report["training_data_hash"])
        self.assertEqual(fit.training_report["excluded_rows"]["unavailable_at_issue_or_after_training_limit"], 2)
        req = ForecastRequest("future-test", ISSUE, ("T1",), 24)
        self.assertEqual([r.prediction for r in fit.predict(req, [], weather(turbines=("T1",))).rows],
                         [r.prediction for r in clean.predict(req, [], weather(turbines=("T1",))).rows])

    def test_cutoff_and_delayed_labels_are_respected_in_validation(self):
        rows = training_rows(turbines=("T1",))
        # This measurement is inside the chronological training portion but was
        # reported during the holdout period. It must not enter diagnostic fit.
        rows[250] = replace(rows[250], available_at=ISSUE - timedelta(hours=20))
        fit = HistogramPowerRegressor(max_iter=12).fit(rows, issue_time=ISSUE)
        self.assertEqual(fit.training_report["by_turbine"]["T1"]["validation"]["purged_delayed_training_rows"], 1)
        limited = HistogramPowerRegressor(max_iter=12).fit(rows, issue_time=ISSUE,
            training_limit=ISSUE - timedelta(hours=50))
        self.assertLessEqual(limited.trained_through, ISSUE - timedelta(hours=50))

    def test_sparse_training_and_duplicates_fail(self):
        with self.assertRaisesRegex(ValueError, "at least 168"):
            HistogramPowerRegressor().fit(self.rows[:20], issue_time=ISSUE)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            HistogramPowerRegressor().fit(self.rows + [self.rows[0]], issue_time=ISSUE)
        with self.assertRaisesRegex(ValueError, "got 0"):
            HistogramPowerRegressor(max_iter=5).fit(training_rows(turbines=("T1",)),
                issue_time=ISSUE, turbine_ids=("T1", "T9"))

    def test_forecast_rejects_incomplete_invalid_and_late_weather(self):
        request = ForecastRequest("bad-weather", ISSUE, ("T1", "T2"), 24)
        bundle = weather()
        for invalid in (replace(bundle, rows=bundle.rows[:-1]),
                        replace(bundle, rows=(replace(bundle.rows[0], wind_ms=float("inf")),) + bundle.rows[1:]),
                        replace(bundle, run_init_time=ISSUE + timedelta(hours=1))):
            with self.assertRaises(ValueError):
                self.model.predict(request, [], invalid)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.model.predict(replace(request, issue_time=ISSUE - timedelta(days=1)), [], bundle)

    def test_weather_outside_training_range_is_labeled(self):
        request = ForecastRequest("outside", ISSUE, ("T1", "T2"), 24)
        bundle = weather()
        result = self.model.predict(request, [], replace(bundle,
            rows=tuple(replace(p, wind_ms=80) for p in bundle.rows)))
        self.assertEqual(result.status, "degraded")
        self.assertTrue(any("outside the training range" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
