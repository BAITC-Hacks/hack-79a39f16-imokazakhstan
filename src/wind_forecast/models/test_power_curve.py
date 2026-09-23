"""Regression checks for persistence, time eligibility, and numeric failures."""
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wind_forecast.contracts import ForecastRequest, Observation
from wind_forecast.models.power_curve import EmpiricalPowerCurve
from wind_forecast.weather.mock import MockWeatherProvider


class PowerCurveTests(unittest.TestCase):
    def setUp(self):
        self.issue = datetime(2026, 1, 31, tzinfo=timezone.utc)
        self.request = ForecastRequest("test", self.issue, ("T1",), 24)
        self.weather = MockWeatherProvider().fetch(self.request)
        self.rows = [Observation("T1", self.issue - timedelta(hours=2),
                                 self.issue - timedelta(hours=1), power, wind)
                     for wind, power in ((4.2, .1), (4.7, .3), (8.1, .8))]

    def test_curve_and_roundtrip(self):
        model = EmpiricalPowerCurve().fit(self.rows)
        self.assertAlmostEqual(model.predict_power("T1", 4.5), .2)
        self.assertAlmostEqual(model.predict_power("unknown", 8), .8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            digest = model.save(path)
            restored = EmpiricalPowerCurve.load(path)
            self.assertEqual(digest, restored.save(path))
            self.assertEqual(model.predict(self.request, [], self.weather).rows,
                             restored.predict(self.request, [], self.weather).rows)

    def test_training_cutoff_cannot_be_bypassed_by_empty_observations(self):
        late = [replace(row, available_at=self.issue + timedelta(minutes=1))
                for row in self.rows]
        model = EmpiricalPowerCurve().fit(late)
        with self.assertRaisesRegex(ValueError, "training data were unavailable"):
            model.predict(self.request, [], self.weather)

    def test_unverified_training_time_rejected(self):
        model = EmpiricalPowerCurve().fit_samples([("T1", 4, .2)])
        with self.assertRaisesRegex(ValueError, "verify training timezone"):
            model.predict(self.request, [], self.weather)

    def test_numeric_and_unfitted_errors(self):
        with self.assertRaisesRegex(ValueError, "fit or load"):
            EmpiricalPowerCurve().predict_power("T1", 4)
        model = EmpiricalPowerCurve().fit(self.rows)
        for wind in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                model.predict_power("T1", wind)
        with self.assertRaises(ValueError):
            EmpiricalPowerCurve().fit_samples([("T1", float("nan"), .2)])

    def test_historical_rejects_synthetic_weather(self):
        model = EmpiricalPowerCurve().fit(self.rows)
        with self.assertRaisesRegex(ValueError, "verified, original"):
            model.predict(replace(self.request, mode="historical"), [], self.weather)

    def test_weather_coverage_and_availability(self):
        model = EmpiricalPowerCurve().fit(self.rows)
        for weather in (replace(self.weather, rows=self.weather.rows[:-1]),
                        replace(self.weather, available_at=self.issue + timedelta(hours=1))):
            with self.assertRaises(ValueError):
                model.predict(self.request, [], weather)


if __name__ == "__main__":
    unittest.main()
