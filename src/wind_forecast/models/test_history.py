"""Causality and timestamp checks for the daily history experiment."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from wind_forecast.contracts import ForecastRequest, Observation
from wind_forecast.models.history_predictor import HistoryPowerPredictor

import numpy as np
import pandas as pd

from wind_forecast.models.history import features, load_hourly
from wind_forecast.evaluation.history_experiment import causal_correction


class HistoryTests(unittest.TestCase):
    def test_features_ignore_future(self):
        issue = pd.Timestamp('2026-01-01 06:00', tz='Etc/GMT-5')
        index = pd.date_range(issue-pd.Timedelta(days=31), issue+pd.Timedelta(days=2), freq='1h')
        frame = pd.DataFrame({'power':.5, 'wind':8., 'temperature':5.}, index=index)
        expected, names = features(frame, issue)
        frame.loc[frame.index > issue] = 999999
        actual, other_names = features(frame, issue)
        np.testing.assert_equal(expected, actual)
        self.assertEqual(names, other_names)

    def test_hourly_labels_end_and_gaps_not_filled(self):
        text = 'ID,Статистическое время,Средняя скорость ветра(m/s),Нормализованная активная мощность,Средняя температура окружающей среды(°C)\n'
        for i in range(11):
            hour, minute = 5+i//6, (i%6)*10
            text += f'{i},2026-01-01 {hour:02}:{minute:02}:00,8,0.4,10\n'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.csv'
            path.write_text(text)
            hourly, _ = load_hourly(path)
        self.assertEqual(hourly.index[0].hour,6)
        self.assertAlmostEqual(hourly.power.iloc[0],.4)
        self.assertTrue(np.isnan(hourly.power.iloc[1]))

    def test_correction_uses_only_matured_targets(self):
        hour = 3600_000_000_000
        issues = np.array([0,24*hour,48*hour], dtype=np.int64)
        turbines = np.zeros(3,dtype=int)
        predicted = np.zeros((3,48))
        actual = np.ones((3,48))
        actual[0,24:] = 999  # Not known at the second issue.
        correction = causal_correction(predicted,actual,issues,turbines,7)
        np.testing.assert_equal(correction[0],0)
        np.testing.assert_equal(correction[1,:24],1)
        np.testing.assert_equal(correction[1,24:],0)

    def test_saved_predictor_checks_training_cutoff(self):
        model=HistoryPowerPredictor.__new__(HistoryPowerPredictor)
        issue=datetime(2026,1,1,1,tzinfo=timezone.utc)
        model.metadata={'training_available_through':(issue+timedelta(hours=1)).isoformat()}
        request=ForecastRequest('cutoff',issue,('T1',),48,'historical')
        with self.assertRaisesRegex(ValueError,'training data'):
            model.predict(request,[])

    def test_interval_not_available_until_its_end(self):
        model=HistoryPowerPredictor.__new__(HistoryPowerPredictor)
        issue=datetime(2026,1,1,1,tzinfo=timezone.utc)
        model.metadata={'training_available_through':(issue-timedelta(days=1)).isoformat()}
        request=ForecastRequest('interval',issue,('T1',),48,'historical')
        # An observation stamped 06:00 represents 06:00–06:10 and is not known yet.
        observation=Observation('T1',issue,issue,.5,8,10)
        with self.assertRaisesRegex(ValueError,'after issue_time'):
            model.predict(request,[observation])


if __name__ == '__main__':
    unittest.main()
