"""Dependency-free empirical wind-to-power baseline (Person 2)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from wind_forecast.contracts import (
    ForecastRequest,
    ForecastResult,
    ForecastRow,
    Observation,
    WeatherBundle,
)


class EmpiricalPowerCurve:
    """Predict mean historical normalized power in one m/s wind-speed bins."""

    model_id = "empirical-power-curve-v0"

    def __init__(self) -> None:
        self._bins: dict[str, dict[int, float]] = {}
        self._global: dict[int, float] = {}

    def fit(self, observations: list[Observation]) -> "EmpiricalPowerCurve":
        grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        pooled: dict[int, list[float]] = defaultdict(list)
        for row in observations:
            if row.wind_ms is None or row.power_norm is None or row.quality_flag != "ok":
                continue
            speed_bin = int(row.wind_ms)
            grouped[row.turbine_id][speed_bin].append(row.power_norm)
            pooled[speed_bin].append(row.power_norm)
        self._bins = {
            turbine: {key: sum(values) / len(values) for key, values in bins.items()}
            for turbine, bins in grouped.items()
        }
        self._global = {key: sum(values) / len(values) for key, values in pooled.items()}
        if not self._global:
            raise ValueError("no valid wind_ms/power_norm training pairs")
        return self

    def predict(
        self,
        request: ForecastRequest,
        observations: list[Observation],
        weather: WeatherBundle,
    ) -> ForecastResult:
        del observations  # This baseline does not use future measured observations.
        _audit_weather(request, weather)
        output: list[ForecastRow] = []
        by_turbine = {turbine: self._bins.get(turbine, self._global) for turbine in request.turbine_ids}
        for point in sorted(weather.rows, key=lambda row: (row.turbine_id, row.valid_time)):
            if point.turbine_id not in by_turbine or point.wind_ms is None:
                continue
            curve = by_turbine[point.turbine_id]
            if not curve:
                curve = self._global
            speed_bin = int(point.wind_ms)
            nearest = min(curve, key=lambda key: abs(key - speed_bin))
            lead = int((point.valid_time - request.issue_time).total_seconds() // 3600)
            output.append(
                ForecastRow(
                    turbine_id=point.turbine_id,
                    issue_time=request.issue_time,
                    valid_time=point.valid_time,
                    lead_hours=lead,
                    prediction=curve[nearest],
                )
            )
        expected = request.horizon_hours * len(request.turbine_ids)
        if len(output) != expected:
            raise ValueError(f"expected {expected} forecast rows, produced {len(output)}")
        return ForecastResult(
            forecast_id=f"forecast-{request.request_id}",
            request_id=request.request_id,
            schema_version="1.0",
            model_id=self.model_id,
            weather_bundle_id=weather.bundle_id,
            input_hash="demo-hash-placeholder",
            created_at=datetime.now(timezone.utc),
            status="degraded" if weather.is_synthetic else "ok",
            is_synthetic=weather.is_synthetic,
            warnings=("Synthetic demo output; not an evaluation forecast.",) if weather.is_synthetic else (),
            rows=tuple(output),
        )


def _audit_weather(request: ForecastRequest, weather: WeatherBundle) -> None:
    if weather.available_at > request.issue_time:
        raise ValueError("weather bundle was unavailable at issue_time")
    if request.mode == "historical" and (
        weather.is_synthetic or weather.provenance_status != "verified_original"
    ):
        raise ValueError("historical mode requires verified, original forecast weather")
    expected = {
        (turbine, request.issue_time + timedelta(hours=lead))
        for turbine in request.turbine_ids
        for lead in range(1, request.horizon_hours + 1)
    }
    actual = {(point.turbine_id, point.valid_time) for point in weather.rows}
    if len(actual) != len(weather.rows) or actual != expected:
        raise ValueError("weather rows must cover each requested turbine/hour exactly once")
