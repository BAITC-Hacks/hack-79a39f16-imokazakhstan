"""A bounded, inspectable workflow runner. Person 3 can add the OpenAI tool planner here."""

from __future__ import annotations

from datetime import datetime, timezone

from wind_forecast.contracts import ForecastRequest, ForecastResult, Observation, WorkflowTrace
from wind_forecast.models.base import Predictor
from wind_forecast.weather.base import WeatherProvider


class ForecastWorkflow:
    """Run fetch -> audit -> predict -> inspect; each step leaves a trace event."""

    def __init__(self, weather_provider: WeatherProvider, predictor: Predictor) -> None:
        self.weather_provider = weather_provider
        self.predictor = predictor

    def run(
        self, request: ForecastRequest, observations: list[Observation]
    ) -> tuple[ForecastResult, WorkflowTrace]:
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

        record("fetch_weather", "started", "Request a bundle for this issue time")
        weather = self.weather_provider.fetch(request)
        record("fetch_weather", "ok", f"{weather.bundle_id}: {len(weather.rows)} rows")

        record("audit_inputs", "started", "Check provenance, issue time and coverage")
        if weather.available_at > request.issue_time:
            record("audit_inputs", "failed", "Weather was not yet available")
            raise ValueError("weather bundle was unavailable at issue_time")
        if request.mode == "historical" and (
            weather.is_synthetic or weather.provenance_status != "verified_original"
        ):
            record("audit_inputs", "failed", "Historical mode requires verified original weather")
            raise ValueError("historical mode requires verified, original forecast weather")
        for obs in observations:
            if obs.observed_at > request.issue_time:
                record("audit_inputs", "failed", "Observation was recorded after issue_time")
                raise ValueError("an observation timestamp was after issue_time")
            if obs.available_at > request.issue_time:
                record("audit_inputs", "failed", "Observation was not yet available")
                raise ValueError("an observation was unavailable at issue_time")
        record("audit_inputs", "ok", "Input timestamps passed basic eligibility checks")

        record("predict_power", "started", "Run the numerical predictor")
        result = self.predictor.predict(request, observations, weather)
        expected = request.horizon_hours * len(request.turbine_ids)
        if len(result.rows) != expected:
            record("inspect_forecast", "failed", f"Expected {expected} rows, got {len(result.rows)}")
            raise ValueError("forecast output is incomplete")
        record("predict_power", "ok", f"{result.model_id}: {len(result.rows)} rows")
        record("inspect_forecast", "ok", "Forecast row count is complete")
        record("prepare_output", "ok", "Forecast artifact is ready for persistence")
        return result, trace
