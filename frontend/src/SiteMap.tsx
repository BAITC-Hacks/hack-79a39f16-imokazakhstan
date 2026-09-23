import { lazy, Suspense, useState } from 'react';
import { Crosshair, Layers, Minus, Plus, Wind } from 'lucide-react';
import { summarizeTurbine } from './data';
import type { Turbine } from './types';

const StreetMap = lazy(() => import('./StreetMap'));
export const percent = (value: number | null) =>
  value === null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
export const deviationTone = (value: number | null) =>
  value === null ? 'neutral' : value < 0 ? 'negative' : 'positive';
export const deviationLabel = (turbine: Turbine) => {
  const summary = summarizeTurbine(turbine);
  return summary.deviationPercent === null
    ? summary.comparedHours
      ? 'Forecast total is zero'
      : 'No comparable data'
    : `${percent(summary.deviationPercent)} versus forecast`;
};

export function TurbineGlyph({ className = '' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 84" fill="none" aria-hidden="true">
      <path d="M32 39L28 79H36L33 39" fill="currentColor" />
      <path
        d="M32 34L29 5Q30 1 33 4L36 30ZM28 37L4 52Q0 53 2 49L24 32ZM36 37L58 52Q63 54 61 49L40 32Z"
        fill="currentColor"
      />
      <circle cx="32" cy="34" r="6" fill="currentColor" stroke="white" strokeWidth="2" />
    </svg>
  );
}

export default function SiteMap({
  turbines,
  selectedId,
  onSelect,
}: {
  turbines: Turbine[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [layer, setLayer] = useState<'site' | 'street'>('site');
  const [zoom, setZoom] = useState(1);
  const centerLat = (turbines[0].latitude + turbines[1].latitude) / 2;
  const centerLon = (turbines[0].longitude + turbines[1].longitude) / 2;
  return (
    <section className="panel map-panel" aria-label="Turbine site map">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">THE BIG PICTURE</span>
          <h2>Two turbines. One view.</h2>
        </div>
        <div className="layer-toggle" aria-label="Map layer">
          <button aria-pressed={layer === 'site'} onClick={() => setLayer('site')}>
            <Layers size={14} /> Site
          </button>
          <button aria-pressed={layer === 'street'} onClick={() => setLayer('street')}>
            Street
          </button>
        </div>
      </div>
      <div className="map-canvas">
        {layer === 'street' ? (
          <Suspense fallback={<div className="map-loading">Loading map…</div>}>
            <StreetMap turbines={turbines} selectedId={selectedId} onSelect={onSelect} />
          </Suspense>
        ) : (
          <>
            <svg
              className="site-grid"
              viewBox="0 0 800 420"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <defs>
                <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
                  <path d="M 48 0 L 0 0 0 48" fill="none" stroke="#c8d5bb" strokeWidth=".6" />
                </pattern>
                <radialGradient id="land">
                  <stop stopColor="#edf2df" />
                  <stop offset="1" stopColor="#dae6d4" />
                </radialGradient>
              </defs>
              <rect width="800" height="420" fill="url(#land)" />
              <rect width="800" height="420" fill="url(#grid)" />
              <g stroke="#bdcfb4" fill="none" opacity=".55">
                <ellipse cx="20" cy="400" rx="230" ry="180" />
                <ellipse cx="20" cy="400" rx="260" ry="210" />
                <ellipse cx="20" cy="400" rx="290" ry="240" />
                <ellipse cx="20" cy="400" rx="320" ry="270" />
                <ellipse cx="810" cy="30" rx="215" ry="160" />
                <ellipse cx="810" cy="30" rx="245" ry="190" />
                <ellipse cx="810" cy="30" rx="275" ry="220" />
              </g>
              <path d="M190 134L610 286" stroke="#94af92" strokeWidth="1.5" strokeDasharray="5 7" />
            </svg>
            <div className="map-site-tag">
              <span className="status-dot" /> Kazakhstan <span className="muted">/</span> Two-site
              pilot
            </div>
            <div className="map-markers" style={{ transform: `scale(${zoom})` }}>
              {turbines.map((turbine, index) => {
                const deviation = summarizeTurbine(turbine).deviationPercent;
                const selected = selectedId === turbine.id;
                const x = 50 + ((turbine.longitude - centerLon) / 0.008) * 100;
                const y = 50 - ((turbine.latitude - centerLat) / 0.006) * 100;
                return (
                  <button
                    key={turbine.id}
                    className={`turbine-pin ${selected ? 'selected' : ''}`}
                    style={{ left: `${x}%`, top: `${y}%` }}
                    onClick={() => onSelect(turbine.id)}
                    aria-label={`Select ${turbine.name}, ${deviationLabel(turbine)}`}
                    aria-pressed={selected}
                  >
                    <span className="turbine-shadow" />
                    <TurbineGlyph className="turbine-glyph" />
                    <span className="pin-card">
                      <span className="pin-number">0{index + 1}</span>
                      <span>
                        <strong>{turbine.name}</strong>
                        <span className={deviationTone(deviation)}>
                          {percent(deviation)}{' '}
                          <small>{deviation === null ? 'unavailable' : 'vs forecast'}</small>
                        </span>
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="map-compass" aria-label="North up">
              <span>N</span>
              <span>↑</span>
            </div>
            <div className="map-tools">
              <button
                aria-label="Zoom in"
                disabled={zoom >= 1.5}
                onClick={() => setZoom((value) => Math.min(1.5, value + 0.25))}
              >
                <Plus size={17} />
              </button>
              <button
                aria-label="Zoom out"
                disabled={zoom <= 1}
                onClick={() => setZoom((value) => Math.max(1, value - 0.25))}
              >
                <Minus size={17} />
              </button>
              <button aria-label="Reset map view" onClick={() => setZoom(1)}>
                <Crosshair size={17} />
              </button>
            </div>
            <div className="site-caption">
              Site schematic · verified coordinates · illustrative background
            </div>
          </>
        )}
      </div>
      <div className="map-footer">
        <span>
          <span className="legend-dot green" /> Above forecast
        </span>
        <span>
          <span className="legend-dot amber" /> Below forecast
        </span>
        <span className="map-hint">
          <Wind size={13} /> Select a turbine to explore
        </span>
      </div>
    </section>
  );
}
