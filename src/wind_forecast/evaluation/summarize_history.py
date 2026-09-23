"""Produce a readable local experiment report with explicit validation limitations."""
import argparse
import base64
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd


LABELS={
    'wind_tree_scenarios':'Wind scenarios → smooth power curve',
    'wind_then_smooth':'Mean wind forecast → smooth power curve',
    'wind_then_lut':'Mean wind forecast → lookup table',
    'training_mean':'Constant training mean',
    'persistence':'Last observed power',
    'daily_profile':'Repeat last daily profile',
    'weekly_mean':'Last week mean',
    'extra_shallow':'Direct ExtraTrees (small)',
    'extra_deep':'Direct ExtraTrees (deeper)',
    'hist_boost':'Direct gradient boosting',
    'ridge_7d':'Linear Ridge, 7-day statistics',
    'ridge_30d':'Linear Ridge, 30-day statistics',
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    args=parser.parse_args()
    folder=args.experiment
    report=json.loads((folder/'report.json').read_text())
    latency=json.loads((folder/'inference_check.json').read_text()) if (folder/'inference_check.json').exists() else {'median_seconds':None}
    latency_text=f"{latency['median_seconds']:.2f} с" if latency['median_seconds'] is not None else 'не измерено'
    meta=json.loads((folder/'model_metadata.json').read_text())
    replay=pd.read_csv(folder/'replay.csv')
    winner=report['winner_selected_on_october_november']
    chosen=report['metrics'][winner]['control']['all']
    first=replay[replay.split=='control'].issue_time.min()
    fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=True,layout='constrained')
    for ax,turbine in zip(axes,['T1','T2']):
        rows=replay[(replay.issue_time==first)&(replay.turbine_id==turbine)]
        ax.fill_between(rows.lead_hours,rows.p10_empirical,rows.p90_empirical,color='#4479cc',alpha=.15,label='Empirical P10–P90')
        ax.plot(rows.lead_hours,rows.prediction,color='#225ab5',lw=2,label='Forecast at issue time')
        ax.plot(rows.lead_hours,rows.actual,color='#242b36',lw=1.8,label='Actual (evaluation only)')
        ax.set_ylabel(turbine+' normalized power')
        ax.grid(alpha=.2)
    axes[0].legend(ncol=3,fontsize=8)
    axes[0].set_title('First control issue: '+first+' — not selected for good performance')
    axes[1].set_xlabel('Forecast lead (hours)')
    fig.savefig(folder/'forecast_example.png',dpi=140)
    plt.close(fig)
    encoded=base64.b64encode((folder/'forecast_example.png').read_bytes()).decode()
    rows=''
    for name,score in sorted(report['metrics'].items(),key=lambda kv:kv[1]['selection']['all']['rmse']):
        dev=score['selection']['all']; test=score['control']['all']
        rows+=f'<tr><td>{html.escape(LABELS.get(name,name))}{" ★" if name==winner else ""}</td><td>{dev["rmse"]:.4f}</td><td>{test["rmse"]:.4f}</td><td>{test["mae"]:.4f}</td></tr>'
    page=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>48 часов — историческая проверка</title>
