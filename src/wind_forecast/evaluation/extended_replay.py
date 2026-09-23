"""Bounded monthly replay of additional CPU/neural/foundation model families.

Only this experiment imports optional dependencies. Cached joblib inputs must be
trusted local files. Pretrained checkpoints are retrospective, not as-of eligible.
"""
from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import platform
import resource
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from wind_forecast.models.history import features, expanded_features, metrics
from wind_forecast.evaluation.monthly_replay import HOUR_NS, ZONE

MODELS = ['catboost_multi4','catboost_multi6','catboost_neighbor','catboost_pooled',
          'arima_201','autoarima','sarima_101','gru','lstm','patch_transformer','graph_gru',
          'chronos_bolt_tiny','chronos2_small','chronos2_small_lora','chronos2_base','chronos2_base_lora']


def prepare(experiment, output):
    from wind_forecast.models.sequence_forecast import sequence_at
    data = joblib.load(experiment/'experiment_data.joblib')
    sequence, neighbor_x = [], []
    for issue,turbine in zip(data['issues'],data['turbines']):
        stamp = pd.Timestamp(issue,tz='UTC').tz_convert(ZONE)
        sequence.append(sequence_at(data['frames'],stamp,int(turbine),neighbor=True))
        neighbor_x.append(features(data['frames'][1-int(turbine)],stamp)[0])
    data['sequence'] = np.asarray(sequence)
    data['neighbor_x'] = np.concatenate([data['x'],np.asarray(neighbor_x)],axis=1)
    data['calendar'] = data['x'][:,-3:].astype(np.float32)
    data['source_sha256'] = json.loads((experiment/'report.json').read_text())['source_sha256']
    joblib.dump(data,output,compress=3)


def train_mask(issues, targets, cutoff):
    return (issues+48*HOUR_NS<=cutoff) & np.isfinite(targets).all(axis=1)


def score(pred, data):
    valid = data['issues'][:,None]+np.arange(1,49)[None,:]*HOUR_NS
    start = pd.Timestamp('2025-02-01',tz=ZONE).value
    end = pd.Timestamp('2026-02-01',tz=ZONE).value
    mask = (valid>start)&(valid<=end)
    actual = np.where(mask,data['y'],np.nan)
    overall = metrics(actual,pred)
    months = {}
    for month in pd.date_range('2025-02-01','2026-01-01',freq='MS',tz=ZONE):
        use = (valid>month.value)&(valid<=(month+pd.offsets.MonthBegin()).value)
        months[month.strftime('%Y-%m')] = metrics(np.where(use,data['y'],np.nan),pred)
    buckets = {label:metrics(actual[:,lo:hi],pred[:,lo:hi]) for label,lo,hi in [('1-24',0,24),('25-48',24,48)]}
    return {'overall':overall,'months':months,'lead_buckets':buckets}


def history_values(data, index, hours=720, columns=('power',)):
    issue = pd.Timestamp(data['issues'][index],tz='UTC').tz_convert(ZONE)
    frame = data['frames'][int(data['turbines'][index])]
    past = frame.reindex(pd.date_range(end=issue,periods=hours,freq='h'))[list(columns)]
    # Missing historical inputs only; targets always remain missing.
    return past.ffill().fillna(past.mean()).fillna(0).to_numpy(dtype=np.float32)


def forecast_foundation(pipeline, data, indices, bolt=False):
    import torch
    if bolt:
        context = torch.as_tensor(np.stack([history_values(data,i)[:,0] for i in indices]))
        quantiles, mean = pipeline.predict_quantiles(context,prediction_length=48,quantile_levels=[.1,.5,.9])
        return mean.numpy(),quantiles[:,:,0].numpy(),quantiles[:,:,2].numpy()
    # cross_learning=False isolates forecast vintages; each task has its own
    # power target plus wind/temperature history available at that issue only.
    tasks = []
    for i in indices:
        h = history_values(data,i,hours=168,columns=('power','wind','temperature'))
        tasks.append({'target':h[:,0], 'past_covariates':{'wind':h[:,1],'temperature':h[:,2]}})
    quantiles, means = pipeline.predict_quantiles(tasks,prediction_length=48,
        quantile_levels=[.1,.5,.9],context_length=168,batch_size=24,cross_learning=False)
    return (np.stack([m[0].numpy() for m in means]),
            np.stack([q[0,:,0].numpy() for q in quantiles]),
            np.stack([q[0,:,2].numpy() for q in quantiles]))


