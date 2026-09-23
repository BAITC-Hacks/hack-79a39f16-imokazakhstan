"""Refit the development-selected wind-scenario model for subsequent daily forecasts."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from wind_forecast.models.history import make_estimator
from wind_forecast.models.history_predictor import HistoryPowerPredictor


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    args=parser.parse_args()
    report=json.loads((args.experiment/'report.json').read_text())
    if report['winner_selected_on_october_november'] != 'wind_tree_scenarios':
        raise ValueError('this refit is specific to the selected wind scenario architecture')
    destination=args.experiment/'model.joblib'
    if destination.exists():
        raise FileExistsError(destination)
    data=joblib.load(args.experiment/'experiment_data.joblib')
    selected=data['final_train'] & np.isfinite(data['wind']).all(axis=1)
    started=time.perf_counter()
    model=make_estimator('extra_shallow')
    with threadpool_limits(limits=2):
        model.fit(data['x'][selected],data['wind'][selected])
    curves={}
    for turbine,frame in enumerate(data['frames']):
        valid=frame.dropna(subset=['wind','power'])
        bins=valid.groupby(np.floor(valid.wind).astype(int)).power.mean()
        curves[turbine]={'wind':(bins.index.to_numpy()+.5).tolist(),'power':bins.to_numpy().tolist()}
    artifact={
        'schema_version':1, 'model_id':HistoryPowerPredictor.model_id,
        'wind_model':model,'curves':curves,'feature_names':data['feature_names'],
        'interval_offsets':report['interval_offsets'],
        'metadata':{
            'training_available_through':report['last_observation_interval_end'],
            'source_sha256':report['source_sha256'], 'training_origins':int(selected.sum()),
            'versions':report['versions'], 'issue_hour_local':6, 'utc_offset_hours':5,
            'observation_availability_assumption':'ten-minute interval start + 10 minutes',
            'features':'own turbine past power, wind and temperature; turbine ID; season',
            'point_prediction':'mean of mapped individual tree wind forecasts; additive correction disabled by validation',
            'intervals':'fixed October-November error offsets from pre-September model; provisional after refit',
            'fit_seconds':time.perf_counter()-started,
        }}
    joblib.dump(artifact,destination,compress=3)
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    (args.experiment/'model_metadata.json').write_text(json.dumps({**artifact['metadata'],
        'sha256':digest,'bytes':destination.stat().st_size},indent=2))
    print(json.dumps({'artifact':str(destination),'sha256':digest,'fit_seconds':artifact['metadata']['fit_seconds'],
                      'bytes':destination.stat().st_size},indent=2))


if __name__ == '__main__':
    main()
