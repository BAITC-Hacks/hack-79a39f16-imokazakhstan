"""Dependency-free empirical wind-to-power baseline (Person 2)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
import hashlib
import json
from math import isfinite
from pathlib import Path
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

    model_id = "empirical-power-curve-v1"

    def __init__(self) -> None:
        self._bins: dict[str, dict[int, float]] = {}
        self._global: dict[int, float] = {}
        self.metadata: dict[str, object] = {}

    def fit(self, observations: list[Observation]) -> "EmpiricalPowerCurve":
        self.fit_samples(
            (row.turbine_id, row.wind_ms, row.power_norm)
            for row in observations if row.quality_flag == "ok"
        )
        eligible = [row for row in observations if row.quality_flag == "ok"
                    and row.wind_ms is not None and row.power_norm is not None
                    and isfinite(row.wind_ms) and isfinite(row.power_norm) and row.wind_ms >= 0]
        self.metadata = {
            "training_available_through": max(
                max(row.observed_at, row.available_at) for row in eligible
            ).isoformat(),
            "time_basis": "aware",
        }
        return self

    def fit_samples(
        self, samples: Iterable[tuple[str, float | None, float | None]]
    ) -> "EmpiricalPowerCurve":
        """Fit numeric pairs; operational prediction needs verified time metadata."""
        grouped: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        pooled: dict[int, list[float]] = defaultdict(list)
        for turbine, wind, power in samples:
            if wind is None or power is None or not isfinite(wind) or not isfinite(power):
                continue
            if wind < 0:
                continue
            speed_bin = int(wind)
            grouped[turbine][speed_bin].append(power)
            pooled[speed_bin].append(power)
        self._bins = {
            turbine: {key: sum(values) / len(values) for key, values in bins.items()}
            for turbine, bins in grouped.items()
        }
        self._global = {key: sum(values) / len(values) for key, values in pooled.items()}
        self.metadata = {"time_basis": "unverified"}
        if not self._global:
            raise ValueError("no valid wind_ms/power_norm training pairs")
        return self

    def predict_power(self, turbine_id: str, wind_ms: float) -> float:
        """Evaluate the curve; this alone is not an operational weather forecast."""
        if not self._global:
            raise ValueError("fit or load the model before prediction")
        if not isfinite(wind_ms) or wind_ms < 0:
            raise ValueError("wind_ms must be finite and nonnegative")
        curve = self._bins.get(turbine_id, self._global)
        nearest = min(curve, key=lambda key: (abs(key - int(wind_ms)), key))
        return curve[nearest]

    def save(self, path: str | Path) -> str:
        if not self._global:
            raise ValueError("cannot save an unfitted model")
        payload = {"schema_version": 1, "model_id": self.model_id,
                   "bins": self._bins, "global": self._global, "metadata": self.metadata}
        content = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
        Path(path).write_text(content, encoding="utf-8")
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> "EmpiricalPowerCurve":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload["schema_version"] != 1 or payload["model_id"] != cls.model_id:
            raise ValueError("unsupported model artifact")
        model = cls()
        model._bins = {t: {int(k): v for k, v in bins.items()}
                       for t, bins in payload["bins"].items()}
        model._global = {int(k): v for k, v in payload["global"].items()}
        model.metadata = payload["metadata"]
        if not model._global or any(
            not isfinite(v) for bins in [model._global, *model._bins.values()]
            for v in bins.values()
        ):
            raise ValueError("invalid model artifact")
        return model

    def predict(
        self,
        request: ForecastRequest,
        observations: list[Observation],
        weather: WeatherBundle,
    ) -> ForecastResult:
        del observations  # This baseline does not use future measured observations.
        if not self._global:
            raise ValueError("fit or load the model before prediction")
        if self.metadata.get("time_basis") != "aware":
            raise ValueError("verify training timezone and availability before forecasting")
        cutoff = datetime.fromisoformat(self.metadata["training_available_through"])
        if cutoff > request.issue_time:
            raise ValueError("model training data were unavailable at issue_time")
        _audit_weather(request, weather)
        output: list[ForecastRow] = []
        by_turbine = {turbine: self._bins.get(turbine, self._global) for turbine in request.turbine_ids}
        for point in sorted(weather.rows, key=lambda row: (row.turbine_id, row.valid_time)):
            if point.turbine_id not in by_turbine or point.wind_ms is None:
                continue
            lead = int((point.valid_time - request.issue_time).total_seconds() // 3600)
            output.append(
                ForecastRow(
                    turbine_id=point.turbine_id,
                    issue_time=request.issue_time,
                    valid_time=point.valid_time,
                    lead_hours=lead,
                    prediction=self.predict_power(point.turbine_id, point.wind_ms),
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
            input_hash=hashlib.sha256(json.dumps(
                {"request": asdict(request), "weather": asdict(weather),
                 "bins": self._bins, "global": self._global, "metadata": self.metadata},
                default=str, sort_keys=True, allow_nan=False,
            ).encode()).hexdigest(),
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
