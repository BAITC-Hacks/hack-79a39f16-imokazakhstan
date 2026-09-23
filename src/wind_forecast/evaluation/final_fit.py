"""Refit deployable CPU CatBoost and optional patch Transformer on eligible real history."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import joblib
import numpy as np
import pandas as pd
from wind_forecast.evaluation.extended_replay import train_mask


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--transformer',action='store_true')
    p.add_argument('--device',default='cpu')
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    d=joblib.load(a.dataset)
    cutoff=pd.Timestamp('2026-02-01T00:00:00+05:00')
    train=train_mask(d['issues'],d['y'],cutoff.value)
    if a.transformer:
        import torch
        from wind_forecast.models.sequence_forecast import fit_sequence
        torch.set_num_threads(2)
        tick=time.perf_counter()
        model,info=fit_sequence('patch_transformer',d['sequence'][:,:1],d['calendar'],d['y'],train,
            d['issues'],cutoff.value,epochs=50,device=a.device,width=64)
        path=a.output/'patch_transformer.pt'
        torch.save(model.cpu().state_dict(),path)
        architecture='patch_transformer'
    else:
        from catboost import CatBoostRegressor
        tick=time.perf_counter()
        model=CatBoostRegressor(iterations=200,depth=4,border_count=32,learning_rate=.04,
            l2_leaf_reg=10,thread_count=2,random_seed=42,allow_writing_files=False,verbose=False,loss_function='MultiRMSE')
        model.fit(d['neighbor_x'][train],d['y'][train])
        path=a.output/'catboost_neighbor.cbm'
        model.save_model(str(path))
        architecture='catboost_neighbor'
        info={'parameters':model.get_params()}
    metadata={'model_id':architecture+'-20260131-v1','architecture':architecture,'width':64,
       'trained_through':cutoff.tz_convert('UTC').isoformat(),'training_origins':int(train.sum()),
       'trained_on_synthetic':False,'target_units':'normalized_active_power',
       'features':list(d['feature_names'])+(['neighbor_'+n for n in d['feature_names'][:-1]] if not a.transformer else []),
       'training_data_hash':hashlib.sha256(''.join(d['source_sha256']).encode()).hexdigest(),
       'source_sha256':d['source_sha256'],'artifact_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
       'fit_seconds':time.perf_counter()-tick,'artifact_bytes':path.stat().st_size,'fit_info':info,
       'issue_hour_utc_plus_5':6,'horizon_hours':48,'input_policy':'hourly interval END; available_at <= issue',
       'clip_output':[0,1],'intervals':'none; point prediction only'}
    path.with_suffix('.metadata.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps({'path':str(path),'seconds':metadata['fit_seconds'],'bytes':metadata['artifact_bytes']}))


if __name__=='__main__': main()
