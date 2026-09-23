/** Display contract only. The Python shared contracts remain authoritative upstream. */
export type Period = 'today' | 'yesterday';

export interface RuntimeConfig {
  dataMode: 'demo' | 'api';
  /** Full dashboard JSON endpoint, e.g. /api/dashboard. */
  apiUrl: string;
  /** Optional navigation link to the existing Python application. */
  backendUrl: string;
}

export interface HistoryPoint {
  /** Start of a one-hour interval, with an explicit timezone offset. */
  time: string;
  actualPower: number | null;
  predictedPower: number | null;
  actualTemperature: number | null;
  predictedTemperature: number | null;
  actualWindSpeed: number | null;
  predictedWindSpeed: number | null;
  actualWindDirection: number | null;
  predictedWindDirection: number | null;
}

export interface Turbine {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  status: 'operating' | 'attention' | 'offline';
  history: HistoryPoint[];
}

export interface WeatherEvent {
  id: string;
  type: 'rain' | 'frost' | 'snow' | 'clear' | 'storm';
  startsAt: string;
  endsAt: string;
  severity: 'low' | 'moderate' | 'high';
  title: string;
  description: string;
  recommendation: string;
  /** Sum of forecast hourly normalized power over the event, in n.u.·h. */
  predictedEnergy: Record<string, number | null>;
}

export interface DashboardData {
  schemaVersion: 1;
  provenance: 'synthetic' | 'verified_original';
  generatedAt: string;
  timezone: string;
  date: string;
  period: Period;
  turbines: Turbine[];
  events: WeatherEvent[];
}

export interface TurbineSummary {
  /** Both totals use only hours with both actual and predicted power. */
  actualEnergy: number | null;
  predictedEnergy: number | null;
  deviationPercent: number | null;
  comparedHours: number;
}
