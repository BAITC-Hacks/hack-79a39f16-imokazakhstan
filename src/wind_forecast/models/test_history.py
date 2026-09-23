"""Causality and timestamp checks for the daily history experiment."""
import tempfile
import unittest
from pathlib import Path


import numpy as np
import pandas as pd

from wind_forecast.models.history import features, load_hourly


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


if __name__ == '__main__':
    unittest.main()
