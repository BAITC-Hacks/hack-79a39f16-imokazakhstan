"""Bounded, chronological 48-hour power forecast experiment using only SCADA history."""
from __future__ import annotations

import argparse
import json
import platform
import resource
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy.interpolate import PchipInterpolator
from threadpoolctl import threadpool_limits

from wind_forecast.models.history import (
    load_hourly, features, make_estimator, expanded_features, predict_estimator, metrics,
)


def grouped_metrics(y, pred, turbines):
    result = {'all': metrics(y, pred)}
    for turbine in (0, 1):
        mask = turbines == turbine
        result[f'T{turbine+1}'] = metrics(y[mask], pred[mask])
        for start, stop in ((0, 24), (24, 48)):
            result[f'T{turbine+1}_{start+1}-{stop}h'] = metrics(y[mask, start:stop], pred[mask, start:stop])
    return result


def causal_correction(pred, actual, issues, turbines, window):
    """Use only matured errors; overlapping daily forecast vintages retain their leads."""
    correction = np.zeros_like(pred)
    valid_ns = issues[:, None] + np.arange(1, 49)[None, :] * 3600_000_000_000
    for i in range(len(pred)):
        for lo, hi in ((0, 24), (24, 48)):
            eligible = ((turbines == turbines[i])[:, None]
                        & (issues < issues[i])[:, None]
                        & (valid_ns <= issues[i])
                        & (valid_ns > issues[i] - window * 24 * 3600_000_000_000))
            eligible[:, :lo] = False
            eligible[:, hi:] = False
            err = (actual - pred)[eligible]
            err = err[np.isfinite(err)]
            if len(err) >= 24:
                correction[i, lo:hi] = np.mean(err)
    return correction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--turbine-1', type=Path, required=True)
    parser.add_argument('--turbine-2', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    frames, hashes = [], []
    for path in (args.turbine_1, args.turbine_2):
        frame, digest = load_hourly(path)
        frames.append(frame)
        hashes.append(digest)
    zone = 'Etc/GMT-5'
    training_cutoff = pd.Timestamp('2025-09-01 06:00', tz=zone)
    selection_start = pd.Timestamp('2025-10-01 06:00', tz=zone)
    control_start = pd.Timestamp('2025-12-01 06:00', tz=zone)
    observations_end = min(frame.index.max() for frame in frames)
    first = max(frame.index.min() for frame in frames).normalize() + pd.Timedelta(days=30, hours=6)
    issue_dates = pd.date_range(first, observations_end, freq='24h')
    xx, yy, ww, tt, ii, last, daily, weekly = [], [], [], [], [], [], [], []
    for issue in issue_dates:
        for turbine, frame in enumerate(frames):
            x, feature_names = features(frame, issue)
            past = frame.loc[:issue]
            if past.power.tail(24).count() < 18:
                continue
            future = frame.reindex(pd.date_range(issue + pd.Timedelta(hours=1), periods=48, freq='1h'))
            xx.append(np.r_[x, turbine])
            yy.append(future.power.to_numpy())
            ww.append(future.wind.to_numpy())
            tt.append(turbine)
            ii.append(issue.value)
            last.append(np.repeat(past.power.dropna().iloc[-1], 48))
            # Repeat the last known 24-hour profile, with a causal mean for missing bins.
            profile = frame.reindex(pd.date_range(issue-pd.Timedelta(hours=23), issue, freq='1h')).power
            profile = profile.fillna(past.power.tail(168).mean()).to_numpy()
            daily.append(np.tile(profile, 2))
            weekly.append(np.repeat(past.power.tail(168).mean(), 48))
    x, y, wind = np.asarray(xx), np.asarray(yy), np.asarray(ww)
    turbines, issues = np.asarray(tt), np.asarray(ii, dtype=np.int64)
    last_target = issues + 48*3600_000_000_000
    train = (last_target <= training_cutoff.value) & np.isfinite(y).all(axis=1)
    dev = (issues >= selection_start.value) & (last_target <= control_start.value)
    control = issues >= control_start.value
    # Fully prior targets only; late November issues are left out of model selection.
    warmup = (issues >= training_cutoff.value) & (last_target <= selection_start.value)
    active = warmup | dev | control
    predictions = {'persistence': np.asarray(last), 'daily_profile': np.asarray(daily),
                   'weekly_mean': np.asarray(weekly)}
    means = {t: float(frame.loc[:training_cutoff].power.mean()) for t,frame in enumerate(frames)}
    predictions['training_mean'] = np.array([np.repeat(means[t],48) for t in turbines])
    timing, fitted, input_dims = {}, {}, {}
    print(json.dumps({'training_origins': int(train.sum()), 'selection_origins': int(dev.sum()),
                      'control_origins': int(control.sum()), 'features': x.shape[1]}), flush=True)
    # All models trained once before September, never on the control period.
    configs = ['ridge_7d', 'ridge_30d', 'extra_shallow', 'extra_deep', 'hist_boost']
    seven = np.array([i for i, name in enumerate(feature_names+['turbine']) if '_720h' not in name])
    with threadpool_limits(limits=2):
        for name in configs:
            cols = seven if name == 'ridge_7d' else np.arange(x.shape[1])
            model = make_estimator(name)
            began = time.perf_counter()
            if name == 'hist_boost':
                model.fit(expanded_features(x[train][:, cols]), y[train].reshape(-1))
            else:
                model.fit(x[train][:, cols], y[train])
            fit_seconds = time.perf_counter() - began
            pred = np.full_like(y, np.nan)
            pred[active] = predict_estimator(model, name, x[active][:, cols])
            predictions[name] = pred
            fitted[name], input_dims[name] = model, cols
            began = time.perf_counter()
            for _ in range(5):
                predict_estimator(model, name, x[active][:2, cols])
            timing[name] = {'fit_seconds': fit_seconds,
                            'predict_two_turbines_seconds': (time.perf_counter()-began)/5}
            print(name, metrics(y[dev], pred[dev]), timing[name], flush=True)
        # Indirect hypothesis: forecast wind first, then map through training-only curve.
        wind_train = train & np.isfinite(wind).all(axis=1)
        wm = make_estimator('extra_shallow')
        began = time.perf_counter()
        wm.fit(x[wind_train], wind[wind_train])
        predicted_wind = wm.predict(x[active])
        mapped_lut = np.full_like(y, np.nan)
        mapped_smooth = np.full_like(y, np.nan)
        mapped_scenarios = np.full_like(y, np.nan)
        curves = {}
        active_t = turbines[active]
        for turbine, frame in enumerate(frames):
            rows = frame.loc[frame.index <= training_cutoff].dropna(subset=['wind', 'power'])
            bins = rows.groupby(np.floor(rows.wind).astype(int)).power.mean()
            idx = np.argmin(np.abs(np.floor(predicted_wind[active_t == turbine])[...,None] - bins.index.to_numpy()), axis=-1)
            mapped_lut[np.where(active)[0][active_t == turbine]] = bins.to_numpy()[idx]
            curve = PchipInterpolator(bins.index.to_numpy()+.5, bins.to_numpy(), extrapolate=False)
            curves[turbine] = {'wind': (bins.index.to_numpy()+.5).tolist(), 'power': bins.to_numpy().tolist()}
            speeds = np.clip(predicted_wind[active_t == turbine], bins.index.min()+.5, bins.index.max()+.5)
            mapped_smooth[np.where(active)[0][active_t == turbine]] = curve(speeds)
        predictions['wind_then_lut'], predictions['wind_then_smooth'] = mapped_lut, mapped_smooth
        # Tree variability is a cheap scenario approximation, NOT calibrated weather uncertainty.
        transformed = wm[0].transform(x[active])
        tree_power = []
        for tree in wm[-1].estimators_:
            speeds = tree.predict(transformed)
            power = np.zeros_like(speeds)
            for turbine, data in curves.items():
                selected_t = active_t == turbine
                interp = PchipInterpolator(data['wind'], data['power'])
                power[selected_t] = interp(np.clip(speeds[selected_t], min(data['wind']), max(data['wind'])))
            tree_power.append(power)
        mapped_scenarios[active] = np.mean(tree_power, axis=0)
        predictions['wind_tree_scenarios'] = mapped_scenarios
        timing['wind_then_curve'] = {'fit_and_predict_seconds': time.perf_counter()-began}
    # Choose additions using development ONLY; test labels never select hyperparameters.
    base_names = list(predictions)
    ranking = sorted(base_names, key=lambda name: metrics(y[dev], predictions[name][dev])['rmse'])
    top = ranking[0]
    for window in (7, 30):
        masked_y = y.copy()
        masked_y[~active] = np.nan
        correction = causal_correction(predictions[top], masked_y, issues, turbines, window)
        predictions[f'{top}_plus_error_{window}d'] = predictions[top] + correction
    # Complementary families, rather than only two nearly identical curves.
    direct = min(configs, key=lambda name: metrics(y[dev], predictions[name][dev])['rmse'])
    second = direct if direct != top else ranking[1]
    for weight in (.25, .5, .75):
        predictions[f'blend_{top}_{second}_{weight}'] = weight*predictions[top] + (1-weight)*predictions[second]
    # Error model trained on genuinely out-of-sample September forecasts.
    error_model = make_estimator('ridge_7d')
    residual_x = np.column_stack([x, np.nan_to_num(predictions[top])])
    residual_train = warmup & np.isfinite(y).all(axis=1)
    error_model.fit(residual_x[residual_train], (y-predictions[top])[residual_train])
    corrected = np.full_like(y, np.nan)
    corrected[dev | control] = predictions[top][dev | control] + error_model.predict(residual_x[dev | control])
    predictions[f'{top}_plus_learned_error'] = corrected
    winner = min(predictions, key=lambda name: metrics(y[dev], predictions[name][dev])['rmse'])
    # Calibrate intervals on development errors, report actual held-out coverage.
    # Selection and calibration share data: these are empirical, not guaranteed intervals.
    low, high = predictions[winner].copy(), predictions[winner].copy()
    offsets = {}
    for turbine in (0, 1):
        for lo, hi in ((0,24),(24,48)):
            mask = dev & (turbines == turbine)
            err = (y-predictions[winner])[mask,lo:hi]
            err = err[np.isfinite(err)]
            q = np.quantile(err, [.1,.9])
            offsets[f'T{turbine+1}_{lo+1}-{hi}'] = q.tolist()
            low[turbines == turbine,lo:hi] += q[0]
            high[turbines == turbine,lo:hi] += q[1]
    # Adaptive empirical bands use only past, matured errors, with static dev bands
    # as a fallback for the control period. No claim of conformal coverage.
    adaptive_low, adaptive_high = low.copy(), high.copy()
    valid_ns = issues[:,None] + np.arange(1,49)[None,:]*3600_000_000_000
    residuals = y-predictions[winner]
    for i in np.where(control)[0]:
        for lo,hi in ((0,24),(24,48)):
            eligible = ((turbines == turbines[i])[:,None] & (issues < issues[i])[:,None]
                        & (valid_ns <= issues[i])
                        & (valid_ns > issues[i]-30*24*3600_000_000_000))
            eligible[:,:lo] = False
            eligible[:,hi:] = False
            err = residuals[eligible]
            err = err[np.isfinite(err)]
            if len(err) >= 200:
                a,b = np.quantile(err,[.1,.9])
                adaptive_low[i,lo:hi] = predictions[winner][i,lo:hi]+a
                adaptive_high[i,lo:hi] = predictions[winner][i,lo:hi]+b
    all_metrics = {name: {'selection': grouped_metrics(y[dev], p[dev], turbines[dev]),
                           'control': grouped_metrics(y[control], p[control], turbines[control])}
                   for name,p in predictions.items()}
    available = control[:,None] & np.isfinite(y)
    coverage = float(np.mean(((y >= low) & (y <= high))[available]))
    report = {
        'winner_selected_on_october_november': winner,
        'source_sha256': hashes, 'issue_hour_local': 6, 'utc_offset_hours': 5,
        'training_targets_available_by': training_cutoff.isoformat(),
        'selection_start': selection_start.isoformat(), 'control_start': control_start.isoformat(),
        'last_observation_interval_end': observations_end.isoformat(),
        'training_origins': int(train.sum()), 'selection_origins': int(dev.sum()),
        'control_origins': int(control.sum()), 'metrics': all_metrics, 'timing': timing,
        'interval_80_control_coverage': coverage,
        'interval_80_control_mean_width': float(np.mean((high-low)[available])),
         'interval_offsets': offsets,
        'adaptive_interval_80_control_coverage': float(np.mean(((y >= adaptive_low) & (y <= adaptive_high))[available])),
        'adaptive_interval_80_control_mean_width': float(np.mean((adaptive_high-adaptive_low)[available])),
        'elapsed_seconds': time.perf_counter()-start,
        'peak_rss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2),
        'versions': {'python': platform.python_version(), 'numpy': np.__version__,
                     'pandas': pd.__version__, 'sklearn': sklearn.__version__},
        'limitations': [
            'No February targets exist in the supplied files; these are historical replay metrics.',
            'Hourly mean targets require six ten-minute source records; missing targets are not filled.',
            'Source timestamps assumed interval starts; timezone UTC+5 confirmed by user.',
            'Both overlapping daily forecast vintages are scored separately.',
            'RMSE is provisional selection metric; official scoring formula remains unknown.',
            'Control December-January not used for choosing models in this experiment; January previously inspected for curve diagnostic.',
            'Empirical intervals share selection/calibration data and have no guaranteed coverage.',
            'No future measured wind, weather forecasts, or neighboring turbine features are used.',
        ],
    }
    (args.output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    # Preserve every forecast vintage and make base/correction independently visible.
    selected = predictions[winner]
    correction = selected-predictions[top] if '_plus_' in winner else np.zeros_like(selected)
    rows=[]
    for i in np.where(active)[0]:
        issue=pd.Timestamp(issues[i],tz='UTC').tz_convert(zone)
        for h in range(48):
            rows.append({'turbine_id':f'T{turbines[i]+1}', 'issue_time':issue.isoformat(),
                         'valid_time':(issue+pd.Timedelta(hours=h+1)).isoformat(),
                         'lead_hours':h+1, 'prediction':selected[i,h],
                         'base_prediction':selected[i,h]-correction[i,h], 'correction':correction[i,h],
                         'p10_empirical':adaptive_low[i,h], 'p90_empirical':adaptive_high[i,h], 'actual':y[i,h],
                         'split':'control' if control[i] else ('selection' if dev[i] else 'residual_training')})
    pd.DataFrame(rows).to_csv(args.output/'replay.csv', index=False)
    # Save compact candidates for repeatable inference; final refit follows winner review.
    for name in ranking[:2]:
        if name in fitted:
            joblib.dump({'estimator':fitted[name], 'name':name, 'columns':input_dims[name],
                         'feature_names':feature_names+['turbine'],
                         'training_available_through': training_cutoff.isoformat()},
                        args.output/f'{name}.joblib', compress=3)
    joblib.dump({'wind_model':wm, 'curves':curves, 'fitted':fitted,
                 'columns':input_dims, 'feature_names':feature_names+['turbine'],
                 'winner':winner, 'base':top, 'second':second, 'error_model':error_model,
                 'interval_offsets':offsets, 'training_available_through':training_cutoff.isoformat()},
                args.output/'replay_models.joblib', compress=3)
    # Complete target windows only; never fit to missing or post-dataset targets.
    final_train = (last_target <= observations_end.value) & np.isfinite(y).all(axis=1)
    joblib.dump({'x':x, 'y':y, 'wind':wind, 'issues':issues, 'turbines':turbines,
                 'final_train':final_train, 'predictions':predictions, 'frames':frames,
                 'active':active, 'feature_names':feature_names+['turbine']},
                args.output/'experiment_data.joblib', compress=3)
    print(json.dumps({'winner':winner,'control':all_metrics[winner]['control'],
                      'interval_coverage':coverage,'seconds':report['elapsed_seconds']},indent=2),flush=True)


if __name__ == '__main__':
    main()
