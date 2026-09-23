#!/usr/bin/env python3
"""Run a fully synthetic 48-hour forecast and write inspectable local artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from wind_forecast.agent.artifacts import save_forecast_run
from wind_forecast.agent.runner import ForecastWorkflow
from wind_forecast.contracts import ForecastRequest
from wind_forecast.demo_data import sample_observations
from wind_forecast.models.power_curve import EmpiricalPowerCurve
from wind_forecast.weather.mock import MockWeatherProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="runs/demo")
    args = parser.parse_args()

    request = ForecastRequest(
        request_id="fixture-20260131T0000Z",
        issue_time=datetime(2026, 1, 31, tzinfo=timezone.utc),
        turbine_ids=("T1", "T2"),
        horizon_hours=48,
        mode="fixture",
    )
    observations = sample_observations()
    predictor = EmpiricalPowerCurve().fit(observations)
    workflow = ForecastWorkflow(MockWeatherProvider(), predictor)
    run = workflow.run_detailed(request, observations)
    artifacts = save_forecast_run(run.result, run.trace, run.weather, args.output)
    print(f"Wrote {len(run.result.rows)} synthetic rows to {artifacts.forecast_csv}")
    print("Synthetic fixture output. Do not use it as a scored forecast.")


if __name__ == "__main__":
    main()