<style>body{{font:16px/1.55 system-ui;background:#f3f5f9;color:#1d2939;margin:0}}main{{max-width:1040px;margin:auto;padding:36px}}h1{{font-size:32px;line-height:1.2}}section{{background:white;padding:24px;margin:18px 0;border-radius:12px}}.note{{border-left:5px solid #bc7400;padding:14px;background:#fff6e8}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:9px;border-bottom:1px solid #e4e7ec}}img{{width:100%}}.metrics{{display:flex;gap:28px;flex-wrap:wrap}}b.big{{display:block;font-size:30px;color:#225ab5}}code{{overflow-wrap:anywhere}}</style>
<main><h1>Прогноз мощности на 48 часов</h1><p>Ежедневный выпуск в 06:00 UTC+5. Только доступная история мощности, ветра и температуры.</p>
<div class="note"><b>Это историческая проверка, не результат за февраль.</b> CSV заканчиваются 31 января 2026 года. Ошибки ниже относятся к декабрю–январю, мощность — в исходной нормализованной шкале.</div>
<section><div class="metrics"><div>RMSE<b class="big">{chosen['rmse']:.3f}</b></div><div>MAE<b class="big">{chosen['mae']:.3f}</b></div><div>Прогноз 2 × 48 часов<b class="big">{latency_text}</b></div><div>Файл модели<b class="big">{meta['bytes']/1e6:.1f} МБ</b></div></div></section>
<section><h2>Как работает выбранная модель</h2><p>160 небольших деревьев прогнозируют ветер по прошлым измерениям. Каждый прогноз проходит через плавную кривую мощности своей турбины. Усредняются мощности, а не скорости ветра. Это варианты модели, не независимые метеорологические сценарии.</p><p>Добавочная поправка отключена: проверенные варианты не улучшили выборочную оценку. Смесь с прямой моделью мощности также не победила на октябре–ноябре.</p><p>Относительно постоянной средней мощности снижение RMSE на контроле составляет {(1-chosen['rmse']/report['metrics']['training_mean']['control']['all']['rmse'])*100:.1f}%. Выигрыш пока небольшой, статистическая значимость не установлена.</p></section>
<section><h2>Пример одного ежедневного выпуска</h2><img src="data:image/png;base64,{encoded}" alt="48-часовой прогноз и фактическая мощность двух турбин"><p>Фактические значения использованы только после выпуска для оценки. Первая контрольная дата показана без отбора удачного примера.</p></section>
<section><h2>Сравнение гипотез</h2><p>Выбор по RMSE октября–ноября. Декабрь–январь — контроль; январь ранее использовался в другой диагностике. Перекрывающиеся ежедневные прогнозы оцениваются как отдельные выпуски.</p><table><thead><tr><th>Метод</th><th>RMSE выбора</th><th>RMSE контроля</th><th>MAE контроля</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>Неопределённость</h2><p>Фиксированный диапазон P10–P90 покрыл {report['interval_80_control_coverage']*100:.1f}% фактов вместо целевых 80%. Пересчёт по созревшим ошибкам последних 30 дней дал {report['adaptive_interval_80_control_coverage']*100:.1f}%, но средняя ширина диапазона — {report['adaptive_interval_80_control_mean_width']:.3f}. Это почти вся наблюдаемая шкала мощности: уверенность без прогноза погоды низкая.</p><p>В сохранённом предикторе пока используются фиксированные эмпирические интервалы. Динамический вариант проверен в replay и требует передачи истории прогнозов при интеграции. После финального переобучения покрытие интервалов ещё не проверено.</p></section>
<section><h2>Что добавить дальше</h2><ol><li>Архив и текущий прогноз ветра на высоте ротора с датой выпуска — основной кандидат на улучшение.</li><li>Координаты, высота и диаметр ротора из паспорта оборудования — для привязки погодной сетки.</li><li>Направление ветра и коды ограничений/остановок из SCADA — для различения слабого ветра и недоступности турбины.</li><li>Разброс скорости ветра внутри интервала; давление для оценки плотности воздуха. Сначала проверить существующие датчики.</li></ol><p>Модель переобучена на доступных данных до 2026-02-01 00:00 UTC+5. Для выпуска 1 февраля в 06:00 не хватает последних шести часов измерений. Контрольные метрики получены моделью, обученной до сентября, и не являются оценкой финального переобученного артефакта.</p><p>Пиковая память всего эксперимента: {report['peak_rss_mb']:.0f} МБ. GPU не использовался. SHA-256 модели: <code>{meta['sha256']}</code>.</p></section></main></html>'''
    (folder/'report.html').write_text(page,encoding='utf-8')
    print(folder/'report.html')


if __name__=='__main__':
    main()
