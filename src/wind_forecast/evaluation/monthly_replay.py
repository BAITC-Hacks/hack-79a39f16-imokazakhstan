"""Monthly walk-forward replay, daily 06:00 UTC+5 issues, causal prediction intervals."""
from __future__ import annotations

import argparse
import base64
import json
import math
import platform
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from threadpoolctl import threadpool_limits

from wind_forecast.models.history import load_hourly, features, make_estimator, metrics

HOUR_NS = 3_600_000_000_000
ZONE = 'Etc/GMT-5'


def interval_offsets(errors, method='signed', alpha=.2, min_count=200):
    """Calibration samples must be matured out-of-sample errors supplied by the caller."""
    error = np.asarray(errors)
    error = error[np.isfinite(error)]
    if len(error) < min_count:
        return np.nan, np.nan
    if method == 'signed':
        return tuple(np.quantile(error, [alpha/2, 1-alpha/2]))
    if method != 'absolute':
        raise ValueError('unknown interval method')
    # Finite-sample order statistic; serial dependence precludes an iid guarantee.
    order = min(len(error), math.ceil((len(error)+1)*(1-alpha)))
    q = np.partition(np.abs(error), order-1)[order-1]
    return -q, q


def interval_metrics(actual, low, high, alpha=.2):
    valid = np.isfinite(actual) & np.isfinite(low) & np.isfinite(high)
    actual, low, high = actual[valid], low[valid], high[valid]
    if not len(actual):
        return {'n':0,'coverage':None,'mean_width':None,'interval_score':None}
    width = high-low
    score = width + (2/alpha)*(np.maximum(low-actual,0)+np.maximum(actual-high,0))
    return {'n':int(len(actual)), 'coverage':float(np.mean((actual>=low)&(actual<=high))),
            'mean_width':float(np.mean(width)), 'interval_score':float(np.mean(score))}


def rolling_intervals(pred, actual, issues, turbines, active, days, method):
    low, high = np.full_like(pred,np.nan),np.full_like(pred,np.nan)
    valid_ns = issues[:,None] + np.arange(1,49)[None,:]*HOUR_NS
    residual = actual-pred
    for i in np.where(active)[0]:
        for lo,hi in ((0,24),(24,48)):
            eligible = ((issues < issues[i])[:,None] & (turbines==turbines[i])[:,None]
                        & (valid_ns <= issues[i]) & (valid_ns > issues[i]-days*24*HOUR_NS))
            eligible[:,:lo] = False
            eligible[:,hi:] = False
            a,b = interval_offsets(residual[eligible],method)
            low[i,lo:hi],high[i,lo:hi] = pred[i,lo:hi]+a,pred[i,lo:hi]+b
    return low,high


def build_dataset(frames):
    first = max(f.index.min() for f in frames).normalize()+pd.Timedelta(days=30,hours=6)
    end = min(f.index.max() for f in frames)
    xs,ys,ws,ts,iss,ps = [],[],[],[],[],[]
    skipped=[]
    for issue in pd.date_range(first,end,freq='24h'):
        for turbine,frame in enumerate(frames):
            past = frame.loc[:issue]
            if past.power.tail(24).count() < 18:
                skipped.append({'issue_time':issue.isoformat(),'turbine':f'T{turbine+1}',
                                'reason':'fewer than 18 complete power hours in last 24'})
                continue
            x,names = features(frame,issue)
            future = frame.reindex(pd.date_range(issue+pd.Timedelta(hours=1),periods=48,freq='1h'))
            xs.append(np.r_[x,turbine]);ys.append(future.power.to_numpy());ws.append(future.wind.to_numpy())
            ts.append(turbine);iss.append(issue.value)
            ps.append(np.repeat(past.power.dropna().iloc[-1],48))
    return (np.asarray(xs),np.asarray(ys),np.asarray(ws),np.asarray(ts),
            np.asarray(iss,dtype=np.int64),np.asarray(ps),names+['turbine'],skipped)


