"""Saved, daily 48-hour predictor: forecast wind scenarios, then map to power."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

from wind_forecast.contracts import ForecastResult, ForecastRow
from wind_forecast.models.history import features


class HistoryPowerPredictor:
    """SCADA-only predictor. External weather is not consumed by this model."""

    model_id = 'history-wind-scenarios-v1'

    def __init__(self, artifact):
        if artifact.get('schema_version') != 1 or artifact.get('model_id') != self.model_id:
            raise ValueError('unsupported history model artifact')
        self.artifact = artifact
        self.model = artifact['wind_model']
        self.curves = artifact['curves']
        self.metadata = artifact['metadata']

    @classmethod
    def load(cls, path: str | Path):
        """Load only trusted local artifacts: joblib is a Python serialization format."""
        return cls(joblib.load(path))

    def predict(self, request, observations, weather=None):
        del weather  # Protocol compatibility: this predictor uses SCADA history only.
        cutoff = datetime.fromisoformat(self.metadata['training_available_through'])
        if cutoff > request.issue_time:
            raise ValueError('model training data were unavailable at issue_time')
        local_issue = pd.Timestamp(request.issue_time).tz_convert('Etc/GMT-5')
        if local_issue.hour != 6 or local_issue.minute != 0:
            raise ValueError('history model expects a daily issue at 06:00 UTC+5')
        if not observations:
            raise ValueError('historical observations are required')
        for row in observations:
            if row.observed_at+timedelta(minutes=10) > request.issue_time or row.available_at > request.issue_time:
                raise ValueError('observation interval or availability is after issue_time')
        observations = [row for row in observations if row.observed_at >= request.issue_time-timedelta(days=30)]
        vectors, warnings = [], [
            'History-only forecast; no numerical weather forecast is used.',
            'Empirical interval estimates are not guaranteed to attain 80% coverage.',
            'Source timestamps assumed to mark the start of a ten-minute interval.',
        ]
        for turbine in request.turbine_ids:
            if turbine not in ('T1','T2'):
                raise ValueError('model supports only the two trained turbines T1/T2')
            rows = [row for row in observations if row.turbine_id == turbine]
            if not rows:
                raise ValueError(f'no observations for {turbine}')
            index = pd.DatetimeIndex([row.observed_at for row in rows]).tz_convert('Etc/GMT-5')
            if index.duplicated().any():
                raise ValueError('duplicate observation times')
            raw = pd.DataFrame({'wind':[row.wind_ms for row in rows],
                                'power':[row.power_norm for row in rows],
                                'temperature':[row.temp_c for row in rows]}, index=index).sort_index().apply(pd.to_numeric)
            raw.loc[[row.observed_at for row in rows if row.quality_flag != 'ok']] = np.nan
            raw = raw.replace([np.inf,-np.inf],np.nan)
            raw.loc[raw.wind < 0,'wind'] = np.nan
            grouped=raw.resample('1h',closed='left',label='right')
            hourly=grouped.mean().where(grouped.count()==6)
            recent=hourly.reindex(pd.date_range(local_issue-pd.Timedelta(hours=23),local_issue,freq='1h'))
            if recent.power.count() < 18:
                raise ValueError(f'{turbine}: fewer than 18 complete hours in last 24 hours')
            if hourly.power.dropna().index[-1] < local_issue:
                warnings.append(f'{turbine}: latest complete hourly power is before issue_time.')
            vec,names=features(hourly,local_issue)
            if names+['turbine'] != self.artifact['feature_names']:
                raise ValueError('feature schema differs from model artifact')
            vectors.append(np.r_[vec,int(turbine[1:])-1])
        x=np.asarray(vectors)
        transformed=self.model[0].transform(x)
        power=[]
        for tree in self.model[-1].estimators_:
            winds=tree.predict(transformed)
            values=np.zeros_like(winds)
            for i,turbine in enumerate(request.turbine_ids):
                curve=self.curves[int(turbine[1:])-1]
                spline=PchipInterpolator(curve['wind'],curve['power'])
                values[i]=spline(np.clip(winds[i],min(curve['wind']),max(curve['wind'])))
            power.append(values)
        prediction=np.mean(power,axis=0)
        output=[]
        for i,turbine in enumerate(request.turbine_ids):
            for h in range(request.horizon_hours):
                bucket='1-24' if h<24 else '25-48'
                a,b=self.artifact['interval_offsets'][f'{turbine}_{bucket}']
                output.append(ForecastRow(turbine,request.issue_time,
                                         request.issue_time+timedelta(hours=h+1),h+1,
                                         float(prediction[i,h]),
                                         p10=float(prediction[i,h]+a),
                                         p90=float(prediction[i,h]+b)))
        content=json.dumps({'request':asdict(request),'observations':[asdict(r) for r in observations],
                            'training':self.metadata},default=str,sort_keys=True,allow_nan=False)
        return ForecastResult(
            forecast_id=f'history-{request.request_id}',request_id=request.request_id,
            schema_version='1.0',model_id=self.model_id,weather_bundle_id='not-used-history-only',
            input_hash=hashlib.sha256(content.encode()).hexdigest(),created_at=datetime.now(timezone.utc),
            status='degraded',is_synthetic=False,warnings=tuple(warnings),rows=tuple(output))
