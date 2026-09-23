"""Attach approximate 80% bands from fully observed, out-of-fold recent residuals."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--metadata',type=Path,required=True)
 p.add_argument('--replay',type=Path,required=True)
 a=p.parse_args(); m=json.loads(a.metadata.read_text()); r=np.load(a.replay)
 cutoff=pd.Timestamp(m['trained_through']).value
 hour=3600_000_000_000
 valid=r['issues'][:,None]+np.arange(1,49)*hour
 eligible=(valid<=cutoff)&(valid>cutoff-30*24*hour)&(r['issues'][:,None]<cutoff)
 errors=np.abs(np.clip(r['prediction'],0,1)-r['actual'])
 radii={}; counts={}
 for turbine in (0,1):
  tid=f'T{turbine+1}';radii[tid]={};counts[tid]={}
  for name,lo,hi in [('1-24',0,24),('25-48',24,48)]:
   mask=eligible[:,lo:hi]&(r['turbines']==turbine)[:,None]
   residuals=errors[:,lo:hi][mask];residuals=residuals[np.isfinite(residuals)]
   if len(residuals)<100: raise ValueError('Insufficient matured calibration errors')
   radii[tid][name]=float(np.quantile(residuals,.8,method='higher'));counts[tid][name]=len(residuals)
 m.update(interval80_radius=radii,interval80_counts=counts,intervals='Approximate symmetric 80% empirical bands; preceding 30 days of out-of-fold errors, grouped by turbine/day. Overlapping forecast vintages; no coverage guarantee. Recalibrate with matured new actuals.')
 a.metadata.write_text(json.dumps(m,indent=2))
 print(json.dumps({'model':m['model_id'],'radii':radii}))


if __name__=='__main__':main()
