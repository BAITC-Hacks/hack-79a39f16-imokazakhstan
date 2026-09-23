"""Common-sample accuracy, uncertainty, and explicit cost assumptions for replay models."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from wind_forecast.models.history import metrics
from wind_forecast.evaluation.extended_replay import score, MODELS
from wind_forecast.evaluation.monthly_replay import HOUR_NS, ZONE, rolling_intervals, interval_metrics
from wind_forecast.evaluation.history_experiment import causal_correction
from wind_forecast.evaluation.benchmark_cost import task_usd, SOURCE


def align_reference(path, data):
    flat = pd.read_csv(path)
    lookup = {(int(issue),int(turbine)):i for i,(issue,turbine) in enumerate(zip(data['issues'],data['turbines']))}
    models = {n:np.full_like(data['y'],np.nan) for n in ['selected','training_mean','persistence']}
    for row in flat.itertuples():
        i = lookup[(pd.Timestamp(row.issue_time).value,int(row.turbine_id[1:])-1)]
        h = row.lead_hours-1
        if np.isfinite(row.actual):
            if not np.isclose(data['y'][i,h],row.actual,rtol=1e-7,atol=1e-9):
                raise ValueError('reference actual values disagree with cached dataset')
        for name,column in [('selected','prediction'),('training_mean','training_mean'),('persistence','persistence')]:
            models[name][i,h] = getattr(row,column)
    return models


def bootstrap_skill(y,pred,base,issues,mask,seed=42):
    """Paired seven-day block bootstrap; exploratory, not a model-selection correction."""
    use = mask & np.isfinite(y) & np.isfinite(pred) & np.isfinite(base)
    block = (issues-issues.min())//(7*24*HOUR_NS)
    rows = []
    for b in np.unique(block):
        sel = use & (block==b)[:,None]
        if sel.any():
            rows.append([((pred-y)[sel]**2).sum(),((base-y)[sel]**2).sum(),sel.sum()])
    a = np.asarray(rows)
    rng = np.random.default_rng(seed)
    totals = a[rng.integers(0,len(a),size=(1000,len(a)))].sum(axis=1)
    delta = np.sqrt(totals[:,0]/totals[:,2])-np.sqrt(totals[:,1]/totals[:,2])
    return np.quantile(delta,[.025,.975]).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=Path,required=True)
    parser.add_argument('--extra-runs',type=Path,nargs='*',default=[])
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    data = joblib.load(args.dataset)
    y,issues,turbines = data['y'],data['issues'],data['turbines']
    valid = issues[:,None]+np.arange(1,49)[None,:]*HOUR_NS
    start = pd.Timestamp('2025-02-01',tz=ZONE).value
    end = pd.Timestamp('2026-02-01',tz=ZONE).value
    selection_end = pd.Timestamp('2025-12-01',tz=ZONE).value
    period = (valid>start)&(valid<=end)
    development = period & (valid<=selection_end)
    control = period & (valid>selection_end)
    predictions = align_reference(args.reference,data)
    details = {}
    native = {}
    incomplete = []
    for p in sorted(args.runs.glob('*/report.json')) + [p for root in args.extra_runs for p in sorted(root.glob('*/report.json'))]:
        r = json.loads(p.read_text())
        if not r['complete']:
            incomplete.append({'model':r['model'],'last_month':r['fits'][-1]['issue_month']})
            continue
        a = np.load(p.parent/'predictions.npz')
        if not np.array_equal(a['issues'],issues) or not np.array_equal(a['turbines'],turbines):
            raise ValueError('replay keys disagree')
        np.testing.assert_allclose(a['actual'],y,equal_nan=True)
        details[r['model']] = r
        predictions[r['model']] = a['prediction']
        native[r['model']] = (a['low'],a['high'])
    for name in MODELS:
        if name not in {r.get('architecture',r['model']) for r in details.values()} and not any(item['model']==name for item in incomplete):
            incomplete.append({'model':name,'status':'no completed report; inspect execution log'})
    # Use the exact same production-hour pairs for ranking all completed models.
    common = period & np.isfinite(y)
    for p in predictions.values():
        common &= np.isfinite(p)
    if common.sum() < .95*(period & np.isfinite(y)).sum():
        raise ValueError('too many missing predictions for a common-sample comparison')
    ranks = sorted(['selected',*details],key=lambda n:metrics(np.where(common&development,y,np.nan),predictions[n])['rmse'])
    scratch = [n for n in ranks if not details.get(n,{}).get('pretrained_hindsight',False)]
    # A small, explicit blend grid selected using February-November only.
    compositions = {}
    for label,names in [('blend_scratch',scratch[:2]),('blend_all',ranks[:2]),
                       ('blend_previous',[ranks[0],'selected'])]:
        if len(set(names))==2:
            if any(set(c.get('weights',{}))==set(names) for c in compositions.values()):
                continue
            for weight in (.25,.5,.75):
                variant = label+f'_{int(weight*100)}pct'
                predictions[variant] = weight*predictions[names[0]]+(1-weight)*predictions[names[1]]
                compositions[variant] = {'weights':{names[0]:weight,names[1]:1-weight},'selected_on':'February-November 2025'}
    # Corrections remain explicitly additive, based only on already observed errors.
    correction_names = list(dict.fromkeys([scratch[0],ranks[0]])) if ranks else []
    correction_components = {}
    for name in correction_names:
        active = np.isfinite(predictions[name]).all(axis=1)
        ix = np.where(active)[0]
        correction = np.zeros_like(y)
        correction[ix] = causal_correction(predictions[name][ix],y[ix],issues[ix],turbines[ix],30)
        label = name+'+error30d'
        predictions[label] = predictions[name]+correction
        compositions[label] = {'base':name,'correction':'mean matured residual, trailing 30 days, per turbine and 24-hour lead bucket'}
        correction_components[label] = correction
    rows,monthly,groups,intervals = [],[],[],{}
    for name,p in predictions.items():
        whole = metrics(np.where(common,y,np.nan),p)
        dev = metrics(np.where(common&development,y,np.nan),p)
        test = metrics(np.where(common&control,y,np.nan),p)
        r = details.get(name)
        warm = float(np.median([f['daily_model_seconds'] for f in r['fits']])) if r else None
        training = float(np.median([f['fit_seconds'] for f in r['fits']])) if r else None
        statistical = name in ('arima_201','autoarima','sarima_101')
        # CPU-only cost sensitivity; GPU runs are not extrapolated to CPU prices.
        cpu_costable = r is not None and r['device']=='cpu' and r['peak_rss_mib']<4096
        daily_cost = task_usd(10+warm,slowdown=5) if cpu_costable else None
        monthly_trained = r is not None and not statistical and ('lora' in name or not name.startswith('chronos'))
        training_cost = task_usd(20+training,slowdown=5)/30 if monthly_trained else 0.
        row = {'model':name,**whole,'development_rmse':dev['rmse'],'control_rmse':test['rmse'],
               'first_day_rmse':metrics(np.where(common,y,np.nan)[:,:24],p[:,:24])['rmse'],
               'second_day_rmse':metrics(np.where(common,y,np.nan)[:,24:],p[:,24:])['rmse'],
               'rmse_delta_vs_selected_95pct_block_interval':bootstrap_skill(y,p,predictions['selected'],issues,common),
               'daily_model_seconds':warm,'monthly_fit_seconds':None if statistical else training,
               'peak_rss_mib':r['peak_rss_mib'] if r else None,
               'estimated_cpu_usd_per_day_with_monthly_refit_5x':daily_cost+training_cost if cpu_costable else None,
               'pretrained_hindsight':r['pretrained_hindsight'] if r else any(details.get(n,{}).get('pretrained_hindsight',False) for n in compositions.get(name,{}).get('weights',{})) or ('+error30d' in name and details.get(name.split('+')[0],{}).get('pretrained_hindsight',False))}
        rows.append(row)
        for m,s in score(p,dict(data,y=np.where(common,y,np.nan)))['months'].items():
            monthly.append({'model':name,'month':m,**s})
        for turbine in (0,1):
            for label,lo,hi in [('1-24',0,24),('25-48',24,48)]:
                mask = common & (turbines==turbine)[:,None]
                groups.append({'model':name,'turbine':f'T{turbine+1}','lead_bucket':label,
                               **metrics(np.where(mask,y,np.nan)[:,lo:hi],p[:,lo:hi])})
        active = np.isfinite(p).all(axis=1)
        lo,hi = rolling_intervals(p,y,issues,turbines,active,30,'absolute')
        intervals[name] = {'calibrated_80':interval_metrics(np.where(common,y,np.nan),lo,hi)}
        if name in native and np.isfinite(native[name][0]).any():
            intervals[name]['native_80'] = interval_metrics(np.where(common,y,np.nan),*native[name])
    rows.sort(key=lambda r:r['development_rmse'])
    weights = {}
    for weight in sorted((args.dataset.parent/'pretrained').glob('*/*.safetensors')):
        digest = hashlib.sha256()
        with weight.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        weights[weight.parent.name] = {'file':weight.name,'bytes':weight.stat().st_size,'sha256':digest.hexdigest()}
    report = {'common_scored_pairs':int(common.sum()),'source_sha256':data['source_sha256'],
              'pretrained_weights':weights,
              'ranking':rows,'compositions':compositions,'intervals':intervals,'incomplete':incomplete,
              'selection':'February-November production hours; December-January descriptive control already inspected in earlier work',
              'cost':{'price_source':SOURCE,'price_checked':'2026-09-23','vcpu':2,'gib':4,'slowdown':5,
                      'minimum_task_seconds':60,'startup_seconds':5,'forecast_extra_seconds':10,'fit_extra_seconds':20,
                      'monthly_retraining':1,'budget_usd_day':25,'actual_cloud_spend':None,
                      'exclusions':'storage, networking, logs, image transfer, UI/API hosting, taxes; cold-start assumptions not measured'},
              'limitations':['All results exploratory; historical months reused for development.',
                             'Pretrained weights postdate some replay issues; no as-of eligibility or pretraining-contamination guarantee.',
                             'Two turbines only; graph is an own-neighbor ablation without coordinates/direction or physical wake attribution.',
                             'Patch-attention network is inspired by PatchTST, not an exact reproduction.',
                             'Seven-day block bootstrap is an exploratory uncertainty diagnostic, not selection-adjusted significance.',
                             'Cost uses local CPU timings at published Fargate rates, not measured Brev or AWS charges.',
                             'Monthly model refresh evaluated; daily retraining accuracy has not been established.',
                             'Source timestamps assumed interval starts; normalization capacity unconfirmed; no February 2026 targets.'],
              'versions':{n:importlib.metadata.version(n) for n in ['numpy','pandas','torch','catboost','statsforecast','chronos-forecasting','peft','transformers']}}
    (args.output/'report.json').write_text(json.dumps(report,indent=2))
    pd.DataFrame(rows).to_csv(args.output/'comparison.csv',index=False)
    pd.DataFrame(monthly).to_csv(args.output/'monthly.csv',index=False)
    pd.DataFrame(groups).to_csv(args.output/'turbine_leads.csv',index=False)
    np.savez_compressed(args.output/'combined_predictions.npz',actual=y,issues=issues,turbines=turbines,**predictions)
    np.savez_compressed(args.output/'additive_errors.npz',**correction_components)
    # Standalone scientific figure, also embedded in the user-facing report.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax = plt.subplots(figsize=(11,8),layout='constrained')
    names = [r['model'] for r in rows]
    ax.barh(names,[r['rmse'] for r in rows],color=['#b7791f' if r['pretrained_hindsight'] else '#2563a6' for r in rows])
    ax.invert_yaxis();ax.set_xlabel('RMSE: February 2025–January 2026 (lower is better)')
    ax.axvline(next(r['rmse'] for r in rows if r['model']=='selected'),color='black',ls='--',label='Previous selected model')
    ax.legend();ax.grid(axis='x',alpha=.2)
    fig.savefig(args.output/'comparison.png',dpi=140);plt.close(fig)
    image = base64.b64encode((args.output/'comparison.png').read_bytes()).decode()
    table = ''
    for r in rows:
        duration = '—' if r['daily_model_seconds'] is None else f"{r['daily_model_seconds']:.3f}"
        cost = '—' if r['estimated_cpu_usd_per_day_with_monthly_refit_5x'] is None else f"${r['estimated_cpu_usd_per_day_with_monthly_refit_5x']:.4f}"
        table += f"<tr><td>{html.escape(r['model'])}{' *' if r['pretrained_hindsight'] else ''}</td><td>{r['rmse']:.4f}</td><td>{r['mae']:.4f}</td><td>{r['control_rmse']:.4f}</td><td>{r['second_day_rmse']:.4f}</td><td>{duration}</td><td>{cost}</td></tr>"
    page = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Сравнение моделей мощности</title>
<style>body{{font:16px/1.5 system-ui;color:#172438;background:#f5f7fa}}main{{max-width:1200px;margin:auto;padding:24px}}section{{background:white;padding:24px;margin:18px 0;border-radius:12px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #ddd}}img{{max-width:100%}}.note{{border-left:4px solid #b7791f;padding:16px;background:#fff5df}}</style>
<main><h1>48 часов: расширенное сравнение моделей</h1><p>Две турбины · ежедневно 06:00 UTC+5 · {common.sum():,} одинаковых прогноз–факт пар · февраль 2025 — январь 2026</p>
<p class="note">Исследовательский бэктест: эти месяцы уже использовались при разработке. Выбор вариантов — по февралю–ноябрю; декабрь–январь показаны отдельно. Модели со звёздочкой используют предобученные веса, опубликованные позже части периода: это не имитация реально доступного тогда решения.</p>
<section><h2>Точность и ресурсы</h2><table><tr><th>Модель</th><th>RMSE за год ↓</th><th>MAE за год ↓</th><th>RMSE дек.–янв.</th><th>RMSE вторых суток</th><th>Прогноз, с</th><th>Оценка CPU/день</th></tr>{table}</table><p>Стоимость — расчёт по <a href="{SOURCE}">AWS Fargate</a>: 2 vCPU / 4 GiB, минимум 60 секунд на запуск, ежемесячное обучение, пятикратный запас к локальному времени. Не счёт Brev; сеть, хранение и приложение исключены. Фактические облачные расходы эксперимента: $0.</p></section>
<section><img src="data:image/png;base64,{image}" alt="Сравнение RMSE моделей"><p>Синий — обучение на доступной истории; охра — варианты с предобученными весами.</p></section>
<section><h2>Что именно проверено</h2><p>CatBoost: прямые 48 выходов, объединённая модель с номером горизонта, признаки соседней турбины. ARIMA, AutoARIMA и сезонная ARIMA. Небольшие GRU/LSTM, трансформер с блоками истории, графовая GRU с двумя узлами. Chronos-Bolt Tiny, Chronos-2 Small/Base и LoRA-дообучение Small/Base. Пропуски целевой мощности не заполняются.</p><p>Ансамбли — взвешенное среднее двух моделей, выбранных по февралю–ноябрю; проверены веса 25/50/75%. Поправки error30d сохраняются отдельно: итог = основной прогноз + средняя уже известная ошибка последних 30 дней. Интервалы и их фактическое покрытие, месячные результаты, ограничения и версии — в report.json и monthly.csv.</p></section>
<section><h2>Ограничения</h2><p>Нет февраля 2026 и будущего прогноза погоды. Граф не описывает физический след турбины. Небольшой трансформер — вариант, вдохновлённый PatchTST, не точная реализация статьи. LoRA проверяется как отдельная гипотеза, без предположения об обязательном улучшении. Малые различия RMSE требуют дополнительной проверки на новых данных.</p></section></main></html>'''
    (args.output/'report.html').write_text(page)
    print(json.dumps({'ranking':rows,'output':str(args.output)},indent=2))


if __name__=='__main__':
    main()
