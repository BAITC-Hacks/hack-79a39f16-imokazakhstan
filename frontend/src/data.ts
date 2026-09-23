import type {
  DashboardData,
  HistoryPoint,
  Period,
  RuntimeConfig,
  Turbine,
  TurbineSummary,
  WeatherEvent,
} from './types';

export const DASHBOARD_TIMEZONE = 'Asia/Qyzylorda';
export const ENERGY_UNIT = 'n.u.·h';
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const UTC_OFFSET = 5 * HOUR;
const SITE_LOCATIONS = [
  { id: 'turbine_1', name: 'Demo site 01', latitude: 43.64515, longitude: 78.535604 },
  { id: 'turbine_2', name: 'Demo site 02', latitude: 43.643198, longitude: 78.538828 },
] as const;

function localDate(now: Date, period: Period): string {
  return new Date(now.getTime() + UTC_OFFSET - (period === 'yesterday' ? DAY : 0))
    .toISOString()
    .slice(0, 10);
}

const round = (value: number, digits = 3): number => Number(value.toFixed(digits));
const direction = (value: number): number => round((value + 360) % 360, 1);

/** A deterministic UI fixture, never an operational power or weather prediction. */
export function createDemoDashboard(period: Period, now = new Date()): DashboardData {
  const date = localDate(now, period);
  const start = Date.parse(`${date}T00:00:00+05:00`);
  const turbines: Turbine[] = SITE_LOCATIONS.map((site, index) => ({
    ...site,
    status: index === 0 ? 'operating' : 'attention',
    history: Array.from({ length: 24 }, (_, hour): HistoryPoint => {
      const time = start + hour * HOUR;
      const completed = time + HOUR <= now.getTime();
      // Independent authored display curves; no weather-to-power inference runs here.
      const predictedPower = round(0.59 + 0.19 * Math.sin((hour - 3) / 4.4) + index * 0.055);
      const actualPower = round(
        predictedPower * (index === 0 ? 1.064 : 0.927) + 0.021 * Math.sin(hour / 2.1 + index),
      );
      const temperature = round(7.5 + 5.5 * Math.sin((hour - 7) / 4.9) - index * 0.3, 1);
      const wind = round(6.8 + 2.5 * Math.sin((hour + 1) / 5) + index * 0.25, 1);
      const windDirection = direction(230 + 30 * Math.sin(hour / 6) + index * 8);
      return {
        time: new Date(time).toISOString(),
        actualPower: completed ? actualPower : null,
        predictedPower,
        actualTemperature: completed ? round(temperature + 0.7 * Math.sin(hour / 2), 1) : null,
        predictedTemperature: temperature,
        actualWindSpeed: completed ? round(wind + 0.4 * Math.cos(hour / 3), 1) : null,
        predictedWindSpeed: wind,
        actualWindDirection: completed ? direction(windDirection + 9 * Math.sin(hour / 4)) : null,
        predictedWindDirection: windDirection,
      };
    }),
  }));

  const anchor = Math.ceil(now.getTime() / HOUR) * HOUR;
  const scenarios: Array<{
    type: WeatherEvent['type'];
    severity: WeatherEvent['severity'];
    offset: number;
    duration: number;
    title: string;
    description: string;
    recommendation: string;
    energy: [number, number];
  }> = [
    {
      type: 'rain',
      severity: 'low',
      offset: 2,
      duration: 4,
      title: 'Passing rain',
      description: 'Synthetic scenario: light rain accompanies a small change in wind.',
      recommendation: 'Review the wind forecast and monitor observed output.',
      energy: [2.34, 2.17],
    },
    {
      type: 'frost',
      severity: 'moderate',
      offset: 8,
      duration: 5,
      title: 'Frost window',
      description: 'Synthetic scenario: below-freezing temperatures could create icing risk.',
      recommendation: 'Check icing indicators and the approved cold-weather procedure.',
      energy: [2.12, 1.93],
    },
    {
      type: 'snow',
      severity: 'moderate',
      offset: 17,
      duration: 6,
      title: 'Snow showers',
      description: 'Synthetic scenario: snow and colder air may affect turbine availability.',
      recommendation: 'Review local observations and maintenance access conditions.',
      energy: [2.61, 2.42],
    },
    {
      type: 'clear',
      severity: 'low',
      offset: 28,
      duration: 7,
      title: 'Clear weather',
      description: 'Synthetic scenario: no precipitation, with steady moderate wind.',
      recommendation: 'Continue normal monitoring; output still depends on wind and availability.',
      energy: [4.73, 4.52],
    },
    {
      type: 'storm',
      severity: 'high',
      offset: 39,
      duration: 4,
      title: 'Storm watch',
      description: 'Synthetic scenario: strong gusts could reduce availability or trigger cut-out.',
      recommendation: 'Review turbine operating limits and the approved severe-weather procedure.',
      energy: [1.36, 1.19],
    },
  ];
  const events = scenarios.map((scenario): WeatherEvent => ({
    id: `demo-${scenario.type}`,
    type: scenario.type,
    startsAt: new Date(anchor + scenario.offset * HOUR).toISOString(),
    endsAt: new Date(anchor + (scenario.offset + scenario.duration) * HOUR).toISOString(),
    severity: scenario.severity,
    title: scenario.title,
    description: scenario.description,
    recommendation: scenario.recommendation,
    predictedEnergy: { turbine_1: scenario.energy[0], turbine_2: scenario.energy[1] },
  }));

  return {
    schemaVersion: 1,
    provenance: 'synthetic',
    generatedAt: now.toISOString(),
    timezone: DASHBOARD_TIMEZONE,
    date,
    period,
    turbines,
    events,
  };
}