def render_report(folder, report, flat):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    months=list(report['months'])
    fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True,layout='constrained')
    for name,label in [('selected','Wind → power model'),('training_mean','Historical mean'),('persistence','Last measured power')]:
        axes[0].plot(months,[report['months'][m]['models'][name]['rmse'] for m in months],marker='o',label=label)
    axes[0].set_ylabel('RMSE, source power units');axes[0].legend();axes[0].grid(alpha=.2)
    for name in report['interval_methods']:
        axes[1].plot(months,[100*report['months'][m]['intervals'][name]['coverage'] for m in months],marker='.',label=name)
    axes[1].axhline(80,color='black',ls='--',label='80% target')
    axes[1].set_ylabel('Prediction interval coverage, %');axes[1].legend(ncol=3,fontsize=8)
    axes[1].grid(alpha=.2);axes[1].tick_params(axis='x',rotation=45)
    fig.savefig(folder/'monthly_metrics.png',dpi=140);plt.close(fig)
    encoded=base64.b64encode((folder/'monthly_metrics.png').read_bytes()).decode()
    rows=''
    for month,data in report['months'].items():
        m=data['models']['selected'];b=data['models']['training_mean'];ci=data['intervals']['signed_30d']
        rows+=f'<tr><td>{month}</td><td>{m["n"]}</td><td>{m["mae"]:.3f}</td><td>{m["rmse"]:.3f}</td><td>{b["rmse"]:.3f}</td><td>{ci["coverage"]*100:.1f}%</td><td>{ci["mean_width"]:.3f}</td></tr>'
    all_metrics=report['overall']['models']['selected']
    intervals=''.join(f'<tr><td>{name}</td><td>{v["coverage"]*100:.1f}%</td><td>{v["mean_width"]:.3f}</td><td>{v["interval_score"]:.3f}</td></tr>' for name,v in report['overall']['intervals'].items())
    page=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Годовая проверка прогноза мощности</title>
