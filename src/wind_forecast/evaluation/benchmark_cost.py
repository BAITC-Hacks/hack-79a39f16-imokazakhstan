"""Local CPU benchmarks and transparent USD estimates, not measured cloud bills."""
from __future__ import annotations

import argparse
import io
import json
import math
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

# AWS public Linux/x86 example, us-east-1, checked 2026-09-23.
CPU_USD_SECOND = .000011244
GIB_USD_SECOND = .000001235
VCPU = 2
GIB = 4
SOURCE = 'https://aws.amazon.com/fargate/pricing/'
MODELS = ['ridge_7d','ridge_30d','extra_shallow','extra_deep','hist_boost',
          'wind_then_lut','wind_then_smooth','wind_tree_scenarios']


def compute_usd(seconds, vcpu=VCPU, gib=GIB):
    if seconds < 0 or vcpu <= 0 or gib <= 0:
        raise ValueError('invalid compute allocation or duration')
    return seconds*(vcpu*CPU_USD_SECOND+gib*GIB_USD_SECOND)


def task_usd(seconds, startup_seconds=5, slowdown=1):
    """Sensitivity assumptions: startup is not a measured Fargate launch time."""
    if startup_seconds < 0 or slowdown <= 0:
        raise ValueError('invalid startup or slowdown')
    if seconds < 0:
        raise ValueError('negative runtime')
    billed=max(60,math.ceil(startup_seconds+slowdown*seconds))
    return compute_usd(billed)


