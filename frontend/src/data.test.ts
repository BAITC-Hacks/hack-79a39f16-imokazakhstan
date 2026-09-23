import { afterEach, describe, expect, it, vi } from 'vitest';
import { createDemoDashboard, loadDashboard, summarizeTurbine, validateDashboard } from './data';
import type { DashboardData, RuntimeConfig, Turbine } from './types';

const now = new Date('2026-09-23T09:30:00Z'); // 14:30 in Asia/Qyzylorda.
const apiConfig: RuntimeConfig = { dataMode: 'api', apiUrl: '/api/dashboard', backendUrl: '' };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('offline demonstration', () => {
  it('produces deterministic, explicitly synthetic data at the two source coordinates', () => {
    const data = createDemoDashboard('today', now);
    expect(data).toEqual(createDemoDashboard('today', now));
    expect(data.provenance).toBe('synthetic');
    expect(data.timezone).toBe('Asia/Qyzylorda');
    expect(data.turbines.map(({ latitude, longitude }) => [latitude, longitude])).toEqual([
      [43.64515, 78.535604],
      [43.643198, 78.538828],
    ]);
    expect(validateDashboard(data, 'today', now)).toBe(data);
    expect(new Set(data.events.map((event) => event.type))).toEqual(
      new Set(['rain', 'frost', 'snow', 'clear', 'storm']),
    );
  });

  it('withholds actual observations for current and future intervals', () => {
    const turbine = createDemoDashboard('today', now).turbines[0];
    expect(turbine.history).toHaveLength(24);
    expect(turbine.history.filter((row) => row.actualPower !== null)).toHaveLength(14);
    expect(turbine.history[14].actualPower).toBeNull();
    expect(turbine.history[14].actualTemperature).toBeNull();
    expect(turbine.history[14].actualWindSpeed).toBeNull();
    expect(turbine.history[14].actualWindDirection).toBeNull();
    expect(turbine.history.every((row) => row.predictedPower !== null)).toBe(true);
    expect(summarizeTurbine(turbine).comparedHours).toBe(14);
  });

  it('uses the site calendar date even when the UTC date differs', () => {
    const dateBoundary = new Date('2026-09-23T21:15:00Z');
    const today = createDemoDashboard('today', dateBoundary);
    const yesterday = createDemoDashboard('yesterday', dateBoundary);
    expect(today.date).toBe('2026-09-24');
    expect(today.turbines[0].history[0].time).toBe('2026-09-23T19:00:00.000Z');
    expect(summarizeTurbine(today.turbines[0]).comparedHours).toBe(2);
    expect(yesterday.date).toBe('2026-09-23');
    expect(summarizeTurbine(yesterday.turbines[0]).comparedHours).toBe(24);
    expect(validateDashboard(yesterday, 'yesterday', dateBoundary)).toBe(yesterday);
  });

  it('has no comparable hours at local midnight and never fetches in demo mode', async () => {
    const fetch = vi.fn(() => {
      throw new Error('Network access forbidden');
    });
    vi.stubGlobal('fetch', fetch);
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-23T19:00:00Z'));
    const data = await loadDashboard('today', { ...apiConfig, dataMode: 'demo' });
    expect(summarizeTurbine(data.turbines[0])).toEqual({
      actualEnergy: null,
      predictedEnergy: null,
      deviationPercent: null,
      comparedHours: 0,
    });
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('like-for-like energy comparison', () => {
  const turbineWithPower = (pairs: Array<[number | null, number | null]>): Turbine => {
    const turbine = createDemoDashboard('yesterday', now).turbines[0];
    turbine.history = pairs.map(([actualPower, predictedPower], index) => ({
      ...turbine.history[index],
      actualPower,
      predictedPower,
    }));
    return turbine;
  };

  it('sums only matched hourly observations and predictions', () => {
    const turbine = turbineWithPower([
      [2, 1],
      [null, 80],
      [70, null],
      [4, 3],
      [0, 0],
    ]);
    expect(summarizeTurbine(turbine)).toEqual({
      actualEnergy: 6,
      predictedEnergy: 4,
      deviationPercent: 50,
      comparedHours: 3,
    });
  });

  it('returns no percentage when the matched forecast total is zero', () => {
    expect(
      summarizeTurbine(
        turbineWithPower([
          [2, 0],
          [0, 0],
        ]),
      ),
    ).toEqual({
      actualEnergy: 2,
      predictedEnergy: 0,
      deviationPercent: null,
      comparedHours: 2,
    });
  });

  it('preserves the sign of below-forecast output and ignores nonfinite values', () => {
    expect(
      summarizeTurbine(
        turbineWithPower([
          [1, 2],
          [Number.NaN, 2],
        ]),
      ),
    ).toEqual({
      actualEnergy: 1,
      predictedEnergy: 2,
      deviationPercent: -50,
      comparedHours: 1,
    });
  });
});

describe('API schema validation', () => {
  it('accepts a verified response only with the same complete display contract', () => {
    const data = createDemoDashboard('today', now);
    // Test-only transport label: the upstream adapter must establish provenance.
    data.provenance = 'verified_original';
    expect(validateDashboard(data, 'today', now)).toBe(data);
  });

  it('rejects events that have ended since a snapshot was generated', () => {
    const previousSnapshot = new Date('2026-09-23T01:00:00Z');
    const data = createDemoDashboard('today', previousSnapshot);
    expect(() => validateDashboard(data, 'today', now)).toThrow('has already ended');
  });

  const invalidCases: Array<[string, (data: DashboardData) => void]> = [
    [
      'nonfinite observation',
      (data) => {
        data.turbines[0].history[0].actualPower = Number.NaN;
      },
    ],
    [
      'nonfinite event energy',
      (data) => {
        data.events[0].predictedEnergy.turbine_1 = Infinity;
      },
    ],
    [
      'unknown provenance',
      (data) => {
        Object.assign(data, { provenance: 'unverified' });
      },
    ],
    [
      'wrong schema',
      (data) => {
        Object.assign(data, { schemaVersion: 2 });
      },
    ],
    [
      'wrong date',
      (data) => {
        data.date = '2026-09-22';
      },
    ],
    [
      'wrong period',
      (data) => {
        data.period = 'yesterday';
      },
    ],
    [
      'wrong timezone',
      (data) => {
        data.timezone = 'UTC';
      },
    ],
    [
      'duplicate site',
      (data) => {
        data.turbines[1].id = 'turbine_1';
      },
    ],
    [
      'unknown site',
      (data) => {
        data.turbines[1].id = 'other';
      },
    ],
    [
      'changed coordinates',
      (data) => {
        data.turbines[0].latitude += 0.01;
      },
    ],
    [
      'missing hour',
      (data) => {
        data.turbines[0].history.pop();
      },
    ],
    [
      'duplicate timestamp',
      (data) => {
        data.turbines[0].history[1].time = data.turbines[0].history[0].time;
      },
    ],
    [
      'unaligned timestamp',
      (data) => {
        data.turbines[0].history[0].time = '2026-09-22T19:30:00Z';
      },
    ],
    [
      'timezone-less timestamp',
      (data) => {
        data.turbines[0].history[0].time = '2026-09-23T00:00:00';
      },
    ],
    [
      'invalid calendar date',
      (data) => {
        data.generatedAt = '2026-02-30T00:00:00Z';
      },
    ],
    [
      'future actual power',
      (data) => {
        data.turbines[0].history[14].actualPower = 0.5;
      },
    ],
    [
      'future actual weather',
      (data) => {
        data.turbines[0].history[14].actualWindSpeed = 4;
      },
    ],
    [
      'negative wind speed',
      (data) => {
        data.turbines[0].history[0].predictedWindSpeed = -1;
      },
    ],
    [
      'invalid direction',
      (data) => {
        data.turbines[0].history[0].predictedWindDirection = 360;
      },
    ],
    [
      'missing numeric field',
      (data) => {
        Object.assign(data.turbines[0].history[0], { actualPower: undefined });
      },
    ],
    [
      'unknown event type',
      (data) => {
        Object.assign(data.events[0], { type: 'unknown' });
      },
    ],
    [
      'duplicate event id',
      (data) => {
        data.events[1].id = data.events[0].id;
      },
    ],
    [
      'reversed event',
      (data) => {
        data.events[0].endsAt = data.events[0].startsAt;
      },
    ],
    [
      'event outside forecast window',
      (data) => {
        data.events[0].endsAt = '2026-09-26T00:00:00Z';
      },
    ],
    [
      'event before creation',
      (data) => {
        data.events[0].startsAt = '2026-09-23T08:00:00Z';
      },
    ],
    [
      'event unknown turbine',
      (data) => {
        data.events[0].predictedEnergy.other = 1;
      },
    ],
    [
      'event missing turbine',
      (data) => {
        delete data.events[0].predictedEnergy.turbine_2;
      },
    ],
  ];

  it.each(invalidCases)('rejects %s', (_name, mutate) => {
    const data = createDemoDashboard('today', now);
    mutate(data);
    expect(() => validateDashboard(data, 'today', now)).toThrow('Invalid dashboard data');
  });
});

describe('API transport', () => {
  it('requests the selected period and forwards cancellation', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const data = createDemoDashboard('yesterday', now);
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(data), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    expect(await loadDashboard('yesterday', apiConfig, controller.signal)).toEqual(data);
    expect(fetch.mock.calls[0][0]).toMatch(/\/api\/dashboard\?period=yesterday$/);
    expect(fetch.mock.calls[0][1].signal).toBe(controller.signal);
    expect(fetch.mock.calls[0][1].cache).toBe('no-store');
  });

  it('surfaces API failures without falling back to synthetic data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('unavailable', { status: 503 })));
    await expect(loadDashboard('today', apiConfig)).rejects.toThrow('HTTP 503');
  });

  it('rejects an HTML application page in place of a dashboard API', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<html>Streamlit</html>', {
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    );
    await expect(loadDashboard('today', apiConfig)).rejects.toThrow('must return JSON');
  });

  it('rejects malformed JSON and refuses credentials embedded in URLs', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response('{broken', {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetch);
    await expect(loadDashboard('today', apiConfig)).rejects.toThrow('malformed JSON');
    await expect(
      loadDashboard('today', {
        ...apiConfig,
        apiUrl: 'https://user:password@example.test/dashboard',
      }),
    ).rejects.toThrow('without credentials');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('honors cancellation before loading either mode', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    controller.abort();
    await expect(loadDashboard('today', apiConfig, controller.signal)).rejects.toHaveProperty(
      'name',
      'AbortError',
    );
    await expect(
      loadDashboard('today', { ...apiConfig, dataMode: 'demo' }, controller.signal),
    ).rejects.toHaveProperty('name', 'AbortError');
    expect(fetch).not.toHaveBeenCalled();
  });
});
