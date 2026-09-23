import { lazy, Suspense, useEffect, useState } from 'react';
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  ArrowRight,
  CalendarDays,
  Check,
  ChevronDown,
  CloudLightning,
  CloudRain,
  CloudSnow,
  ExternalLink,
  Gauge,
  LayoutDashboard,
  MapPin,
  RefreshCw,
  Snowflake,
  Sun,
  Thermometer,
  Wind,
  X,
} from 'lucide-react';
import { ENERGY_UNIT, loadDashboard, summarizeTurbine } from './data';
import SiteMap, { deviationTone, percent, TurbineGlyph } from './SiteMap';
const HistoryChart = lazy(() => import('./HistoryChart'));
import type { DashboardData, Period, RuntimeConfig, Turbine, WeatherEvent } from './types';

function safeUrl(value: unknown, relative = false): value is string {
  if (typeof value !== 'string') return false;
  if (relative && /^\/(?!\/)/.test(value) && !value.includes('\\')) return true;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

async function loadConfig(signal: AbortSignal): Promise<RuntimeConfig> {
  const response = await fetch('/runtime-config.json', { signal, cache: 'no-store' });
  if (!response.ok)
    throw new Error('Could not load dashboard configuration. Check runtime-config.json.');
  const config: unknown = await response.json();
  if (
    !config ||
    typeof config !== 'object' ||
    !('dataMode' in config) ||
    !('apiUrl' in config) ||
    !('backendUrl' in config) ||
    !['demo', 'api'].includes(String(config.dataMode)) ||
    !safeUrl(config.apiUrl, true) ||
    (config.backendUrl !== '' && !safeUrl(config.backendUrl))
  )
    throw new Error('Dashboard configuration is invalid. Check the data mode and connection URLs.');
  return config as RuntimeConfig;
}

const number = (value: number | null, digits = 2) => (value === null ? '—' : value.toFixed(digits));
const weatherIcons = {
  rain: CloudRain,
  frost: Snowflake,
  snow: CloudSnow,
  clear: Sun,
  storm: CloudLightning,
};
const statusText = { operating: 'Operating', attention: 'Review advised', offline: 'Offline' };
const dateLabel = (date: string) =>
  new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${date}T12:00:00Z`));

function SummaryCards({ turbine, period }: { turbine: Turbine; period: Period }) {
  const summary = summarizeTurbine(turbine);
  const latest = [...turbine.history].reverse().find((point) => point.actualWindSpeed !== null);
  const positive = summary.deviationPercent !== null && summary.deviationPercent >= 0;
  return (
    <div className="summary-grid">
      <article className="summary-card">
        <span className="summary-icon">
          <Gauge size={19} />
        </span>
        <p>
          Actual energy <span>{period === 'today' ? 'today' : 'yesterday'}</span>
        </p>
        <strong>
          {number(summary.actualEnergy)} <small>{ENERGY_UNIT}</small>
        </strong>
        <span className="summary-foot">
          {turbine.name} · {summary.comparedHours} matched hours
        </span>
      </article>
      <article className="summary-card">
        <span className="summary-icon">
          <Activity size={19} />
        </span>
        <p>Predicted energy</p>
        <strong>
          {number(summary.predictedEnergy)} <small>{ENERGY_UNIT}</small>
        </strong>
        <span className="summary-foot">Same completed hours as actual</span>
      </article>
      <article className="summary-card">
        <span
          className={`summary-icon ${summary.deviationPercent !== null && !positive ? 'warm' : ''}`}
        >
          {summary.deviationPercent === null ? (
            <Activity size={19} />
          ) : positive ? (
            <ArrowUpRight size={19} />
          ) : (
            <ArrowDownRight size={19} />
          )}
        </span>
        <p>Against the forecast</p>
        <strong className={deviationTone(summary.deviationPercent)}>
          {percent(summary.deviationPercent)}
        </strong>
        <span className="summary-foot">
          {summary.deviationPercent === null
            ? summary.comparedHours
              ? 'Forecast total is zero'
              : 'Awaiting comparable observations'
            : `${positive ? 'Above' : 'Below'} expected generation`}
        </span>
      </article>
      <article className="summary-card">
        <span className="summary-icon">
          <Wind size={19} />
        </span>
        <p>Latest observed wind</p>
        <strong>
          {number(latest?.actualWindSpeed ?? null, 1)} <small>m/s</small>
        </strong>
        <span className="summary-foot">
          {latest
            ? `${number(latest.actualWindDirection, 0)}° direction · ${number(latest.actualTemperature, 1)}°C`
            : 'Awaiting a completed interval'}
        </span>
      </article>
    </div>
  );
}

function TurbineDetail({ turbine, data }: { turbine: Turbine; data: DashboardData }) {
  const summary = summarizeTurbine(turbine);
  const energyScale = Math.max(
    Math.abs(summary.actualEnergy ?? 0),
    Math.abs(summary.predictedEnergy ?? 0),
    0.001,
  );
  const weatherHistory = [...turbine.history].reverse();
  const temperature = weatherHistory.find((point) => point.actualTemperature !== null);
  const windSpeed = weatherHistory.find((point) => point.actualWindSpeed !== null);
  const windDirection = weatherHistory.find((point) => point.actualWindDirection !== null);
  const interval = (time: string | undefined) => {
    if (!time) return 'No reading';
    const formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: data.timezone,
      hour: '2-digit',
      minute: '2-digit',
    });
    return `${formatter.format(new Date(time))}–${formatter.format(new Date(Date.parse(time) + 3_600_000))}`;
  };
  return (
    <aside className="panel detail-panel" aria-label="Selected turbine details">
      <div className="detail-title">
        <span className="eyebrow">SELECTED TURBINE</span>
        <span className={`status-pill ${turbine.status}`}>
          <span className="status-dot" />
          {statusText[turbine.status]}
        </span>
      </div>
      <div className="turbine-identity">
        <div className="turbine-avatar">
          <TurbineGlyph />
        </div>
        <div>
          <h2>{turbine.name}</h2>
          <span>{turbine.id.replace('_', ' ').toUpperCase()} / KAZAKHSTAN</span>
        </div>
      </div>
      <div className="coordinates">
        <MapPin size={14} />
        <span>
          {turbine.latitude.toFixed(6)}° N<br />
          {turbine.longitude.toFixed(6)}° E
        </span>
        <a
          href={`https://www.openstreetmap.org/?mlat=${turbine.latitude}&mlon=${turbine.longitude}#map=17/${turbine.latitude}/${turbine.longitude}`}
          target="_blank"
          rel="noreferrer"
          aria-label={`Open ${turbine.name} coordinates in OpenStreetMap`}
        >
          <ExternalLink size={14} />
        </a>
      </div>
      <div className="detail-divider" />
      <div className="detail-energy">
        <span>Energy comparison</span>
        <span>{summary.comparedHours} / 24 hours</span>
      </div>
      <div className="energy-bar-row">
        <span>Actual</span>
        <div className="energy-bar" aria-hidden="true">
          <i style={{ width: `${(Math.abs(summary.actualEnergy ?? 0) / energyScale) * 100}%` }} />
        </div>
        <strong>{number(summary.actualEnergy)}</strong>
      </div>
      <div className="energy-bar-row prediction">
        <span>Predicted</span>
        <div className="energy-bar" aria-hidden="true">
          <i
            style={{ width: `${(Math.abs(summary.predictedEnergy ?? 0) / energyScale) * 100}%` }}
          />
        </div>
        <strong>{number(summary.predictedEnergy)}</strong>
      </div>
      <p className="unit-note">Normalized energy ({ENERGY_UNIT}); rated capacity is unconfirmed.</p>
      <div className="detail-weather">
        <span>
          <Thermometer size={16} />
          <strong>{number(temperature?.actualTemperature ?? null, 1)}°</strong>
          <small>Temperature</small>
          <time dateTime={temperature?.time}>{interval(temperature?.time)}</time>
        </span>
        <span>
          <Wind size={16} />
          <strong>
            {number(windSpeed?.actualWindSpeed ?? null, 1)} <small>m/s</small>
          </strong>
          <small>Wind speed</small>
          <time dateTime={windSpeed?.time}>{interval(windSpeed?.time)}</time>
        </span>
        <span>
          <ArrowUpRight size={16} />
          <strong>{number(windDirection?.actualWindDirection ?? null, 0)}°</strong>
          <small>Direction</small>
          <time dateTime={windDirection?.time}>{interval(windDirection?.time)}</time>
        </span>
      </div>
      <a className="detail-link" href="#performance">
        Explore {data.period === 'today' ? 'today’s' : 'yesterday’s'} performance{' '}
        <ArrowRight size={15} />
      </a>
    </aside>
  );
}