def worker(folder,name):
    import joblib
    import numpy as np
    import pandas as pd
    from scipy.interpolate import PchipInterpolator
    from threadpoolctl import threadpool_limits
    from wind_forecast.models.history import features,make_estimator,expanded_features,predict_estimator

    loading=time.perf_counter()
    data=joblib.load(folder/'experiment_data.joblib')
    load_seconds=time.perf_counter()-loading
    mask=data['final_train'].copy()
    if name.startswith('wind_'):
        mask &= np.isfinite(data['wind']).all(axis=1)
    x,y=data['x'][mask],data['y'][mask]
    names=data['feature_names']
    cols=np.array([i for i,n in enumerate(names) if '_720h' not in n]) if name=='ridge_7d' else np.arange(x.shape[1])
    issue=pd.Timestamp('2026-01-31T06:00:00+05:00')
    x_issue=[]
    started=time.perf_counter()
    for turbine,frame in enumerate(data['frames']):
        row,_=features(frame,issue)
        x_issue.append(np.r_[row,turbine])
    x_issue=np.asarray(x_issue)
    feature_seconds=time.perf_counter()-started
    started=time.perf_counter()
    with threadpool_limits(limits=2):
        curves={}
        if name.startswith('wind_'):
            model=make_estimator('extra_shallow')
            model.fit(x,data['wind'][mask])
            for turbine,frame in enumerate(data['frames']):
                known=frame.dropna(subset=['wind','power'])
                bins=known.groupby(np.floor(known.wind).astype(int)).power.mean()
                curves[turbine]=(bins.index.to_numpy()+.5,bins.to_numpy())
        else:
            model=make_estimator(name)
            if name=='hist_boost':
                model.fit(expanded_features(x[:,cols]),y.reshape(-1))
            else:
                model.fit(x[:,cols],y)
    fit_seconds=time.perf_counter()-started

    def predict():
        if not name.startswith('wind_'):
            return predict_estimator(model,name,x_issue[:,cols])
        if name=='wind_tree_scenarios':
            transformed=model[0].transform(x_issue)
            values=[]
            for tree in model[-1].estimators_:
                wind=tree.predict(transformed)
                power=np.zeros_like(wind)
                for t,(v,p) in curves.items():
                    power[t]=PchipInterpolator(v,p)(np.clip(wind[t],v.min(),v.max()))
                values.append(power)
            return np.mean(values,axis=0)
        wind=model.predict(x_issue)
        power=np.zeros_like(wind)
        for t,(v,p) in curves.items():
            if name=='wind_then_lut':
                keys=v-.5
                nearest=np.argmin(np.abs(np.floor(wind[t])[:,None]-keys),axis=1)
                power[t]=p[nearest]
            else:
                power[t]=PchipInterpolator(v,p)(np.clip(wind[t],v.min(),v.max()))
        return power

    samples=[]
    with threadpool_limits(limits=2):
        predict()
        for _ in range(20):
            started=time.perf_counter();result=predict();samples.append(time.perf_counter()-started)
    assert result.shape==(2,48) and np.isfinite(result).all()
    serialized=io.BytesIO()
    started=time.perf_counter()
    joblib.dump({'model':model,'curves':curves,'columns':cols},serialized,compress=3)
    save_seconds=time.perf_counter()-started
    serialized.seek(0)
    started=time.perf_counter();joblib.load(serialized);model_load_seconds=time.perf_counter()-started
    return {'name':name,'training_origins':int(mask.sum()),'cached_dataset_load_seconds':load_seconds,
            'fit_seconds':fit_seconds,'save_seconds':save_seconds,'artifact_load_seconds':model_load_seconds,
            'feature_seconds_two_turbines':feature_seconds,
            'predict_seconds_median':float(np.median(samples)),'predict_seconds_p95':float(np.quantile(samples,.95)),
            'artifact_bytes':len(serialized.getvalue()),
            'peak_process_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if platform.system()=='Darwin' else 1024)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--worker',choices=MODELS)
    args=parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.experiment,args.worker)))
        return
    if args.output is None:
        parser.error('--output required')
    args.output.mkdir(parents=True,exist_ok=False)
    old=json.loads((args.experiment/'report.json').read_text())
    benchmarks=[]
    env=dict(os.environ,OPENBLAS_NUM_THREADS='2',OMP_NUM_THREADS='2',PYTHONDONTWRITEBYTECODE='1')
    for name in MODELS:
        command=[sys.executable,'-m','wind_forecast.evaluation.benchmark_cost','--experiment',str(args.experiment),'--worker',name]
        started=time.perf_counter()
        child=subprocess.run(command,env=env,capture_output=True,text=True,check=True,timeout=120)
        item=json.loads(child.stdout)
        item['whole_benchmark_process_seconds']=time.perf_counter()-started
        # CPU microbenchmarks use prepared data; end-to-end scheduling is an assumption.
        warm=item['feature_seconds_two_turbines']+item['predict_seconds_median']
        train=item['cached_dataset_load_seconds']+item['fit_seconds']+item['save_seconds']
        # 2s input/preprocessing overhead per forecast; 10s raw training preparation.
        forecast_job=2+item['artifact_load_seconds']+warm
        training_job=10+train
        item['usd']={
            'model_compute_one_forecast':compute_usd(warm),
            'model_compute_one_fit':compute_usd(item['fit_seconds']),
            'model_compute_month_30_forecasts_1_fit':compute_usd(30*warm+item['fit_seconds']),
            'scheduled_forecast_job':task_usd(forecast_job),
            'scheduled_training_job':task_usd(training_job),
            'scheduled_month_30_forecasts_1_fit':30*task_usd(forecast_job)+task_usd(training_job),
            'scheduled_month_5x_runtime':30*task_usd(forecast_job,slowdown=5)+task_usd(training_job,slowdown=5),
            'scheduled_month_30_forecasts_30_fits':30*(task_usd(forecast_job)+task_usd(training_job)),
        }
        item['reference_control_rmse']=old['metrics'][name]['control']['all']['rmse']
        benchmarks.append(item)
        print(name,'fit',round(item['fit_seconds'],3),'predict',round(warm,5),
              'USD/month',round(item['usd']['scheduled_month_30_forecasts_1_fit'],4),flush=True)
    report={'currency':'USD','price_checked_date':'2026-09-23','price_source':SOURCE,
            'provider':'AWS Fargate Linux/x86 us-east-1 on-demand; illustrative equivalent allocation',
            'allocated_vcpu':VCPU,'allocated_gib':GIB,'cpu_usd_per_vcpu_second':CPU_USD_SECOND,
            'memory_usd_per_gib_second':GIB_USD_SECOND,'billing_minimum_seconds_per_task':60,
            'assumptions':{
                'forecasts_per_30_day_month':30,'retraining_per_month':1,'turbines':2,'hours_per_forecast':48,
                'startup_seconds_per_task':5,'forecast_input_preparation_seconds':2,
                'training_input_preparation_seconds':10,'cloud_slowdown_base':1,'cloud_slowdown_sensitivity':5,
            },
            'benchmarks':benchmarks,'constant_24_7_allocation_30_day_compute_usd':compute_usd(30*24*3600),
            'machine':{'system':platform.platform(),'processor':platform.machine(),'logical_cpus':os.cpu_count(),
                       'python':platform.python_version(),'thread_limit':2},
            'limitations':[
                'Local wall-time measurements multiplied by public cloud rates are estimates, not an AWS benchmark or bill.',
                'Measured peak RSS includes imports, cached data and benchmark harness, not isolated model-only RAM.',
                'All models use the same 2-vCPU/4-GiB costing allocation; this is not a claim that all need that RAM.',
                'Warm forecast timing includes feature extraction and model calculation, excludes CSV I/O, process startup, hashing and protocol formatting.',
                'End-to-end job estimates add explicit assumed overheads and 60-second billing minimum; image download is not measured.',
                'Reference accuracy is December-January from the earlier experiment; cost benchmark refits on all eligible data and does not rescore quality.',
                'Excludes storage, logs, network, public IPv4/NAT, scheduler, UI hosting, weather/API fees, engineering labor and taxes.',
                'No paid resources were provisioned or used; no credits, discounts or free tiers assumed.',
            ]}
    (args.output/'costs.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    import csv
    with (args.output/'costs.csv').open('w',newline='') as stream:
        flattened=[{k:v for k,v in b.items() if k!='usd'}|{'usd_'+k:v for k,v in b['usd'].items()} for b in benchmarks]
        writer=csv.DictWriter(stream,fieldnames=list(flattened[0]));writer.writeheader();writer.writerows(flattened)
    lines=['# Estimated compute cost\n',
           'Two turbines, daily 48-hour forecast, 30 forecasts and one retraining per month. CPU-only.\n',
           f'Prices: [AWS Fargate]({SOURCE}), checked 2026-09-23; 2 vCPU + 4 GiB. Local timings, not a cloud benchmark.\n\n',
           '| Model | Fit, s | Warm forecast + features, s | Peak process MiB | Artifact MB | Model compute/month $ | Scheduled tasks/month $ | 5× runtime/month $ |\n',
           '|---|---:|---:|---:|---:|---:|---:|---:|\n']
    for b in benchmarks:
        u=b['usd'];warm=b['feature_seconds_two_turbines']+b['predict_seconds_median']
        lines.append(f"| {b['name']} | {b['fit_seconds']:.3f} | {warm:.5f} | {b['peak_process_rss_mib']:.0f} | {b['artifact_bytes']/1e6:.2f} | {u['model_compute_month_30_forecasts_1_fit']:.6f} | {u['scheduled_month_30_forecasts_1_fit']:.4f} | {u['scheduled_month_5x_runtime']:.4f} |\n")
    lines.append(f"\nKeeping the same allocation continuously running for 30 days costs approximately **${report['constant_24_7_allocation_30_day_compute_usd']:.2f}** in compute alone.\n")
    lines.append('\nThe scheduled-job estimate includes a 60-second minimum per task, assumed 5-second startup, 2-second forecast input preparation and 10-second training preparation. Costs exclude storage, networking, logs, UI hosting, paid weather APIs, taxes and engineering. Raw compute costs are useful for comparing algorithms but are not the total service bill.\n')
    lines.append('\nPeak memory includes imports, cached data, training and the benchmark harness; it is not the minimum inference RAM. Warm timing covers prepared-data feature extraction and numerical prediction, excluding CSV reads, startup, provenance hashing and output formatting. All candidates are costed at the same allocation.\n')
    (args.output/'costs.md').write_text(''.join(lines),encoding='utf-8')
    print(args.output/'costs.md',flush=True)


if __name__=='__main__':
    main()