<style>body{{font:16px/1.5 system-ui;background:#f3f5f9;color:#1d2939;margin:0}}main{{max-width:1100px;margin:auto;padding:32px}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #e4e7ec}}img{{width:100%}}.note{{padding:15px;border-left:5px solid #bc7400;background:#fff6e8}}</style>
<main><h1>48 часов: проверка за 12 месяцев</h1><p>Февраль 2025 — январь 2026 · ежедневный выпуск 06:00 UTC+5 · только SCADA</p>
<div class="note">Каждый месяц модель переобучается только на доступной к началу месяца истории. Архитектура ранее выбрана по более поздним месяцам, поэтому это ретроспективная оценка устойчивости, а не новый независимый финальный тест.</div>
<section><h2>Результат за весь период</h2><p>RMSE <b>{all_metrics['rmse']:.3f}</b>, MAE <b>{all_metrics['mae']:.3f}</b>, проверено <b>{all_metrics['n']:,}</b> прогноз–факт пар. Среднее по обучающей истории: RMSE {report['overall']['models']['training_mean']['rmse']:.3f}. Все значения в исходной нормализованной шкале мощности.</p><img alt="Ошибки модели и покрытие интервалов по месяцам" src="data:image/png;base64,{encoded}"></section>
<section><h2>Каждый месяц</h2><p>Месяц определяется часом фактической выработки; час, заканчивающийся в 00:00, относится к предыдущему дню. Перекрывающиеся ежедневные выпуски учитываются отдельно. Интервал в таблице — эмпирические квантили ошибок последних 30 дней.</p><table><tr><th>Месяц</th><th>n</th><th>MAE</th><th>RMSE</th><th>RMSE среднего</th><th>Покрытие 80%</th><th>Ширина</th></tr>{rows}</table></section>
<section><h2>Недорогие интервалы</h2><table><tr><th>Метод</th><th>Покрытие</th><th>Ширина</th><th>Interval score ↓</th></tr>{intervals}</table><p>Окна 30/90 дней используют только уже известные ошибки ранее выпущенных прогнозов. Статистика считается отдельно для каждой турбины и горизонтов 1–24 / 25–48 часов. Из-за зависимости во времени формальная гарантия покрытия не заявляется. Узкий диапазон ценен только при достаточном покрытии.</p></section>
<section><h2>Jev: стоимость и применимость</h2><p>По официальной документации <a href="https://docs.typesafe.ai/confidence">confidence Jev</a> характеризует распределение ответов Choice/Score. Это не готовый предиктивный интервал мощности. Производитель отдельно отмечает <a href="https://docs.typesafe.ai/model-jaggedness/jev-1.13">ограничения численной точности</a>.</p><p>Опубликованная <a href="https://docs.typesafe.ai/models">цена</a>: $0.042 за миллион входных токенов. Например, 730 запросов по 2000 токенов стоили бы около $0.061 только за инференс по этому тарифу. Это расчёт, не измеренная стоимость; API не вызывался. Главный вопрос — полезность, а не цена. Для интервалов выбраны локальные статистические методы. Jev можно отдельно проверять как классификатор текстовых журналов или режимов риска, если появятся такие входы.</p></section>
<section><h2>Ограничения и воспроизводимость</h2><p>Февраль 2026 ещё отсутствует. Неполные часы не заполняются. Пропуски истории могут исключать выпуск. Прогнозы не обрезаются под предположение о мощности оборудования. Границы интервалов могут выходить за наблюдаемую шкалу 0–1.</p><p>Все выпуски: replay.csv; сводка по месяцам: monthly.csv; турбины и горизонты: monthly_groups.csv; исходные хеши, даты обучения, доступность и замеры: report.json. Время расчётов {report['elapsed_seconds']:.1f} с, пиковая память процесса {report['peak_rss_mb']:.0f} MiB, без GPU и платных API.</p></section></main></html>'''
    (folder/'report.html').write_text(page,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--turbine-1',type=Path,required=True)
    parser.add_argument('--turbine-2',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    began=time.perf_counter()
    frames,hashes=zip(*(load_hourly(p) for p in (args.turbine_1,args.turbine_2)))
    x,y,wind,turbines,issues,persistence,feature_names,skipped=build_dataset(frames)
    last_target=issues+48*HOUR_NS
    pred=np.full_like(y,np.nan);mean=np.full_like(y,np.nan)
    fits=[]
    # Three unscored months initialize the 90-day error window before evaluation.
    for boundary in pd.date_range('2024-11-01','2026-01-01',freq='MS',tz=ZONE):
        cutoff=boundary+pd.Timedelta(hours=6)
        next_cutoff=boundary+pd.offsets.MonthBegin()+pd.Timedelta(hours=6)
        train=(last_target<=cutoff.value)&np.isfinite(y).all(axis=1)&np.isfinite(wind).all(axis=1)
        use=(issues>=cutoff.value)&(issues<next_cutoff.value)
        started=time.perf_counter()
        model=make_estimator('extra_shallow')
        with threadpool_limits(limits=2):
            model.fit(x[train],wind[train])
        transformed=model[0].transform(x[use]);t=turbines[use]
        curves={}
        for turbine,frame in enumerate(frames):
            known=frame.loc[frame.index<=cutoff].dropna(subset=['wind','power'])
            bins=known.groupby(np.floor(known.wind).astype(int)).power.mean()
            curves[turbine]=(bins.index.to_numpy()+.5,bins.to_numpy())
            mean[use & (turbines==turbine)]=frame.loc[:cutoff].power.mean()
        total=np.zeros((int(use.sum()),48))
        for tree in model[-1].estimators_:
            speed=tree.predict(transformed)
            for turbine,(v,p) in curves.items():
                mask=t==turbine
                total[mask]+=PchipInterpolator(v,p)(np.clip(speed[mask],v.min(),v.max()))
        pred[use]=total/len(model[-1].estimators_)
        info={'issue_month':boundary.strftime('%Y-%m'), 'train_cutoff':cutoff.isoformat(),
              'training_origins':int(train.sum()), 'forecast_origins':int(use.sum()),
              'latest_training_target':pd.Timestamp(last_target[train].max(),tz='UTC').isoformat(),
              'fit_and_predict_seconds':time.perf_counter()-started}
        fits.append(info);print(json.dumps(info),flush=True)
    active=np.isfinite(pred).all(axis=1)
    methods={'signed_30d':(30,'signed'),'signed_90d':(90,'signed'),'absolute_30d':(30,'absolute')}
    intervals={}
    interval_started=time.perf_counter()
    for name,(days,method) in methods.items():
        intervals[name]=rolling_intervals(pred,y,issues,turbines,active,days,method)
    interval_seconds=time.perf_counter()-interval_started
    rows=[]
    for i in np.where(active)[0]:
        issue=pd.Timestamp(issues[i],tz='UTC').tz_convert(ZONE)
        for h in range(48):
            valid=issue+pd.Timedelta(hours=h+1)
            # Hour ending midnight is power generated in the PREVIOUS calendar day.
            target_month=(valid-pd.Timedelta(nanoseconds=1)).strftime('%Y-%m')
            if not '2025-02' <= target_month <= '2026-01':
                continue
            row={'target_month':target_month,'issue_time':issue.isoformat(),
                 'valid_time':valid.isoformat(),'turbine_id':f'T{turbines[i]+1}',
                 'lead_hours':h+1,'lead_bucket':'1-24' if h<24 else '25-48',
                 'prediction':pred[i,h],'actual':y[i,h],
                 'training_mean':mean[i,h],'persistence':persistence[i,h]}
            for name,(lo,hi) in intervals.items():
                row[name+'_low']=lo[i,h];row[name+'_high']=hi[i,h]
            rows.append(row)
    flat=pd.DataFrame(rows)
    flat.to_csv(args.output/'replay.csv',index=False)

    def summarize(group):
        models={name:metrics(group.actual.to_numpy(),group[column].to_numpy())
                for name,column in [('selected','prediction'),('training_mean','training_mean'),('persistence','persistence')]}
        bands={name:interval_metrics(group.actual.to_numpy(),group[name+'_low'].to_numpy(),group[name+'_high'].to_numpy()) for name in methods}
        return {'models':models,'intervals':bands,'forecast_rows':len(group),
                'missing_actuals':int(group.actual.isna().sum())}

    months={month:summarize(group) for month,group in flat.groupby('target_month')}
    monthly=[];groups=[]
    for month,data in months.items():
        monthly.append({'month':month,**data['models']['selected'],
                        'mean_rmse':data['models']['training_mean']['rmse'],
                        **{name+'_'+k:v for name,band in data['intervals'].items() for k,v in band.items()}})
    for (month,turbine,bucket),group in flat.groupby(['target_month','turbine_id','lead_bucket']):
        groups.append({'month':month,'turbine_id':turbine,'lead_bucket':bucket,
                       **metrics(group.actual.to_numpy(),group.prediction.to_numpy()),
                       **{name+'_'+k:v for name in methods for k,v in interval_metrics(group.actual.to_numpy(),group[name+'_low'].to_numpy(),group[name+'_high'].to_numpy()).items()}})
    pd.DataFrame(monthly).to_csv(args.output/'monthly.csv',index=False)
    pd.DataFrame(groups).to_csv(args.output/'monthly_groups.csv',index=False)
    scored_skips=[s for s in skipped if '2025-02' <= s['issue_time'][:7] <= '2026-01']
    report={'protocol':'monthly expanding training; daily 06:00 UTC+5, 48 hourly forecasts',
            'months':months,'overall':summarize(flat),'interval_methods':list(methods),
            'source_sha256':hashes,'fits':fits,'skipped_origins':scored_skips,
            'feature_count':len(feature_names),'interval_computation_seconds':interval_seconds,
            'elapsed_seconds':time.perf_counter()-began,
            'peak_rss_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if platform.system() == 'Darwin' else 1024),
            'python_version':platform.python_version(),
            'limitations':[
                'Architecture was selected previously using later 2025 months; this is exploratory retrospective assessment, not pristine model selection.',
                'Training and interval calibration never consume observations after each simulated issue.',
                'Months follow target production intervals; overlapping daily forecast vintages remain separate.',
                'No February 2026 targets. Hourly mean requires six ten-minute records; missing targets not imputed.',
                'Source timestamp assumed interval start; UTC+5 user confirmed.',
                'Serial correlation and changing models invalidate a simple iid conformal coverage guarantee.',
                'Jev API not called: confidence of a typed answer is not a power prediction interval.',
            ]}
    (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    render_report(args.output,report,flat)
    print(json.dumps({'overall':report['overall'],'seconds':report['elapsed_seconds'],
                      'output':str(args.output)},indent=2),flush=True)


if __name__=='__main__':
    main()
