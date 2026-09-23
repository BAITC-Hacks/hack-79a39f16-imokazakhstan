"""A bounded, inspectable workflow runner for fixture and future historical inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite

from wind_forecast.contracts import (
    ForecastRequest,
    ForecastResult,
    Observation,
    WeatherBundle,
    WorkflowTrace,
)
from wind_forecast.models.base import Predictor
from wind_forecast.weather.base import WeatherProvider


@dataclass(frozen=True)
class WorkflowRun:
    result: ForecastResult
    trace: WorkflowTrace
    weather: WeatherBundle


def _input_hash(
    request: ForecastRequest, observations: list[Observation], weather: WeatherBundle
) -> str:
    """Hash the eligible inputs without run IDs or retrieval timestamps."""
    request_data = asdict(request)
    request_data.pop("request_id")
    weather_data = {
        "provider": weather.provider,
        "weather_model": weather.weather_model,
        "run_init_time": weather.run_init_time,
        "available_at": weather.available_at,
        "source_hash": weather.source_hash,
        "provenance_status": weather.provenance_status,
        "rows": [
            asdict(row)
            for row in sorted(weather.rows, key=lambda row: (row.turbine_id, row.valid_time))
        ],
    }
    payload = {
        "request": request_data,
        "observations": [
            asdict(row)
            for row in sorted(observations, key=lambda row: (row.turbine_id, row.observed_at))
        ],
        "weather": weather_data,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat()
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ForecastWorkflow:
    """Run fetch -> audit -> predict -> inspect; each step leaves a trace event."""

    def __init__(self, weather_provider: WeatherProvider, predictor: Predictor) -> None:
        self.weather_provider = weather_provider
        self.predictor = predictor

    def run(
        self, request: ForecastRequest, observations: list[Observation]
    ) -> tuple[ForecastResult, WorkflowTrace]:
        """Keep the original two-value interface for existing callers."""
        completed = self.run_detailed(request, observations)
        return completed.result, completed.trace

    def run_detailed(
        self, request: ForecastRequest, observations: list[Observation]
    ) -> WorkflowRun:
        """Return the audited weather bundle alongside the forecast and trace."""
        trace = WorkflowTrace(run_id=request.request_id)

        def record(step: str, status: str, detail: str) -> None:
            trace.events.append(
                {
                    "run_id": request.request_id,
                    "step": step,
                    "event_time": datetime.now(timezone.utc).isoformat(),
                    "simulated_issue_time": request.issue_time.isoformat(),
                    "status": status,
                    "detail": detail,
                }
            )

        def fail(step: str, detail: str) -> None:
            record(step, "failed", detail)
            raise ValueError(detail)

        record("fetch_weather", "started", "Request a bundle for this issue time")
        weather = self.weather_provider.fetch(request)
        record(
            "fetch_weather",
            "ok",
            f"{weather.bundle_id}: {len(weather.rows)} rows from {weather.provider}",
        )

        record("audit_inputs", "started", "Check provenance, issue time and coverage")
        if weather.run_init_time > weather.available_at:
            fail("audit_inputs", "weather run initialization is after its availability time")
        if weather.available_at > request.issue_time:
            fail("audit_inputs", "weather bundle was unavailable at issue_time")
        if request.mode == "historical" and (
            weather.is_synthetic or weather.provenance_status != "verified_original"
        ):
            fail("audit_inputs", "historical mode requires verified, original forecast weather")
        expected_times = {
            (turbine_id, request.issue_time + timedelta(hours=lead))
            for turbine_id in request.turbine_ids
            for lead in range(1, request.horizon_hours + 1)
        }
        weather_times = {(row.turbine_id, row.valid_time) for row in weather.rows}
        if len(weather.rows) != len(expected_times) or weather_times != expected_times:
            fail("audit_inputs", "weather must cover each requested turbine and hour exactly once")
        for obs in observations:
            if obs.observed_at > request.issue_time:
                fail("audit_inputs", "an observation timestamp was after issue_time")
            if obs.available_at > request.issue_time:
                fail("audit_inputs", "an observation was unavailable at issue_time")
        record("audit_inputs", "ok", f"{len(observations)} observations passed eligibility checks")

        record("predict_power", "started", "Run the numerical predictor")
        result = self.predictor.predict(request, observations, weather)
        if result.input_hash == "demo-hash-placeholder":
            result = replace(result, input_hash=_input_hash(request, observations, weather))
        record("predict_power", "ok", f"{result.model_id}: {len(result.rows)} rows")
        if result.request_id != request.request_id or result.weather_bundle_id != weather.bundle_id:
            fail("inspect_forecast", "forecast identifiers do not match the audited inputs")
        if result.is_synthetic != weather.is_synthetic:
            fail("inspect_forecast", "forecast synthetic status does not match weather provenance")
        forecast_times = {(row.turbine_id, row.valid_time) for row in result.rows}
        if len(result.rows) != len(expected_times) or forecast_times != expected_times:
            fail(
                "inspect_forecast",
                "forecast must cover each requested turbine and hour exactly once",
            )
        for row in result.rows:
            lead = int((row.valid_time - request.issue_time).total_seconds() / 3600)
            if row.issue_time != request.issue_time or row.lead_hours != lead:
                fail(
                    "inspect_forecast",
                    "forecast issue time or lead does not match its valid time",
                )
            if not isfinite(row.prediction):
                fail("inspect_forecast", "forecast contains a non-finite prediction")
        record("inspect_forecast", "ok", "Forecast coverage and values are complete")
        record("prepare_output", "ok", "Forecast artifact is ready for persistence")
        return WorkflowRun(result=result, trace=trace, weather=weather)
