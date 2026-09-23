"""CPU-only daily history forecasting helpers; no future weather is required."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

COLUMNS = {
    'Средняя скорость ветра(m/s)': 'wind',
    'Нормализованная активная мощность': 'power',
    'Средняя температура окружающей среды(°C)': 'temperature',
}


def load_hourly(path):
    """Assume source labels interval starts; hourly target is mean of six full bins."""
    raw = pd.read_csv(path)
    stamp = pd.to_datetime(raw['Статистическое время'], format='mixed')
    if ((stamp.dt.minute % 10 != 0) | (stamp.dt.second != 0) | (stamp.dt.microsecond != 0)).any():
        raise ValueError('source timestamps must align to ten-minute intervals')
    if stamp.duplicated().any():
        raise ValueError('duplicate source times')
    frame = raw.rename(columns=COLUMNS)[list(COLUMNS.values())].apply(pd.to_numeric)
    frame.index = pd.DatetimeIndex(stamp).tz_localize('Etc/GMT-5')
    frame = frame.sort_index().replace([np.inf, -np.inf], np.nan)
    frame.loc[frame.wind < 0, 'wind'] = np.nan
    # 05:00..05:50 are available at 06:00, target labeled by interval END.
    grouped = frame.resample('1h', closed='left', label='right')
    hourly = grouped.mean().where(grouped.count() == 6)
    return hourly, hashlib.sha256(Path(path).read_bytes()).hexdigest()


def features(frame, issue, days=30):
    """Strictly backward-looking; changing any future row cannot change features."""
    issue = pd.Timestamp(issue)
    past = frame.reindex(pd.date_range(issue - pd.Timedelta(days=days) + pd.Timedelta(hours=1),
                                       issue, freq='1h'))
    values, names = [], []
    for column in ('power', 'wind', 'temperature'):
        series = past[column]
        for lag in [*range(24), 47, 71, 167]:
            values.append(series.iloc[-1-lag] if lag < len(series) else np.nan)
            names.append(f'{column}_lag_{lag}h')
        for hours in (6, 24, 72, 168, 720):
            if hours > days * 24:
                continue
            part = series.iloc[-hours:]
            values.extend([part.mean(), part.std(), part.min(), part.max(), part.count()/hours])
            names.extend(f'{column}_{stat}_{hours}h' for stat in ('mean','std','min','max','coverage'))
    angle = 2*np.pi*issue.dayofyear/365.25
    values.extend([np.sin(angle), np.cos(angle)])
    names.extend(['season_sin', 'season_cos'])
    return np.array(values, dtype=float), names


def make_estimator(name):
    if name.startswith('ridge'):
        return make_pipeline(SimpleImputer(add_indicator=True), StandardScaler(), Ridge(alpha=100.0))
    if name.startswith('extra'):
        leaf = 6 if 'deep' in name else 15
        return make_pipeline(SimpleImputer(add_indicator=True), ExtraTreesRegressor(
            n_estimators=160, max_depth=12 if leaf == 6 else 8,
            min_samples_leaf=leaf, max_features=.8, n_jobs=2, random_state=42))
    if name == 'hist_boost':
        return HistGradientBoostingRegressor(max_iter=140, max_leaf_nodes=15,
                                            l2_regularization=10, learning_rate=.05,
                                            early_stopping=False, random_state=42)
    raise ValueError(name)


def expanded_features(x):
    """One pooled regressor across lead times; lead remains an explicit feature."""
    repeated = np.repeat(x, 48, axis=0)
    lead = np.tile(np.arange(1, 49), len(x))
    hour = (6 + lead) % 24
    return np.column_stack([repeated, lead / 48, np.sin(2*np.pi*hour/24), np.cos(2*np.pi*hour/24)])


def predict_estimator(estimator, name, x):
    if name == 'hist_boost':
        return estimator.predict(expanded_features(x)).reshape(-1, 48)
    return estimator.predict(x)


def metrics(actual, prediction):
    valid = np.isfinite(actual) & np.isfinite(prediction)
    error = prediction[valid] - actual[valid]
    if not len(error):
        return {'n': 0, 'mae': None, 'rmse': None}
    return {'n': int(len(error)), 'mae': float(np.mean(np.abs(error))),
            'rmse': float(np.sqrt(np.mean(error**2)))}