export function summarizeTurbine(turbine: Turbine): TurbineSummary {
  const paired = turbine.history.filter(
    (row) =>
      row.actualPower !== null &&
      row.predictedPower !== null &&
      Number.isFinite(row.actualPower) &&
      Number.isFinite(row.predictedPower),
  );
  if (paired.length === 0) {
    return { actualEnergy: null, predictedEnergy: null, deviationPercent: null, comparedHours: 0 };
  }
  const actualEnergy = paired.reduce((sum, row) => sum + row.actualPower!, 0);
  const predictedEnergy = paired.reduce((sum, row) => sum + row.predictedPower!, 0);
  const deviation =
    predictedEnergy === 0 ? null : ((actualEnergy - predictedEnergy) / predictedEnergy) * 100;
  return {
    actualEnergy: Number.isFinite(actualEnergy) ? actualEnergy : null,
    predictedEnergy: Number.isFinite(predictedEnergy) ? predictedEnergy : null,
    deviationPercent: deviation !== null && Number.isFinite(deviation) ? deviation : null,
    comparedHours: paired.length,
  };
}

function invalid(path: string, reason: string): never {
  throw new Error(`Invalid dashboard data: ${path} ${reason}.`);
}

function object(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return invalid(path, 'must be an object');
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    return invalid(path, 'must be a nonempty string');
  }
  return value;
}

function oneOf<T extends string>(value: unknown, options: readonly T[], path: string): T {
  if (typeof value !== 'string' || !options.includes(value as T)) {
    return invalid(path, `must be one of ${options.join(', ')}`);
  }
  return value as T;
}

function finite(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return invalid(path, 'must be a finite number');
  }
  return value;
}

function optionalNumber(value: unknown, path: string): number | null {
  return value === null ? null : finite(value, path);
}

function timestamp(value: unknown, path: string): number {
  const raw = text(value, path);
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/.exec(
      raw,
    );
  if (!match) return invalid(path, 'must be an ISO timestamp with a timezone');
  const [, year, month, day, hour, minute, second, , offset] = match;
  const monthDays = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  const offsetHours = offset === 'Z' ? 0 : Number(offset.slice(1, 3));
  const offsetMinutes = offset === 'Z' ? 0 : Number(offset.slice(4, 6));
  if (
    Number(month) < 1 ||
    Number(month) > 12 ||
    Number(day) < 1 ||
    Number(day) > monthDays ||
    Number(hour) > 23 ||
    Number(minute) > 59 ||
    Number(second) > 59 ||
    offsetHours > 14 ||
    offsetMinutes > 59 ||
    (offsetHours === 14 && offsetMinutes !== 0)
  ) {
    return invalid(path, 'must be a valid timestamp');
  }
  const milliseconds = Date.parse(raw);
  if (!Number.isFinite(milliseconds)) return invalid(path, 'must be a valid timestamp');
  return milliseconds;
}

const numericFields = [
  'actualPower',
  'predictedPower',
  'actualTemperature',
  'predictedTemperature',
  'actualWindSpeed',
  'predictedWindSpeed',
  'actualWindDirection',
  'predictedWindDirection',
] as const;