function WeatherOutlook({ data, turbine }: { data: DashboardData; turbine: Turbine }) {
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const events = data.events.filter((event) => !attentionOnly || event.severity !== 'low');
  const eventTime = (value: string) =>
    new Intl.DateTimeFormat('en-GB', {
      timeZone: data.timezone,
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(value));
  return (
    <section className="weather-section" id="weather" aria-labelledby="weather-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">LOOKING AHEAD</span>
          <h2 id="weather-title">The weather, and what comes with it.</h2>
          <p>
            Next 48 hours · expected energy for <strong>{turbine.name}</strong> during each event.
          </p>
        </div>
        <button
          className={`filter-button ${attentionOnly ? 'active' : ''}`}
          aria-pressed={attentionOnly}
          onClick={() => setAttentionOnly((value) => !value)}
        >
          {attentionOnly ? <Check size={14} /> : <CloudLightning size={14} />}
          {attentionOnly ? 'Attention only' : 'All weather'}
          <ChevronDown size={14} />
        </button>
      </div>
      <div className="weather-grid">
        {events.length === 0 ? (
          <div className="empty-events">No weather events match this view.</div>
        ) : (
          events.map((event: WeatherEvent) => {
            const Icon = weatherIcons[event.type];
            const open = expanded === event.id;
            return (
              <article
                key={event.id}
                className={`weather-card ${event.type} ${open ? 'expanded' : ''}`}
              >
                <div className="event-top">
                  <span className={`weather-icon ${event.type}`}>
                    <Icon size={24} strokeWidth={1.5} />
                  </span>
                  <span className={`severity ${event.severity}`}>
                    {event.severity === 'low'
                      ? 'Routine'
                      : event.severity === 'moderate'
                        ? 'Monitor'
                        : 'Attention'}
                  </span>
                </div>
                <h3>{event.title}</h3>
                <p className="event-time">
                  {eventTime(event.startsAt)}
                  <br />
                  <span>to {eventTime(event.endsAt)}</span>
                </p>
                <div className="event-energy">
                  <strong>{number(event.predictedEnergy[turbine.id] ?? null)}</strong>
                  <span>{ENERGY_UNIT}</span>
                </div>
                <span className="event-energy-label">Predicted during event</span>
                <button
                  className="event-toggle"
                  aria-expanded={open}
                  aria-controls={`event-${event.id}`}
                  onClick={() => setExpanded(open ? null : event.id)}
                >
                  {open ? 'Close outlook' : 'View outlook'}
                  {open ? <X size={14} /> : <ArrowUpRight size={14} />}
                </button>
                {open && (
                  <div id={`event-${event.id}`} className="event-description">
                    <p>{event.description}</p>
                    <strong>Suggested action</strong>
                    <p>{event.recommendation}</p>
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>
      <div className="weather-note">
        <span className="status-dot" />
        {data.provenance === 'synthetic'
          ? 'Illustrative weather scenarios and energy values. These are not operational predictions.'
          : 'Event energy is supplied by the forecast service. Follow approved site procedures.'}{' '}
        Event totals cover their own time windows.
      </div>
    </section>
  );
}

export default function App() {
  const [period, setPeriod] = useState<Period>('today');
  const [selectedId, setSelectedId] = useState('turbine_1');
  const [data, setData] = useState<DashboardData | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeoutId = window.setTimeout(
      () =>
        controller.abort(
          new Error('Loading timed out after 15 seconds. Check the connection and try again.'),
        ),
      15_000,
    );
    setLoading(true);
    setError('');
    setData(null);
    void (async () => {
      try {
        const runtime = await loadConfig(controller.signal);
        const dashboard = await loadDashboard(period, runtime, controller.signal);
        if (!controller.signal.aborted) {
          setConfig(runtime);
          setData({
            ...dashboard,
            turbines: [...dashboard.turbines].sort((a, b) => a.id.localeCompare(b.id)),
          });
        }
      } catch (cause) {
        if (active)
          setError(cause instanceof Error ? cause.message : 'The dashboard could not be loaded.');
      } finally {
        window.clearTimeout(timeoutId);
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [period, refresh]);
  const selected = data?.turbines.find((turbine) => turbine.id === selectedId) ?? data?.turbines[0];
  const synthetic = data?.provenance === 'synthetic';
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to dashboard
      </a>
      <aside className="sidebar">
        <a className="brand" href="#overview" aria-label="Aeris overview">
          <span>
            <Wind size={25} />
          </span>
          <strong>
            aeris<span>WIND OPERATIONS</span>
          </strong>
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <a className="nav-item current" href="#overview">
            <LayoutDashboard size={18} /> Overview <span />
          </a>
          <a className="nav-item" href="#performance">
            <Activity size={18} /> Performance
          </a>
          <a className="nav-item" href="#weather">
            <CloudRain size={18} /> Weather outlook
          </a>
        </nav>
        <div className="sidebar-site">
          <div className="site-monogram">
            <TurbineGlyph />
          </div>
          <span className="eyebrow">KAZAKHSTAN PILOT</span>
          <strong>
            A little closer
            <br />
            to the wind.
          </strong>
          <p>Two sites. A connected view of performance and conditions.</p>
          <span className="site-count">
            <span className="status-dot" /> 2 mapped turbines
          </span>
        </div>
        <div className="sidebar-bottom">
          <span className="avatar">KZ</span>
          <div>
            <strong>Wind workspace</strong>
            <span>Kazakhstan pilot</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span> <strong>Overview</strong>
          </div>
          <div className="topbar-right">
            <span className={`data-badge ${synthetic ? 'demo' : ''}`}>
              <span className="status-dot" />
              {loading
                ? 'Connecting'
                : error
                  ? 'Connection issue'
                  : synthetic
                    ? 'Demo environment'
                    : 'Verified source data'}
            </span>
            {config?.backendUrl && (
              <a className="backend-link" href={config.backendUrl} target="_blank" rel="noreferrer">
                Forecast workspace <ExternalLink size={13} />
              </a>
            )}
            <span className="topbar-avatar">KZ</span>
          </div>
        </header>
        <main id="main">
          <div id="overview" className="overview-heading">
            <div>
              <div className="eyebrow">AERIS / SITE INTELLIGENCE</div>
              <h1>Every turn, in perspective.</h1>
              <p>Your turbines, their performance, and the weather ahead.</p>
            </div>
            <div className="date-controls">
              <div className="period-toggle" role="group" aria-label="Comparison day">
                <button aria-pressed={period === 'today'} onClick={() => setPeriod('today')}>
                  Today
                </button>
                <button
                  aria-pressed={period === 'yesterday'}
                  onClick={() => setPeriod('yesterday')}
                >
                  Yesterday
                </button>
              </div>
              <button
                className="refresh-button"
                onClick={() => setRefresh((value) => value + 1)}
                disabled={loading}
                aria-label="Refresh dashboard"
              >
                <RefreshCw size={17} className={loading ? 'spin' : ''} />
              </button>
            </div>
          </div>
          {loading && (
            <div className="loading-state" role="status">
              <Wind size={28} />
              <h2>Reading the wind…</h2>
              <p>Loading turbine performance and the weather outlook.</p>
            </div>
          )}
          {error && (
            <div className="error-state" role="alert">
              <CloudLightning size={30} />
              <h2>We couldn’t load this view.</h2>
              <p>{error}</p>
              <button className="primary-button" onClick={() => setRefresh((value) => value + 1)}>
                Try again <RefreshCw size={15} />
              </button>
              <small>Check the dashboard connection and runtime configuration.</small>
            </div>
          )}
          {data && selected && (
            <>
              <div className={`provenance-note ${synthetic ? 'synthetic' : ''}`}>
                <div>
                  <span className="demo-label">{synthetic ? 'SYNTHETIC DEMO' : 'SOURCE DATA'}</span>
                  <span>
                    {synthetic
                      ? 'Sample readings, forecasts and status. Site coordinates are verified; SCADA mapping is unconfirmed.'
                      : 'Original source readings and forecasts supplied by the connected service.'}
                  </span>
                </div>
                <span className="dashboard-date">
                  <CalendarDays size={13} />
                  {dateLabel(data.date)} · UTC+05:00
                </span>
              </div>
              <div className="selection-heading">
                <span>
                  <span className="status-dot" /> Showing <strong>{selected.name}</strong>
                </span>
                <div className="turbine-switch" role="group" aria-label="Selected turbine">
                  {data.turbines.map((turbine, index) => (
                    <button
                      key={turbine.id}
                      aria-label={`Select turbine ${index + 1}`}
                      aria-pressed={selected.id === turbine.id}
                      onClick={() => setSelectedId(turbine.id)}
                    >
                      Turbine 0{index + 1}
                    </button>
                  ))}
                </div>
              </div>
              <SummaryCards turbine={selected} period={period} />
              <div className="site-layout">
                <SiteMap
                  turbines={data.turbines}
                  selectedId={selected.id}
                  onSelect={setSelectedId}
                />
                <TurbineDetail turbine={selected} data={data} />
              </div>
              <Suspense
                fallback={
                  <div className="panel chart-placeholder" role="status">
                    Loading performance charts…
                  </div>
                }
              >
                <HistoryChart data={data} turbine={selected} />
              </Suspense>
              <WeatherOutlook data={data} turbine={selected} />
              <footer className="footer">
                <span>
                  <Wind size={15} /> aeris <span>/</span> A clearer view of wind energy.
                </span>
                <span>
                  Updated{' '}
                  {new Intl.DateTimeFormat('en-GB', {
                    timeZone: data.timezone,
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  }).format(new Date(data.generatedAt))}{' '}
                  · {data.timezone}
                </span>
              </footer>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