def run(args):
    args.output.mkdir(parents=True,exist_ok=False)
    began = time.perf_counter()
    data = joblib.load(args.dataset)
    x,y,issues = data['x'],data['y'],data['issues']
    pred,low,high = [np.full_like(y,np.nan) for _ in range(3)]
    fits,failures = [],[]
    name = args.model
    foundation = name.startswith('chronos')
    pipeline = None
    if foundation:
        import torch
        from chronos import BaseChronosPipeline
        torch.set_num_threads(2)
        path = args.pretrained/('bolt-tiny' if name=='chronos_bolt_tiny' else 'chronos2-base' if name.startswith('chronos2_base') else 'chronos2-small')
        pipeline = BaseChronosPipeline.from_pretrained(str(path),device_map=args.device,torch_dtype=torch.float32)
    for month in pd.date_range(args.first_month,args.last_month,freq='MS',tz=ZONE):
        cutoff = month+pd.Timedelta(hours=6)
        next_cutoff = month+pd.offsets.MonthBegin()+pd.Timedelta(hours=6)
        train = train_mask(issues,y,cutoff.value)
        use = np.where((issues>=cutoff.value)&(issues<next_cutoff.value))[0]
        if not len(use):
            continue
        started = time.perf_counter()
        info = {'issue_month':month.strftime('%Y-%m'),'training_cutoff':cutoff.isoformat(),
                'latest_training_target':pd.Timestamp((issues[train]+48*HOUR_NS).max(),tz='UTC').isoformat(),
                'training_origins':int(train.sum()),'forecast_origins':len(use)}
        model = None
        with threadpool_limits(limits=2):
            if name.startswith('catboost'):
                from catboost import CatBoostRegressor
                xx = data['neighbor_x'] if name=='catboost_neighbor' else x
                params = dict(iterations=200,depth=6 if name=='catboost_multi6' else 4,
                    border_count=32,learning_rate=.04,l2_leaf_reg=10,thread_count=2,random_seed=42,
                    allow_writing_files=False,verbose=False)
                if args.cat_iterations:
                    params['iterations'] = args.cat_iterations
                if args.cat_depth:
                    params['depth'] = args.cat_depth
                if args.device=='cuda':
                    params.update(task_type='GPU',devices='0',boosting_type='Plain')
                model = CatBoostRegressor(loss_function='RMSE' if name=='catboost_pooled' else 'MultiRMSE',**params)
                info['hyperparameters'] = params
                model.fit(expanded_features(xx[train]) if name=='catboost_pooled' else xx[train],
                          y[train].reshape(-1) if name=='catboost_pooled' else y[train])
                info['fit_seconds'] = time.perf_counter()-started
                pp = lambda indices: model.predict(expanded_features(xx[indices]) if name=='catboost_pooled' else xx[indices]).reshape(-1,48)
                tick = time.perf_counter(); pred[use] = pp(use)
                info['predict_all_seconds'] = time.perf_counter()-tick
                tick = time.perf_counter(); pp(use[:2])
                info['daily_model_seconds'] = time.perf_counter()-tick
                if month.strftime('%Y-%m')==args.last_month:
                    model.save_model(str(args.output/'last_model.cbm'))
            elif name in ('gru','lstm','patch_transformer','graph_gru'):
                import torch
                from wind_forecast.models.sequence_forecast import fit_sequence,predict_sequence
                torch.set_num_threads(2)
                seq = data['sequence'] if name=='graph_gru' else data['sequence'][:,:1]
                model, details = fit_sequence(name,seq,data['calendar'],y,train,issues,cutoff.value,
                                              epochs=args.epochs,device=args.device,width=args.width)
                info.update(details)
                tick = time.perf_counter()
                pred[use] = predict_sequence(model,seq[use],data['calendar'][use],args.device)
                info['predict_all_seconds'] = time.perf_counter()-tick
                tick = time.perf_counter()
                predict_sequence(model,seq[use[:2]],data['calendar'][use[:2]],args.device)
                if args.device=='mps': torch.mps.synchronize()
                if args.device=='cuda': torch.cuda.synchronize()
                info['daily_model_seconds'] = time.perf_counter()-tick
                if month.strftime('%Y-%m')==args.last_month:
                    torch.save(model.cpu().state_dict(),args.output/'last_model.pt')
            elif foundation:
                active_pipeline = pipeline
                if name.endswith('_lora'):
                    import peft  # Fail explicitly instead of silently falling back to full tuning.
                    tasks = []
                    for frame in data['frames']:
                        history = frame.loc[:cutoff,['power','wind','temperature']].tail(180*24)
                        tasks.append({'target':history.power.to_numpy(dtype=np.float32),
                                      'past_covariates':{'wind':history.wind.to_numpy(dtype=np.float32),
                                                         'temperature':history.temperature.to_numpy(dtype=np.float32)}})
                    active_pipeline = pipeline.fit(tasks,prediction_length=48,context_length=168,
                        finetune_mode='lora',num_steps=args.lora_steps,batch_size=args.lora_batch,learning_rate=args.lora_lr,
                        output_dir=args.output/month.strftime('%Y-%m'),optim='adamw_torch',
                        report_to='none',disable_tqdm=True,logging_steps=args.lora_steps,
                        seed=42,data_seed=42)
                    info['lora_steps'] = args.lora_steps
                    info['parameters'] = sum(p.numel() for p in active_pipeline.model.parameters())
                    info['trainable_parameters'] = sum(p.numel() for p in active_pipeline.model.parameters() if p.requires_grad)
                info['fit_seconds'] = time.perf_counter()-started
                tick = time.perf_counter()
                pred[use],low[use],high[use] = forecast_foundation(active_pipeline,data,use,name=='chronos_bolt_tiny')
                info['predict_all_seconds'] = time.perf_counter()-tick
                tick = time.perf_counter()
                forecast_foundation(active_pipeline,data,use[:2],name=='chronos_bolt_tiny')
                info['daily_model_seconds'] = time.perf_counter()-tick
                if active_pipeline is not pipeline:
                    del active_pipeline
            else:
                from statsforecast.models import ARIMA,AutoARIMA
                durations = []
                for i in use:
                    tick = time.perf_counter()
                    history = history_values(data,i)[:,0].astype(float)
                    if name=='autoarima':
                        model = AutoARIMA(max_p=3,max_q=2,max_d=1,seasonal=False,
                                          nmodels=12,approximation=True)
                    elif name=='sarima_101':
                        model = ARIMA(order=(1,0,1),seasonal_order=(1,0,0),season_length=24)
                    else:
                        model = ARIMA(order=(2,0,1))
                    try:
                        values = model.forecast(y=history,h=48,level=[80])
                        if not np.isfinite(values['mean']).all():
                            raise ValueError('non-finite forecast')
                        pred[i] = values['mean']
                        low[i],high[i] = values['lo-80'],values['hi-80']
                    except (ValueError,RuntimeError,np.linalg.LinAlgError) as error:
                        failures.append({'index':int(i),'error':type(error).__name__})
                    durations.append(time.perf_counter()-tick)
                info['fit_seconds'] = sum(durations)
                info['daily_model_seconds'] = float(np.quantile(durations,.95)*2)
                info['fit_policy'] = 'refit on preceding 720 hours at EACH daily issue'
        info['fit_predict_seconds'] = time.perf_counter()-started
        fits.append(info)
        np.savez_compressed(args.output/'predictions.npz',prediction=pred,low=low,high=high,
                            issues=issues,turbines=data['turbines'],actual=y)
        report = {'model':args.label or name,'architecture':name,**score(pred,data),'fits':fits,'failures':failures,
                  'device':args.device if foundation or name.startswith('catboost') or name in ('gru','lstm','patch_transformer','graph_gru') else 'cpu',
                  'source_sha256':data['source_sha256'],
                  'elapsed_seconds':time.perf_counter()-began,
                  'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if platform.system()=='Darwin' else 1024),
                  'pretrained_hindsight':foundation,'complete':month.strftime('%Y-%m')==args.last_month,
                  'assumptions': ['UTC+5; 06:00 issues; source timestamps assumed interval starts.',
                    'Hourly target needs six complete ten-minute source records; missing targets not imputed.',
                    'Monthly training cutoff includes only fully matured 48-hour targets.',
                    'Exploratory retrospective comparison; earlier results have already been inspected.',
                    'Foundation weights released after part of replay; not an as-of historical claim.',
                    'Cross-task foundation attention disabled; no attention across future issue contexts.',
                    'Model timings exclude cold start, raw CSV preparation, provenance audit and formatting.']}
        (args.output/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'model':name,**info,'rmse_so_far':report['overall']['rmse']}),flush=True)
        del model
        gc.collect()
        if time.perf_counter()-began>args.max_seconds:
            print('Time cap reached after completed monthly fold.',flush=True)
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',type=Path)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--model',choices=MODELS)
    parser.add_argument('--label')
    parser.add_argument('--device',choices=['cpu','mps','cuda'],default='cpu')
    parser.add_argument('--pretrained',type=Path,default=Path('src/wind_forecast/models/artifacts/pretrained'))
    parser.add_argument('--first-month',default='2025-01-01')
    parser.add_argument('--last-month',default='2026-01')
    parser.add_argument('--epochs',type=int,default=16)
    parser.add_argument('--width',type=int,default=24)
    parser.add_argument('--cat-iterations',type=int)
    parser.add_argument('--cat-depth',type=int)
    parser.add_argument('--lora-steps',type=int,default=40)
    parser.add_argument('--lora-batch',type=int,default=8)
    parser.add_argument('--lora-lr',type=float,default=1e-5)
    parser.add_argument('--max-seconds',type=int,default=1200)
    args = parser.parse_args()
    if args.prepare:
        if args.dataset.exists():
            parser.error('dataset already exists')
        prepare(args.prepare,args.dataset)
    else:
        if args.output is None or args.model is None:
            parser.error('--output and --model required')
        run(args)


if __name__=='__main__':
    main()
