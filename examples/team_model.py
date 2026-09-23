"""Executable Person 2 factory example for a genuinely fitted JSON wind-bin model.

Configure ``model_factory="examples.team_model:load_predictor"`` from the repository
root. Supply your own trained artifact and metadata; no invented learned weights
are bundled with this example. The application checks the artifact SHA256,
training cutoff, target units and model ID before invoking this factory.

Your training script must save a JSON object with these keys:
    schema_version: "wind-bins-v1"
    model_id: exactly the metadata model_id
    trained_through: exactly the metadata's aware ISO timestamp
    wind_height_m: 10 or 100, matched to the forecast weather used for training
    curves: {turbine_id: {integer_wind_bin_as_string: fitted_mean_power, ...}, ...}

Every curve value must be computed from eligible training data. No fitting occurs
inside this loader. This baseline selects the nearest integer m/s bin, matching
the repository baseline's behavior. A teammate can replace this class with their
ML framework while preserving ``predict(request, observations, weather)`` and the
returned ForecastResult. GPU training on Brev belongs in the teammate's training
workflow; this inference example runs on CPU.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wind_forecast.contracts import (
    ForecastRequest,
    ForecastResult,
    ForecastRow,
    Observation,
    WeatherBundle,
    require_utc,
)


class SavedWindBins:
    def __init__(self, artifact: dict, metadata: dict) -> None:
        if artifact.get("schema_version") != "wind-bins-v1":
            raise ValueError("expected a fitted wind-bins-v1 JSON artifact")
        if artifact.get("model_id") != metadata["model_id"]:
            raise ValueError("artifact and metadata model IDs disagree")
        artifact_cutoff = require_utc(
            datetime.fromisoformat(artifact["trained_through"].replace("Z", "+00:00")),
            "trained_through",
        )
        metadata_cutoff = datetime.fromisoformat(metadata["trained_through"].replace("Z", "+00:00"))
        if artifact_cutoff != metadata_cutoff:
            raise ValueError("artifact and metadata training cutoffs disagree")
        if artifact.get("wind_height_m") not in {10, 100}:
            raise ValueError("artifact must declare its forecast wind feature height: 10 or 100 m")
        raw_curves = artifact.get("curves")
        if not isinstance(raw_curves, dict) or not raw_curves:
            raise ValueError("artifact needs fitted curves for each turbine")
        curves: dict[str, dict[int, float]] = {}
        for turbine, raw_bins in raw_curves.items():
            if not isinstance(turbine, str) or not isinstance(raw_bins, dict) or not raw_bins:
                raise ValueError("each turbine must have a nonempty fitted wind-bin mapping")
            curve: dict[int, float] = {}
            for key, value in raw_bins.items():
                wind_bin = int(key)
                if wind_bin < 0 or str(wind_bin) != key:
                    raise ValueError("wind-bin keys must be nonnegative integer strings")
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ValueError("fitted powers must be finite numbers")
                curve[wind_bin] = float(value)
            curves[turbine] = curve
        self.model_id = metadata["model_id"]
        self.trained_through = artifact_cutoff
        self.artifact_sha256 = metadata["artifact_sha256"]
        self.trained_on_synthetic = metadata["trained_on_synthetic"]
        self.wind_height_m = artifact["wind_height_m"]
        self.curves = curves

    def predict(
        self, request: ForecastRequest, observations: list[Observation], weather: WeatherBundle,
    ) -> ForecastResult:
        del observations  # This saved baseline has no observation-lag features.
        if self.trained_through > request.issue_time:
            raise ValueError("saved model was fitted using data beyond this issue time")
        if request.mode != "fixture" and (self.trained_on_synthetic or weather.is_synthetic):
            raise ValueError("real runs require real training data and real forecast weather")
        if weather.available_at > request.issue_time:
            raise ValueError("forecast weather was unavailable at issue time")
        points = {(point.turbine_id, point.valid_time): point for point in weather.rows}
        rows = []
        outside = 0
        for turbine in request.turbine_ids:
            if turbine not in self.curves:
                raise ValueError(f"saved model lacks a fitted curve for {turbine}")
            curve = self.curves[turbine]
            for lead in range(1, request.horizon_hours + 1):
                valid = request.issue_time + timedelta(hours=lead)
                point = points.get((turbine, valid))
                if point is None or point.wind_ms is None or not math.isfinite(point.wind_ms):
                    raise ValueError("complete finite forecast wind is required")
                if point.wind_height_m != self.wind_height_m:
                    raise ValueError("forecast wind height differs from the model's training feature")
                wind_bin = int(point.wind_ms)
                outside += int(wind_bin < min(curve) or wind_bin > max(curve))
                nearest = min(curve, key=lambda candidate: (abs(candidate - wind_bin), candidate))
                rows.append(ForecastRow(turbine, request.issue_time, valid, lead, curve[nearest]))
        warnings = []
        if outside:
            warnings.append(f"{outside} weather values used the nearest trained edge bin.")
        if weather.is_synthetic:
            warnings.append("Synthetic fixture forecast; do not submit as real results.")
        return ForecastResult(
            forecast_id=f"forecast-{request.request_id}", request_id=request.request_id,
            schema_version="1.0", model_id=self.model_id, weather_bundle_id=weather.bundle_id,
            input_hash=self.artifact_sha256,  # Application replaces this with all audited inputs.
            created_at=datetime.now(timezone.utc), status="degraded" if warnings else "ok",
            is_synthetic=weather.is_synthetic, warnings=tuple(warnings), rows=tuple(rows),
        )


def load_predictor(*, model_path: Path, metadata: dict) -> SavedWindBins:
    """Exact factory signature expected by load_team_predictor; no training side effects."""
    artifact = json.loads(model_path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("saved model must contain a JSON object")
    return SavedWindBins(artifact, metadata)
