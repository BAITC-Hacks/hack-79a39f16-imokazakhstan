"""Verify downloaded weights against real cached history; never downloads data."""
import argparse
import json
from pathlib import Path
import time
import joblib
import numpy as np
import pandas as pd
from wind_forecast.contracts import ForecastRequest, Observation
from wind_forecast.models.deployment import SavedPredictor


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--dataset',type=Path,required=True)
 p.add_argument('--artifacts',type=Path,required=True)
 a=p.parse_args(); d=joblib.load(a.dataset); issue=pd.Timestamp('2026-02-01T06:00:00+05:00')
 observations=[]
 for k,f in enumerate(d['frames']):
  for t,row in f.loc[issue-pd.Timedelta(days=30):issue].iterrows():
   observations.append(Observation(f'T{k+1}',t.to_pydatetime(),t.to_pydatetime(),float(row.power),float(row.wind),float(row.temperature)))
 results=[]
 for name,extension in [('catboost_neighbor','cbm'),('patch_transformer','pt')]:
  path=a.artifacts/f'{name}.{extension}';meta=json.loads(path.with_suffix('.metadata.json').read_text())
  model=SavedPredictor(path,meta)
  tick=time.perf_counter();pred=model.predict_frames(d['frames'],issue);seconds=time.perf_counter()-tick
  assert pred.shape==(2,48) and np.isfinite(pred).all() and ((pred>=0)&(pred<=1)).all()
  changed=[f.copy() for f in d['frames']]
  for f in changed:f.loc[issue+pd.Timedelta(hours=1)]=999
  np.testing.assert_array_equal(pred,model.predict_frames(changed,issue))
  request=ForecastRequest('local-validation',issue.to_pydatetime(),('T1','T2'),mode='historical',observation_policy='available')
  result=model.predict(request,observations)
  np.testing.assert_allclose(pred,np.array([r.prediction for r in result.rows]).reshape(2,48))
  delayed=Observation('T1',issue.to_pydatetime(),(issue+pd.Timedelta(hours=1)).to_pydatetime(),999,999,999)
  assert model.predict(request,observations+[delayed]).rows==result.rows
  for row in result.rows:
   if row.p10 is not None: assert 0<=row.p10<=row.prediction<=row.p90<=1
  try:model.predict_frames(d['frames'],issue-pd.Timedelta(days=1))
  except ValueError:pass
  else:raise AssertionError('Training cutoff not enforced')
  bad=dict(meta,artifact_sha256='0'*64)
  try:SavedPredictor(path,bad)
  except ValueError:pass
  else:raise AssertionError('Hash mismatch accepted')
  results.append({'model':name,'seconds_full_features_and_prediction':seconds,'bytes':path.stat().st_size,
    'rows':len(result.rows),'future_invariance':True,'availability_filter':True,'training_cutoff':True,'hash_verification':True,'interval_ordering':True})
 (a.artifacts/'local-validation.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))


if __name__=='__main__':main()
