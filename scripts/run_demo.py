#!/usr/bin/env python3
"""Run a fully synthetic 48-hour forecast and write inspectable local artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

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
    result, trace = workflow.run(request, observations)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    forecast_path = output_dir / "forecast.csv"
    with forecast_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(result.rows[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in result.rows)
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "forecast_id": result.forecast_id,
                "request_id": result.request_id,
                "model_id": result.model_id,
                "weather_bundle_id": result.weather_bundle_id,
                "status": result.status,
                "is_synthetic": result.is_synthetic,
                "warnings": result.warnings,
                "row_count": len(result.rows),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in trace.events), encoding="utf-8"
    )
    print(f"Wrote {len(result.rows)} synthetic rows to {forecast_path}")
    print("Synthetic fixture output. Do not use it as a scored forecast.")


if __name__ == "__main__":
    main()