/** Validate shape and timing; proof of provenance remains the upstream adapter's duty. */
export function validateDashboard(
  payload: unknown,
  period: Period,
  now = new Date(),
): DashboardData {
  const data = object(payload, 'response');
  if (data.schemaVersion !== 1) invalid('schemaVersion', 'must equal 1');
  oneOf(data.provenance, ['synthetic', 'verified_original'], 'provenance');
  if (data.timezone !== DASHBOARD_TIMEZONE) invalid('timezone', `must equal ${DASHBOARD_TIMEZONE}`);
  if (data.period !== period) invalid('period', 'does not match the requested period');
  const expectedDate = localDate(now, period);
  if (data.date !== expectedDate) invalid('date', 'does not match the requested local day');
  const generatedAt = timestamp(data.generatedAt, 'generatedAt');
  if (generatedAt > now.getTime() + 5 * 60_000) invalid('generatedAt', 'is in the future');
  if (!Array.isArray(data.turbines) || data.turbines.length !== 2) {
    invalid('turbines', 'must contain the two configured sites');
  }
  const dayStart = Date.parse(`${expectedDate}T00:00:00+05:00`);
  const seenIds = new Set<string>();
  for (const [index, rawTurbine] of (data.turbines as unknown[]).entries()) {
    const path = `turbines[${index}]`;
    const turbine = object(rawTurbine, path);
    const id = text(turbine.id, `${path}.id`);
    const site = SITE_LOCATIONS.find((location) => location.id === id);
    if (!site || seenIds.has(id)) invalid(`${path}.id`, 'must be a unique configured site ID');
    seenIds.add(id);
    text(turbine.name, `${path}.name`);
    oneOf(turbine.status, ['operating', 'attention', 'offline'], `${path}.status`);
    const latitude = finite(turbine.latitude, `${path}.latitude`);
    const longitude = finite(turbine.longitude, `${path}.longitude`);
    if (
      Math.abs(latitude - site!.latitude) > 0.000001 ||
      Math.abs(longitude - site!.longitude) > 0.000001
    ) {
      invalid(path, 'coordinates do not match the configured site');
    }
    if (!Array.isArray(turbine.history) || turbine.history.length !== 24) {
      invalid(`${path}.history`, 'must contain all 24 hourly intervals (null for missing values)');
    }
    for (const [hour, rawRow] of (turbine.history as unknown[]).entries()) {
      const rowPath = `${path}.history[${hour}]`;
      const row = object(rawRow, rowPath);
      const time = timestamp(row.time, `${rowPath}.time`);
      if (time !== dayStart + hour * HOUR) {
        invalid(
          `${rowPath}.time`,
          'must be the next unique hourly interval of the requested local day',
        );
      }
      for (const field of numericFields) {
        const value = optionalNumber(row[field], `${rowPath}.${field}`);
        if (value !== null && field.endsWith('WindSpeed') && value < 0) {
          invalid(`${rowPath}.${field}`, 'must be nonnegative');
        }
        if (value !== null && field.endsWith('WindDirection') && (value < 0 || value >= 360)) {
          invalid(`${rowPath}.${field}`, 'must be in [0, 360)');
        }
        if (
          field.startsWith('actual') &&
          time + HOUR > Math.min(generatedAt, now.getTime()) &&
          value !== null
        ) {
          invalid(`${rowPath}.${field}`, 'must be null until the hourly interval is complete');
        }
      }
    }
  }
  if (!Array.isArray(data.events)) invalid('events', 'must be an array');
  const eventIds = new Set<string>();
  for (const [index, rawEvent] of (data.events as unknown[]).entries()) {
    const path = `events[${index}]`;
    const event = object(rawEvent, path);
    const id = text(event.id, `${path}.id`);
    if (eventIds.has(id)) invalid(`${path}.id`, 'must be unique');
    eventIds.add(id);
    oneOf(event.type, ['rain', 'frost', 'snow', 'clear', 'storm'], `${path}.type`);
    oneOf(event.severity, ['low', 'moderate', 'high'], `${path}.severity`);
    for (const field of ['title', 'description', 'recommendation'] as const) {
      text(event[field], `${path}.${field}`);
    }
    const startsAt = timestamp(event.startsAt, `${path}.startsAt`);
    const endsAt = timestamp(event.endsAt, `${path}.endsAt`);
    if (startsAt < generatedAt || endsAt <= startsAt || endsAt > generatedAt + 48 * HOUR) {
      invalid(path, 'must be a positive event interval within the next 48 hours');
    }
    if (endsAt <= now.getTime()) invalid(path, 'has already ended and cannot be an upcoming event');
    if (startsAt % HOUR !== 0 || endsAt % HOUR !== 0) {
      invalid(path, 'event intervals must align to hourly forecast boundaries');
    }
    const energies = object(event.predictedEnergy, `${path}.predictedEnergy`);
    if (
      Object.keys(energies).length !== seenIds.size ||
      Object.keys(energies).some((id) => !seenIds.has(id))
    ) {
      invalid(`${path}.predictedEnergy`, 'must map each configured site exactly once');
    }
    for (const id of seenIds) optionalNumber(energies[id], `${path}.predictedEnergy.${id}`);
  }
  return payload as DashboardData;
}

export async function loadDashboard(
  period: Period,
  config: RuntimeConfig,
  signal?: AbortSignal,
): Promise<DashboardData> {
  if (signal?.aborted) throw signal.reason ?? new DOMException('Request aborted', 'AbortError');
  if (config.dataMode === 'demo') return createDemoDashboard(period);
  if (config.dataMode !== 'api') throw new Error('Unsupported dashboard data mode.');
  if (!config.apiUrl?.trim()) throw new Error('Set apiUrl to the dashboard API endpoint.');
  const origin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
  let url: URL;
  try {
    url = new URL(config.apiUrl, origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
      throw new Error();
  } catch {
    throw new Error('The dashboard API endpoint must be an HTTP(S) URL without credentials.');
  }
  url.searchParams.set('period', period);
  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: 'GET',
      signal,
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(
      'Unable to reach the dashboard API. Check the endpoint and network connection.',
    );
  }
  if (!response.ok) throw new Error(`The dashboard API returned HTTP ${response.status}.`);
  if (!/\bapplication\/(?:[\w.+-]+\+)?json\b/i.test(response.headers.get('content-type') ?? '')) {
    throw new Error('The dashboard API must return JSON (Content-Type: application/json).');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error('The dashboard API returned malformed JSON.');
  }
  return validateDashboard(payload, period);
}
