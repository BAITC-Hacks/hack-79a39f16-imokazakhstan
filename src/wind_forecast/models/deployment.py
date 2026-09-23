"""Offline 48-hour artifacts. Canonical observations must label hourly interval ends."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
from wind_forecast.contracts import ForecastRow, ForecastResult
from wind_forecast.models.history import features


class SavedPredictor:
    def __init__(self, model_path, metadata):
        self.metadata = metadata
        self.model_id = metadata['model_id']
        self.kind = metadata['architecture']
        path = Path(model_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['artifact_sha256']:
            raise ValueError('Artifact hash mismatch')
        if self.kind.startswith('catboost'):
            from catboost import CatBoostRegressor
            self.model = CatBoostRegressor()
            self.model.load_model(str(path))
        else:
            import torch
            from wind_forecast.models.sequence_forecast import SequenceRegressor
            self.model = SequenceRegressor(self.kind, width=metadata['width'])
            self.model.load_state_dict(torch.load(path, map_location='cpu', weights_only=True))
            self.model.eval()

    def predict_frames(self, frames, issue, turbine_ids=('T1','T2')):
        issue = pd.Timestamp(issue)
        if issue.tzinfo is None:
            raise ValueError('Timezone required')
        issue = issue.tz_convert('Etc/GMT-5')
        if issue.hour != 6 or issue.minute or issue.second or issue.microsecond:
            raise ValueError('Model is validated for daily 06:00 UTC+5 issues')
        if issue < pd.Timestamp(self.metadata['trained_through']):
            raise ValueError('Model training cutoff exceeds issue')
        xs, sequences = [], []
        for tid in turbine_ids:
            if tid not in ('T1','T2'):
                raise ValueError('Only T1 and T2 are trained')
            k = int(tid[1:])-1
            past = frames[k].reindex(pd.date_range(end=issue, periods=24, freq='h'))
            if past.power.notna().sum() < 18:
                raise ValueError('At least 18 observed power hours in last 24 required')
            own = np.r_[features(frames[k], issue)[0], k]
            xs.append(np.r_[own,features(frames[1-k],issue)[0]] if self.kind=='catboost_neighbor' else own)
            if not self.kind.startswith('catboost'):
                from wind_forecast.models.sequence_forecast import sequence_at
                sequences.append(sequence_at(frames,issue,k,neighbor=self.kind=='graph_gru'))
        x = np.asarray(xs)
        if self.kind.startswith('catboost'):
            p = self.model.predict(x)
        else:
            from wind_forecast.models.sequence_forecast import predict_sequence
            p = predict_sequence(self.model,np.asarray(sequences),x[:,-3:].astype(np.float32),'cpu')
        if not np.isfinite(p).all():
            raise ValueError('Nonfinite prediction')
        return np.clip(p,0,1)

    def predict(self, request, observations, weather=None):
        rows = []
        accepted = []
        for o in observations:
            if o.observed_at <= request.issue_time and o.available_at <= request.issue_time:
                if o.turbine_id in ('T1','T2') and o.quality_flag=='ok':
                    if o.observed_at.minute or o.observed_at.second:
                        raise ValueError('Expected hourly END timestamps, not raw ten-minute CSV')
                    accepted.append(o)
        frames = []
        for tid in ('T1','T2'):
            records = [(o.observed_at,o.power_norm,o.wind_ms,o.temp_c) for o in accepted if o.turbine_id==tid]
            frame = pd.DataFrame(records,columns=['time','power','wind','temperature']).set_index('time')
            frame.index = pd.to_datetime(frame.index,utc=True)
            if frame.index.has_duplicates:
                raise ValueError('Duplicate hourly observations')
            frames.append(frame.sort_index().replace([np.inf,-np.inf],np.nan))
        pred = self.predict_frames(frames,request.issue_time,request.turbine_ids)
        for i,tid in enumerate(request.turbine_ids):
            for h in range(request.horizon_hours):
                value=float(pred[i,h])
                radius=self.metadata.get('interval80_radius',{}).get(tid,{}).get('1-24' if h<24 else '25-48')
                rows.append(ForecastRow(tid,request.issue_time,request.issue_time+timedelta(hours=h+1),h+1,value,
                    max(0.,value-radius) if radius is not None else None,None,
                    min(1.,value+radius) if radius is not None else None))
        digest = hashlib.sha256(repr(sorted((o.turbine_id,o.observed_at.isoformat(),o.power_norm,o.wind_ms,o.temp_c) for o in accepted)).encode()).hexdigest()
        return ForecastResult(f'{self.model_id}-{request.issue_time.strftime("%Y%m%dT%H%M")}-{digest[:12]}',request.request_id,'1.0',self.model_id,
            weather.bundle_id if weather else 'history-only',digest,datetime.now(timezone.utc),'ok',False,
            ('History-only forecast; no future weather. Empirical 80% bands, when present, are approximate and need rolling recalibration.',),tuple(rows))


def load_predictor(*,model_path,metadata):
    return SavedPredictor(model_path,metadata)


def main():
    import argparse
    from wind_forecast.models.history import load_hourly
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--metadata',type=Path,required=True)
    parser.add_argument('--csv',type=Path,nargs=2,required=True)
    parser.add_argument('--issue',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    model=SavedPredictor(args.model,json.loads(args.metadata.read_text()))
    frames=[load_hourly(p)[0] for p in args.csv]
    tick=time.perf_counter()
    prediction=model.predict_frames(frames,args.issue)
    payload={'issue_time':args.issue,'model_id':model.model_id,'prediction_seconds':time.perf_counter()-tick,
             'units':'normalized_active_power','predictions':dict(zip(('T1','T2'),prediction.tolist()))}
    args.output.write_text(json.dumps(payload,indent=2))
    print(json.dumps({'output':str(args.output),'seconds':payload['prediction_seconds'],'shape':list(prediction.shape)}))


if __name__=='__main__':
    main()
