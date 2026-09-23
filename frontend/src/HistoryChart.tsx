import { useState } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ArrowDownToLine, ChartNoAxesCombined } from 'lucide-react';
import type { DashboardData, HistoryPoint, Turbine } from './types';

const metrics = {
  power: {
    label: 'Generation',
    actual: 'actualPower',
    predicted: 'predictedPower',
    unit: 'n.u.',
    caption: 'Hourly normalized power',
    domain: [0, 'auto'],
  },
  wind: {
    label: 'Wind speed',
    actual: 'actualWindSpeed',
    predicted: 'predictedWindSpeed',
    unit: 'm/s',
    caption: 'Wind speed',
    domain: [0, 'auto'],
  },
  temperature: {
    label: 'Temperature',
    actual: 'actualTemperature',
    predicted: 'predictedTemperature',
    unit: '°C',
    caption: 'Air temperature',
    domain: ['auto', 'auto'],
  },
  direction: {
    label: 'Direction',
    actual: 'actualWindDirection',
    predicted: 'predictedWindDirection',
    unit: '°',
    caption: 'Wind direction · clockwise from north',
    domain: [0, 360],
  },
} as const;
type Metric = keyof typeof metrics;

function downloadCsv(data: DashboardData, turbine: Turbine) {
  const keys = [
    'time',
    'actualPower',
    'predictedPower',
    'actualTemperature',
    'predictedTemperature',
    'actualWindSpeed',
    'predictedWindSpeed',
    'actualWindDirection',
    'predictedWindDirection',
  ] as const;
  const rows = [
    ['provenance', 'turbine_id', ...keys].join(','),
    ...turbine.history.map((point) =>
      [data.provenance, turbine.id, ...keys.map((key) => point[key] ?? '')].join(','),
    ),
  ];
  const url = URL.createObjectURL(
    new Blob([rows.join('\r\n')], { type: 'text/csv;charset=utf-8;' }),
  );
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${data.provenance}-${turbine.id}-${data.date}.csv`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function HistoryChart({ data, turbine }: { data: DashboardData; turbine: Turbine }) {
  const [metric, setMetric] = useState<Metric>('power');
  const [table, setTable] = useState(false);
  const config = metrics[metric];
  const hour = (value: string) =>
    new Intl.DateTimeFormat('en-GB', {
      timeZone: data.timezone,
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value));
  const format = (value: HistoryPoint[keyof HistoryPoint]) =>
    typeof value === 'number'
      ? `${value.toFixed(metric === 'direction' ? 0 : 2)} ${config.unit}`
      : '—';
  const latest = [...turbine.history].reverse().find((point) => point[config.actual] !== null);
  return (
    <section className="panel history-panel" id="performance" aria-labelledby="history-title">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">ACTUAL MEETS EXPECTED</span>
          <h2 id="history-title">
            Performance over time <span className="heading-note">/ {turbine.name}</span>
          </h2>
        </div>
        <button className="text-button" onClick={() => downloadCsv(data, turbine)}>
          <ArrowDownToLine size={15} /> Export CSV
        </button>
      </div>
      <div className="chart-toolbar">
        <div className="metric-tabs" role="group" aria-label="Historical metric">
          {(Object.keys(metrics) as Metric[]).map((key) => (
            <button key={key} onClick={() => setMetric(key)} aria-pressed={metric === key}>
              {metrics[key].label}
            </button>
          ))}
        </div>
        <div className="chart-legend">
          <span>
            <i /> Actual
          </span>
          <span>
            <i /> Predicted
          </span>
        </div>
      </div>
      <div className="chart-readout">
        <span>{config.caption}</span>
        <strong>{latest ? format(latest[config.actual]) : 'No completed readings'}</strong>
        <small>
          {latest
            ? `Latest completed interval · ${hour(latest.time)}–${hour(new Date(Date.parse(latest.time) + 3_600_000).toISOString())}`
            : 'Actual readings appear after each hour ends.'}
        </small>
      </div>
      <div
        className="chart-wrapper"
        role="img"
        aria-label={`${config.label} for ${turbine.name}: actual and predicted hourly data. Use the data table below for exact values.`}
      >
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <ComposedChart
            data={turbine.history}
            margin={{ top: 10, right: 15, left: -22, bottom: 0 }}
            accessibilityLayer
          >
            <defs>
              <linearGradient id="energy-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3b8b70" stopOpacity={0.14} />
                <stop offset="100%" stopColor="#3b8b70" stopOpacity={0.005} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#e9ede8" vertical={false} strokeDasharray="3 4" />
            <XAxis
              dataKey="time"
              tickFormatter={hour}
              interval={3}
              tickLine={false}
              axisLine={false}
              tickMargin={14}
              fontSize={11}
              stroke="#657361"
            />
            <YAxis
              domain={
                metric === 'power'
                  ? [(minimum: number) => Math.min(0, minimum), 'auto']
                  : [...config.domain]
              }
              ticks={metric === 'direction' ? [0, 90, 180, 270, 360] : undefined}
              tickLine={false}
              axisLine={false}
              fontSize={11}
              stroke="#657361"
              tickFormatter={(value) => String(Math.round(Number(value) * 10) / 10)}
            />
            <Tooltip
              labelFormatter={(label) => `${data.date} · ${hour(String(label))}`}
              formatter={(value, name) => [
                typeof value === 'number' ? `${value.toFixed(2)} ${config.unit}` : 'No reading',
                name === config.actual ? 'Actual' : 'Predicted',
              ]}
              contentStyle={{
                border: '1px solid #e0e7df',
                borderRadius: 10,
                fontSize: 12,
                boxShadow: '0 6px 30px #153e3610',
              }}
            />
            {metric === 'power' && (
              <Area
                type="linear"
                dataKey={config.actual}
                fill="url(#energy-fill)"
                stroke="none"
                tooltipType="none"
                legendType="none"
                isAnimationActive={false}
                connectNulls={false}
              />
            )}
            <Line
              type="linear"
              dataKey={config.predicted}
              stroke="#93825d"
              strokeWidth={2}
              strokeDasharray={metric === 'direction' ? undefined : '5 5'}
              strokeOpacity={metric === 'direction' ? 0 : 1}
              dot={metric === 'direction' ? { r: 3, fill: '#93825d' } : false}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
              connectNulls={false}
            />
            <Line
              type="linear"
              dataKey={config.actual}
              stroke="#267359"
              strokeWidth={2.5}
              strokeOpacity={metric === 'direction' ? 0 : 1}
              dot={metric === 'direction' ? { r: 3, fill: '#267359' } : false}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
              connectNulls={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-footer">
        <span>
          {metric === 'power'
            ? 'Energy totals integrate hourly power, using matched actual and forecast intervals.'
            : metric === 'direction'
              ? 'Direction uses individual points to preserve the 0° / 360° boundary.'
              : 'Gaps represent unavailable readings. Forecasts continue through the selected day.'}
        </span>
        <button
          className="text-button"
          aria-expanded={table}
          onClick={() => setTable((value) => !value)}
        >
          <ChartNoAxesCombined size={14} /> {table ? 'Hide' : 'View'} data table
        </button>
      </div>
      {table && (
        <div className="data-table">
          <table>
            <caption>
              {config.label} · {data.date} · {data.timezone}
            </caption>
            <thead>
              <tr>
                <th scope="col">Interval start</th>
                <th scope="col">Actual ({config.unit})</th>
                <th scope="col">Predicted ({config.unit})</th>
              </tr>
            </thead>
            <tbody>
              {turbine.history.map((point) => (
                <tr key={point.time}>
                  <th scope="row">{hour(point.time)}</th>
                  <td>{format(point[config.actual])}</td>
                  <td>{format(point[config.predicted])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
