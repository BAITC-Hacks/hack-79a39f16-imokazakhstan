"""Regression checks for local models, provenance, and incomplete submissions."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.input_data import ObservationDataset
from wind_forecast.agent.local_models import reject_synthetic_datasets
from wind_forecast.agent.replay import run_replay
from wind_forecast.agent.settings import RunConfig


class LocalIntegrationTests(unittest.TestCase):
    def test_all_explicit_synthetic_markers_block_real_modes(self):
        for marker in [{'synthetic':True},{'is_synthetic':True},{'provenance_status':'synthetic'}]:
            for mode in ('historical','live'):
                with self.subTest(marker=marker,mode=mode),self.assertRaisesRegex(ValueError,'synthetic'):
                    reject_synthetic_datasets({'T1':ObservationDataset((),marker)},mode)
        self.assertTrue(reject_synthetic_datasets({'T1':ObservationDataset((),{'synthetic':True})},'fixture'))

    def test_no_weather_requires_local_model(self):
        with self.assertRaisesRegex(ValueError,'No-weather'):
            RunConfig(issue_time=datetime(2026,2,1,1,tzinfo=timezone.utc),mode='historical',weather_source='none')

    def test_short_successful_fixture_replay_is_not_submission_ready(self):
        with TemporaryDirectory() as folder:
            issue=datetime(2026,2,1,tzinfo=timezone.utc)
            config=RunConfig(issue_time=issue,output_root=folder)
            result=run_replay(config,issue,issue)
            self.assertEqual(result.summary['execution_state'],'completed')
            self.assertEqual(result.summary['state'],'partial')
            self.assertFalse(result.summary['submission_check']['ready'])
            self.assertGreater(result.summary['coverage']['missing_count'],0)
            self.assertTrue(result.summary['is_synthetic'])

    @unittest.skipUnless(Path('data/raw/turbine_1.csv').exists(),'Optional real local SCADA integration')
    def test_real_local_models_cover_48_hours_without_network(self):
        with TemporaryDirectory() as folder:
            config=RunConfig.from_dict(json.loads(Path('examples/local_request.json').read_text()))
            config=replace(config,output_root=folder,turbine_ids=('T1',))
            with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')):
                run=run_forecast(config)
            self.assertEqual(run.state,'completed',run.error)
            self.assertIsNone(run.weather)
            self.assertFalse(run.result.is_synthetic)
            self.assertEqual(len(run.result.rows),48)
            self.assertEqual(len(run.report['wind_forecast']),48)
            self.assertEqual(set(run.report['datasets']),{'T1','T2'})
            self.assertTrue((run.run_dir/'manifest.json').exists())
            self.assertTrue((run.run_dir/'wind.csv').exists())
            self.assertTrue(all(r.p10<=r.prediction<=r.p90 for r in run.result.rows))
