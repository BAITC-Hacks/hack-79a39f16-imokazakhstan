"""Offline SCADA forecast integration; no fabricated weather bundle or network calls."""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import csv
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
import pandas as pd

from wind_forecast.contracts import WorkflowTrace
from wind_forecast.agent.integrations import load_team_predictor
from wind_forecast.agent.settings import parse_time
from wind_forecast.models.history import features

ARTIFACTS = Path(__file__).resolve().parents[1] / 'models' / 'artifacts'


def reject_synthetic_datasets(datasets, mode):
    """Explicit synthetic declarations cannot be overridden by the run mode."""
    synthetic = any(d.report.get('synthetic') is True or d.report.get('is_synthetic') is True
                    or d.report.get('provenance_status') == 'synthetic' for d in datasets.values())
    if mode != 'fixture' and synthetic:
        raise ValueError('Historical/live runs reject explicitly synthetic observation datasets')
    return synthetic


def observation_frames(observations):
    frames=[]
    for turbine in ('T1','T2'):
        rows=[(o.observed_at,o.power_norm,o.wind_ms,o.temp_c) for o in observations if o.turbine_id==turbine]
        f=pd.DataFrame(rows,columns=['time','power','wind','temperature']).set_index('time')
        f.index=pd.to_datetime(f.index,utc=True)
        frames.append(f.sort_index())
    return frames


