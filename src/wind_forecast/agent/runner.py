"""Guarded, resumable Python workflow; API controllers cannot bypass its checks."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite

from wind_forecast.contracts import ForecastRequest, ForecastResult, Observation, WeatherBundle, WorkflowTrace
from wind_forecast.models.base import Predictor
from wind_forecast.weather.base import WeatherProvider


@dataclass(frozen=True)
class WorkflowRun:
    result: ForecastResult
    trace: WorkflowTrace
    weather: WeatherBundle


def _input_hash(request, observations, weather, model_identity=None) -> str:
    request_data = asdict(request)
    request_data.pop("request_id")
    weather_data = asdict(weather)
    for key in ("bundle_id", "retrieved_at", "source_uri", "availability_basis"):
        weather_data.pop(key)
    weather_data["rows"] = sorted(weather_data["rows"], key=lambda r: (r["turbine_id"], r["valid_time"]))
    payload = {"request": request_data, "observations": [asdict(r) for r in sorted(
        observations, key=lambda r: (r.turbine_id, r.observed_at))], "weather": weather_data,
        "model": model_identity}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    default=lambda v: v.isoformat(), allow_nan=False).encode()).hexdigest()


class WorkflowSession:
    """Each successful step is idempotent; calling a later step early is rejected."""
    def __init__(self, request, observations, weather_provider, predictor, *, model_identity=None,
                 trace=None, extra_warnings=()):
        self.request, self.observations = request, observations
        self.weather_provider, self.predictor = weather_provider, predictor
        self.model_identity = model_identity or {"model_id": predictor.model_id}
        self.trace = trace or WorkflowTrace(request.request_id)
        self.weather = None
        self.result = None
        self.stage = 0
        self.summary = {}
        self.extra_warnings = tuple(extra_warnings)
        self.expected = {(t, request.issue_time + timedelta(hours=h)) for t in request.turbine_ids
                         for h in range(1, request.horizon_hours + 1)}

    def record(self, step, status, detail):
        self.trace.events.append({"run_id": self.request.request_id, "step": step,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "simulated_issue_time": self.request.issue_time.isoformat(), "status": status,
            "detail": detail})

    def _ready(self, stage):
        if self.stage < stage:
            raise ValueError("workflow tools must run fetch → audit → predict → inspect → save")

    def fetch_weather(self):
        if self.stage < 1:
            self.record("fetch_weather", "started", "Retrieve configured forecast weather")
            self.weather = self.weather_provider.fetch(self.request)
            self.stage = 1
            self.record("fetch_weather", "ok", f"{self.weather.provider}: {len(self.weather.rows)} rows")
        return {"provider": self.weather.provider, "rows": len(self.weather.rows),
                "available_at": self.weather.available_at.isoformat(),
                "provenance": self.weather.provenance_status, "synthetic": self.weather.is_synthetic}

    def audit_inputs(self):
        self._ready(1)
        if self.stage < 2:
            w, q = self.weather, self.request
            if type(w.is_synthetic) is not bool or w.provenance_status not in {"synthetic","unverified","verified_original"}:
                raise ValueError("invalid weather provenance fields")
            if w.is_synthetic != (w.provenance_status == "synthetic"):
                raise ValueError("weather synthetic flag and provenance disagree")
            if not w.run_init_time <= w.available_at <= q.issue_time:
                raise ValueError("weather initialization/availability is inconsistent or after issue_time")
            if q.mode != "fixture" and (w.is_synthetic or w.provenance_status != "verified_original"):
                raise ValueError("real runs require verified original forecast weather")
            if q.mode != "fixture" and w.retrieved_at < w.available_at:
                raise ValueError("weather was retrieved before its asserted availability")
            if q.mode == "fixture" and not w.is_synthetic:
                raise ValueError("fixture mode requires clearly synthetic weather")
            if not all((w.bundle_id, w.source_hash, w.availability_basis, w.source_uri)):
                raise ValueError("weather provenance fields must not be empty")
            if len(w.rows) != len(self.expected) or {(r.turbine_id, r.valid_time) for r in w.rows} != self.expected:
                raise ValueError("weather must cover each requested turbine/hour exactly once")
            for row in w.rows:
                if row.wind_ms is None or isinstance(row.wind_ms,bool) or not isfinite(row.wind_ms) or row.wind_ms < 0:
                    raise ValueError("weather wind must be finite and nonnegative")
                for value in (row.temp_c, row.wind_direction_deg, row.wind_height_m):
                    if value is not None and (isinstance(value,bool) or not isfinite(value)):
                        raise ValueError("weather contains non-finite values")
            seen = set()
            for row in self.observations:
                if not row.observed_at <= row.available_at <= q.issue_time:
                    raise ValueError("observation timestamp/availability is invalid at issue_time")
                key = (row.turbine_id, row.observed_at)
                if key in seen or row.turbine_id not in q.turbine_ids:
                    raise ValueError("duplicate or unexpected turbine observation")
                seen.add(key)
                for value in (row.wind_ms, row.power_norm, row.temp_c):
                    if value is not None and (isinstance(value,bool) or not isfinite(value)):
                        raise ValueError("observation contains non-finite values")
            self.stage = 2
            self.record("audit_inputs", "ok", f"{len(self.observations)} eligible observations; weather passed")
        return {"eligible_observations": len(self.observations), "audit": "passed"}

    def predict_power(self):
        self._ready(2)
        if self.stage < 3:
            self.record("predict_power", "started", self.predictor.model_id)
            result = self.predictor.predict(self.request, self.observations, self.weather)
            self.result = replace(result, input_hash=_input_hash(self.request, self.observations,
                self.weather, self.model_identity), warnings=tuple(dict.fromkeys((*result.warnings,
                *self.extra_warnings))))
            self.stage = 3
            self.record("predict_power", "ok", f"{result.model_id}: {len(result.rows)} rows")
        return {"model_id": self.result.model_id, "rows": len(self.result.rows)}

    def inspect_forecast(self):
        self._ready(3)
        if self.stage < 4:
            result, q = self.result, self.request
            if (result.request_id != q.request_id or result.weather_bundle_id != self.weather.bundle_id
                    or result.model_id != self.predictor.model_id):
                raise ValueError("forecast identifiers do not match audited inputs/model")
            if result.status not in {"ok", "degraded"}:
                raise ValueError("predictor did not return a successful forecast")
            if type(result.is_synthetic) is not bool or result.is_synthetic != self.weather.is_synthetic:
                raise ValueError("forecast synthetic status does not match its inputs")
            if len(result.rows) != len(self.expected) or {(r.turbine_id, r.valid_time) for r in result.rows} != self.expected:
                raise ValueError("forecast must cover each requested turbine/hour exactly once")
            warnings = list(result.warnings)
            for row in result.rows:
                lead = (row.valid_time - q.issue_time).total_seconds() / 3600
                if row.issue_time != q.issue_time or row.lead_hours != lead:
                    raise ValueError("forecast issue_time/lead_hours mismatch")
                if isinstance(row.prediction,bool) or not isfinite(row.prediction):
                    raise ValueError("forecast contains a non-finite prediction")
                quantiles = [row.p10, row.p50, row.p90]
                if any(v is not None for v in quantiles):
                    if any(v is None or isinstance(v,bool) or not isfinite(v) for v in quantiles) or quantiles != sorted(quantiles):
                        raise ValueError("provide all finite ordered p10/p50/p90 values, or leave all empty")
            if any(not 0 <= r.prediction <= 1 for r in result.rows):
                warnings.append("Predictions outside observed normalized range [0,1]; values were not clipped.")
            self.result = replace(result, warnings=tuple(dict.fromkeys(warnings)))
            by_turbine = {}
            for turbine in q.turbine_ids:
                rows = sorted((r for r in result.rows if r.turbine_id == turbine), key=lambda r:r.valid_time)
                values = [r.prediction for r in rows]
                by_turbine[turbine] = {"min": min(values), "max": max(values),
                    "mean": sum(values)/len(values),
                    "largest_hourly_change": max((abs(b-a) for a,b in zip(values, values[1:])), default=0)}
            self.summary = {"forecast_rows": len(result.rows), "unit": "normalized_active_power",
                            "per_turbine": by_turbine, "warnings": list(self.result.warnings)}
            self.stage = 4
            self.record("inspect_forecast", "ok", "Coverage, numeric values and quantiles passed")
        return self.summary


class ForecastWorkflow:
    """Backward-compatible entry point for teammates and the original fixture."""
    def __init__(self, weather_provider: WeatherProvider, predictor: Predictor):
        self.weather_provider, self.predictor = weather_provider, predictor

    def run(self, request: ForecastRequest, observations: list[Observation]):
        completed = self.run_detailed(request, observations)
        return completed.result, completed.trace

    def run_detailed(self, request: ForecastRequest, observations: list[Observation]) -> WorkflowRun:
        session = WorkflowSession(request, observations, self.weather_provider, self.predictor)
        for step in (session.fetch_weather, session.audit_inputs, session.predict_power, session.inspect_forecast):
            step()
        return WorkflowRun(session.result, session.trace, session.weather)
