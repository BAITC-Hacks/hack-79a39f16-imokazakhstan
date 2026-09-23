"""Run the offline fixture with only observations eligible at its issue time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wind_forecast.agent.artifacts import RunArtifacts, save_forecast_run
from wind_forecast.agent.runner import ForecastWorkflow, WorkflowRun
from wind_forecast.contracts import ForecastRequest
from wind_forecast.demo_data import sample_observations
from wind_forecast.models.power_curve import EmpiricalPowerCurve
from wind_forecast.weather.mock import MockWeatherProvider


@dataclass(frozen=True)
class FixtureRun:
    request: ForecastRequest
    workflow: WorkflowRun
    artifacts: RunArtifacts
    observation_count: int
    latest_observation_at: datetime


def run_fixture(request: ForecastRequest, output_root: Path | str) -> FixtureRun:
    """Fit and run the synthetic baseline, then save an independent run version."""
    if request.mode != "fixture":
        raise ValueError("run_fixture accepts fixture requests only")

    observations = [
        row
        for row in sample_observations()
        if row.turbine_id in request.turbine_ids
        and row.observed_at <= request.issue_time
        and row.available_at <= request.issue_time
    ]
    if not observations:
        raise ValueError("no synthetic observations are available by the selected issue time")

    predictor = EmpiricalPowerCurve().fit(observations)
    workflow = ForecastWorkflow(MockWeatherProvider(), predictor)
    completed = workflow.run_detailed(request, observations)
    artifacts = save_forecast_run(completed.result, completed.trace, completed.weather, output_root)
    return FixtureRun(
        request=request,
        workflow=completed,
        artifacts=artifacts,
        observation_count=len(observations),
        latest_observation_at=max(row.available_at for row in observations),
    )
