import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { summarizeTurbine } from './data';
import { deviationLabel, deviationTone, percent } from './SiteMap';
import type { Turbine } from './types';

export default function StreetMap({
  turbines,
  selectedId,
  onSelect,
}: {
  turbines: Turbine[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<Map<string, L.Marker>>(new Map());
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!host.current) return;
    const map = L.map(host.current, { scrollWheelZoom: false, attributionControl: true });
    mapRef.current = map;
    map.fitBounds(
      L.latLngBounds(turbines.map((turbine) => [turbine.latitude, turbine.longitude])),
      { padding: [95, 95], maxZoom: 16 },
    );
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>',
    })
      .on('tileerror', () => setFailed(true))
      .addTo(map);
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      markersRef.current.clear();
    };
    // The two site coordinates are fixed for the lifetime of this layer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    turbines.forEach((turbine) => {
      const existing = markersRef.current.get(turbine.id);
      if (existing) {
        const root = existing.getElement();
        root?.querySelector('.street-pin')?.classList.toggle('selected', selectedId === turbine.id);
        root?.setAttribute('aria-pressed', String(selectedId === turbine.id));
        root?.setAttribute('aria-label', `Select ${turbine.name}, ${deviationLabel(turbine)}`);
        return;
      }
      const element = document.createElement('div');
      element.className = `street-pin ${selectedId === turbine.id ? 'selected' : ''}`;
      const name = document.createElement('strong');
      name.textContent = turbine.name;
      const difference = document.createElement('span');
      const value = summarizeTurbine(turbine).deviationPercent;
      difference.className = deviationTone(value);
      difference.textContent =
        value === null ? 'Comparison unavailable' : `${percent(value)} vs forecast`;
      element.append(name, difference);
      const marker = L.marker([turbine.latitude, turbine.longitude], {
        icon: L.divIcon({
          html: element,
          className: 'street-marker',
          iconSize: [150, 58],
          iconAnchor: [75, 65],
        }),
        keyboard: true,
        title: `Select ${turbine.name}`,
        alt: `Select ${turbine.name}`,
      })
        .on('click', () => selectRef.current(turbine.id))
        .addTo(map);
      marker.getElement()?.setAttribute('aria-pressed', String(selectedId === turbine.id));
      marker
        .getElement()
        ?.setAttribute('aria-label', `Select ${turbine.name}, ${deviationLabel(turbine)}`);
      marker.getElement()?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          selectRef.current(turbine.id);
        }
      });
      markersRef.current.set(turbine.id, marker);
    });
  }, [turbines, selectedId]);
  return (
    <>
      <div ref={host} className="street-map" aria-label="OpenStreetMap turbine locations" />
      {failed && (
        <div className="tile-warning" role="status">
          Street tiles are unavailable. The Site view works offline.
        </div>
      )}
    </>
  );
}
