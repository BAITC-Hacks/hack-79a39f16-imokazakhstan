"""Tests for causal uncertainty calibration and interval scoring."""
import unittest

import numpy as np

from wind_forecast.evaluation.monthly_replay import (
    HOUR_NS, interval_offsets, interval_metrics, rolling_intervals,
)


class MonthlyReplayTests(unittest.TestCase):
    def test_finite_sample_absolute_quantile(self):
        low,high=interval_offsets(np.arange(1,11),'absolute',alpha=.2,min_count=5)
        self.assertEqual((low,high),(-9,9))

    def test_insufficient_errors_produce_missing_interval(self):
        low,high=interval_offsets([1,2,np.nan],min_count=3)
        self.assertTrue(np.isnan(low) and np.isnan(high))

    def test_interval_score_penalizes_missed_targets(self):
        result=interval_metrics(np.array([1.,3.]),np.array([0.,0.]),np.array([2.,2.]))
        self.assertEqual(result['coverage'],.5)
        self.assertEqual(result['mean_width'],2.)
        self.assertEqual(result['interval_score'],7.)

    def test_unmatured_errors_cannot_change_intervals(self):
        issues=np.arange(20,dtype=np.int64)*24*HOUR_NS
        turbines=np.zeros(20,dtype=int)
        pred=np.zeros((20,48));actual=np.ones((20,48))
        active=np.ones(20,dtype=bool)
        before=rolling_intervals(pred,actual,issues,turbines,active,30,'signed')
        target_issue=15
        valid_ns=issues[:,None]+np.arange(1,49)[None,:]*HOUR_NS
        actual[valid_ns>issues[target_issue]]=10000
        after=rolling_intervals(pred,actual,issues,turbines,active,30,'signed')
        np.testing.assert_equal(before[0][target_issue],after[0][target_issue])
        np.testing.assert_equal(before[1][target_issue],after[1][target_issue])
        self.assertTrue(np.isfinite(before[0][target_issue]).all())


if __name__=='__main__':
    unittest.main()
