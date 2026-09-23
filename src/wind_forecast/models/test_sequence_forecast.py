"""Causality and shape checks for the optional expanded experiment."""
import unittest

import numpy as np
import pandas as pd
import torch

from wind_forecast.models.sequence_forecast import sequence_at, SequenceRegressor
from wind_forecast.evaluation.extended_replay import train_mask


class SequenceTests(unittest.TestCase):
    def test_future_changes_cannot_affect_either_node(self):
        times = pd.date_range('2025-01-01',periods=240,freq='h',tz='Etc/GMT-5')
        frame = pd.DataFrame({'power':.3,'wind':5.,'temperature':10.},index=times)
        issue = times[180]
        expected = sequence_at([frame,frame],issue,0,neighbor=True)
        changed = frame.copy()
        changed.loc[changed.index>issue] = 999
        np.testing.assert_array_equal(expected,sequence_at([changed,changed],issue,0,neighbor=True))

    def test_target_must_have_matured_completely(self):
        hour = 3600_000_000_000
        issues = np.array([0,1,2])*hour
        targets = np.ones((3,48))
        np.testing.assert_array_equal(train_mask(issues,targets,49*hour),[True,True,False])
        targets[0,47] = np.nan
        np.testing.assert_array_equal(train_mask(issues,targets,49*hour),[False,True,False])

    def test_all_neural_heads_return_48_finite_hours(self):
        torch.set_num_threads(2)
        for name in ('gru','lstm','patch_transformer','graph_gru'):
            model = SequenceRegressor(name).eval()
            with torch.no_grad():
                prediction = model(torch.zeros(2,2 if name=='graph_gru' else 1,168,6),torch.zeros(2,3))
            self.assertEqual(tuple(prediction.shape),(2,48))
            self.assertTrue(torch.isfinite(prediction).all())


if __name__=='__main__':
    unittest.main()