def predict_wind(request, observations, model_path, metadata_path, training_limit):
    import joblib
    path=Path(model_path);metadata=json.loads(Path(metadata_path).read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest()!=metadata['artifact_sha256']:
        raise ValueError('Wind artifact SHA-256 mismatch')
    if metadata['target_units']!='m/s' or parse_time(metadata['trained_through'])>min(request.issue_time,training_limit):
        raise ValueError('Wind model units or training cutoff are invalid')
    artifact=joblib.load(path)  # Trusted server configuration only, never an uploaded model.
    if parse_time(artifact['trained_through'])!=parse_time(metadata['trained_through']):
        raise ValueError('Wind artifact cutoff does not match metadata')
    frames=observation_frames(observations);issue=pd.Timestamp(request.issue_time).tz_convert('Etc/GMT-5')
    x=np.array([np.r_[features(frames[int(t[1:])-1],issue)[0],int(t[1:])-1] for t in request.turbine_ids])
    prediction=artifact['model'].predict(x)
    if prediction.shape!=(len(request.turbine_ids),48) or not np.isfinite(prediction).all() or (prediction<0).any():
        raise ValueError('Wind prediction is invalid')
    return [{'turbine_id':t,'issue_time':request.issue_time.isoformat(),
             'valid_time':(request.issue_time+timedelta(hours=h+1)).isoformat(),
             'lead_hours':h+1,'wind_ms':float(prediction[i,h])}
            for i,t in enumerate(request.turbine_ids) for h in range(request.horizon_hours)],metadata


def run_local_forecast(config, datasets=None):
    from wind_forecast.agent.application import ApplicationRun, load_datasets, _validate_observations, _write, _safe_error, _evaluate
    request=config.request();trace=WorkflowTrace(request.request_id)
    directory=Path(config.output_root)/f'local-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}'
    directory.mkdir(parents=True,exist_ok=False)
    report={'weather':{'required':False,'provider':'none','note':'History-only wind and power models'},
            'assumptions':{'source_timezone':config.source_timezone,'interval_label':config.interval_label,
            'reporting_delay_minutes':config.reporting_delay_minutes,'units':'normalized_active_power',
            'operator_acknowledged':config.assumptions_confirmed},'controller':{'used':'deterministic'}}
    def record(step,detail):trace.events.append({'step':step,'status':'ok','detail':detail})
    try:
        if config.mode=='fixture':raise ValueError('Use the normal offline fixture path for synthetic data')
        if not config.assumptions_confirmed:raise ValueError('Confirm source timestamp assumptions')
        if config.source_timezone!='Etc/GMT-5' or config.interval_label!='start':
            raise ValueError('Saved models require the trained UTC+5 / interval-start CSV convention')
        # The selected CatBoost uses its neighbour even when only one turbine is displayed.
        datasets=datasets if datasets is not None else load_datasets(replace(config,turbine_ids=('T1','T2')))
        if set(datasets)!={'T1','T2'}:raise ValueError('Local model requires both T1 and T2 histories, including the neighbour')
        reject_synthetic_datasets(datasets,config.mode)
        all_rows=[o for d in datasets.values() for o in d.observations];_validate_observations(all_rows,('T1','T2'))
        observations=[o for o in all_rows if o.observed_at<=config.training_limit and o.available_at<=request.issue_time and o.quality_flag=='ok']
        context=[o for o in observations if o.observed_at>request.issue_time-timedelta(days=30)]
        report['datasets']={t:d.report for t,d in datasets.items()}
        report['observations']={'eligible':len(context),'latest_hour_end':max(o.observed_at for o in context).isoformat() if context else None}
        report['provenance']={'observations':'local_scada','trained_on_synthetic':False,'timestamp_convention':'operator acknowledged; not independently attested'}
        record('prepare_data',report['observations'])
        model_path=config.model_path or str(ARTIFACTS/'deploy-final/catboost_neighbor.cbm')
        metadata_path=config.model_metadata_path or str(ARTIFACTS/'deploy-final/catboost_neighbor.metadata.json')
        predictor,metadata=load_team_predictor('wind_forecast.models.deployment:load_predictor',model_path,metadata_path,
            issue_time=request.issue_time,training_limit=config.training_limit,mode=config.mode)
        record('load_model',{'model_id':predictor.model_id,'sha256':metadata['artifact_sha256']})
        result=predictor.predict(request,context,None)
        expected={(t,request.issue_time+timedelta(hours=h)) for t in request.turbine_ids for h in range(1,request.horizon_hours+1)}
        if len(result.rows)!=len(expected) or {(r.turbine_id,r.valid_time) for r in result.rows}!=expected or result.is_synthetic:
            raise ValueError('Local forecast coverage/provenance mismatch')
        for r in result.rows:
            if not np.isfinite(r.prediction) or not 0<=r.prediction<=1:raise ValueError('Invalid power')
            if r.p10 is not None and not 0<=r.p10<=r.prediction<=r.p90<=1:raise ValueError('Invalid interval')
        wind,wind_metadata=predict_wind(request,context,config.wind_model_path or ARTIFACTS/'wind-audit/wind_model.joblib',
            config.wind_metadata_path or ARTIFACTS/'wind-audit/metadata.json',config.training_limit)
        identity={'input':result.input_hash,'power':metadata['artifact_sha256'],'wind':wind_metadata['artifact_sha256'],'issue':request.issue_time.isoformat()}
        result=replace(result,input_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest())
        report.update(model=metadata,wind_model=wind_metadata,wind_forecast=wind,evaluation=_evaluate(result,all_rows))
        report['evaluation']['note']='Post-prediction scoring only; no future weather is required by these history-only models.'
        report['forecast']={'forecast_rows':len(result.rows),'warnings':list(result.warnings),'unit':'normalized_active_power',
            'per_turbine':{t:{'mean':float(np.mean([r.prediction for r in result.rows if r.turbine_id==t])),
            'max':max(r.prediction for r in result.rows if r.turbine_id==t)} for t in request.turbine_ids}}
        report['model_validation']={'state':'previous_monthly_replay','power_rmse':0.3357383793,'wind_rmse_ms':3.47781948,
             'period':'2025-02 through 2026-01','note':'Retrospective monthly replay; not measured accuracy of this new forecast.'}
        record('predict_power_and_wind',{'rows_each':len(result.rows),'horizon':request.horizon_hours})
        with (directory/'forecast.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(asdict(result.rows[0])));writer.writeheader();writer.writerows(asdict(r) for r in result.rows)
        with (directory/'wind.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(wind[0]));writer.writeheader();writer.writerows(wind)
        _write(directory/'manifest.json',{'model':metadata,'wind_model':wind_metadata,'input_hash':result.input_hash,'datasets':report['datasets'],'weather':None,'is_synthetic':False})
        summary=f'Local CPU models: {len(result.rows)} hourly power and wind forecasts. No external weather. Issue {request.issue_time.isoformat()}.'
        run=ApplicationRun('completed',request,result,None,trace,directory,report,summary)
    except Exception as exc:
        error=_safe_error(exc);trace.events.append({'step':'local_forecast','status':'blocked','detail':error})
        run=ApplicationRun('blocked',request,None,None,trace,directory,report,'Local forecast blocked: '+error,error)
    _write(directory/'request.json',config.to_dict());_write(directory/'report.json',report)
    (directory/'trace.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in trace.events))
    (directory/'summary.md').write_text(run.summary);_write(directory/'response.json',run.to_dict())
    return run
