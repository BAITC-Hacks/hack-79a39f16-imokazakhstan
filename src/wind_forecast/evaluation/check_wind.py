"""Audit the existing indirect model's 48-hour wind forecast against simple baselines."""
import argparse,json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from threadpoolctl import threadpool_limits
from wind_forecast.models.history import make_estimator,features
from wind_forecast.evaluation.extended_replay import score,train_mask
from wind_forecast.evaluation.monthly_replay import ZONE


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 a.output.mkdir(exist_ok=False);d=joblib.load(a.dataset);w=d['wind'];x=d['x'];issues=d['issues'];turbines=d['turbines']
 pred=np.full_like(w,np.nan);mean=pred.copy();persistence=pred.copy();col=d['feature_names'].index('wind_lag_0h')
 for month in pd.date_range('2025-01-01','2026-01-01',freq='MS',tz=ZONE):
  cutoff=month+pd.Timedelta(hours=6);end=month+pd.offsets.MonthBegin()+pd.Timedelta(hours=6)
  train=train_mask(issues,d['y'],cutoff.value)&np.isfinite(w).all(axis=1);use=(issues>=cutoff.value)&(issues<end.value)
  model=make_estimator('extra_shallow')
  with threadpool_limits(limits=2):model.fit(x[train],w[train]);pred[use]=model.predict(x[use])
  persistence[use]=x[use,col,None]
  for k,frame in enumerate(d['frames']):mean[use&(turbines==k)]=frame.loc[:cutoff].wind.mean()
 common=np.isfinite(pred)&np.isfinite(mean)&np.isfinite(persistence)&np.isfinite(w)
 report={n:score(v,dict(d,y=np.where(common,w,np.nan))) for n,v in [('wind_model',pred),('historical_mean',mean),('persistence',persistence)]}
 report.update(units='m/s',source_sha256=d['source_sha256'],note='Monthly expanding fit; past SCADA only; no future weather. Same origins as power audit, common finite wind targets.')
 (a.output/'report.json').write_text(json.dumps(report,indent=2));np.savez_compressed(a.output/'predictions.npz',prediction=pred,actual=w,issues=issues,turbines=turbines)
 # Final deployable wind estimator, using only matured observed wind targets.
 cutoff=pd.Timestamp('2026-02-01T00:00:00+05:00');train=train_mask(issues,d['y'],cutoff.value)&np.isfinite(w).all(axis=1)
 model=make_estimator('extra_shallow')
 with threadpool_limits(limits=2):model.fit(x[train],w[train])
 joblib.dump({'model':model,'feature_names':d['feature_names'],'trained_through':cutoff.isoformat(),'target_units':'m/s','source_sha256':d['source_sha256']},a.output/'wind_model.joblib',compress=3)
 issue=pd.Timestamp('2026-02-01T06:00:00+05:00'); xx=np.array([np.r_[features(f,issue)[0],k] for k,f in enumerate(d['frames'])])
 (a.output/'example-20260201.json').write_text(json.dumps({'issue':issue.isoformat(),'units':'m/s','predictions':model.predict(xx).tolist(),'future_forecast_not_evaluation':True},indent=2))
 print(json.dumps({k:v['overall'] for k,v in report.items() if isinstance(v,dict) and 'overall' in v}))


if __name__=='__main__':main()
