"""Persist an inspectable forecast run without replacing earlier runs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from wind_forecast.contracts import ForecastResult, WeatherBundle, WorkflowTrace


CSV_FIELDS = (
    "turbine_id",
    "issue_time",
    "valid_time",
    "lead_hours",
    "prediction",
    "p10",
    "p50",
    "p90",
)


@dataclass(frozen=True)
class RunArtifacts:
    run_dir: Path
    forecast_csv: Path
    manifest_json: Path
    trace_jsonl: Path


def _safe_source_uri(uri: str) -> str:
    """Remove URL credentials and query parameters before persisting provenance."""
    parsed = urlsplit(uri)
    if not parsed.scheme:
        return "[local source]"
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def save_forecast_run(
    result: ForecastResult,
    trace: WorkflowTrace,
    weather: WeatherBundle,
    output_root: Path | str,
) -> RunArtifacts:
    """Write CSV, manifest, and trace into a new versioned directory."""
    if result.weather_bundle_id != weather.bundle_id:
        raise ValueError("forecast and weather bundle IDs do not match")

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    while True:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = root / f"v1-{timestamp}-{uuid4().hex}"
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        break

    artifacts = RunArtifacts(
        run_dir=run_dir,
        forecast_csv=run_dir / "forecast.csv",
        manifest_json=run_dir / "manifest.json",
        trace_jsonl=run_dir / "trace.jsonl",
    )

    with artifacts.forecast_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(
                {
                    "turbine_id": row.turbine_id,
                    "issue_time": row.issue_time.isoformat(),
                    "valid_time": row.valid_time.isoformat(),
                    "lead_hours": row.lead_hours,
                    "prediction": row.prediction,
                    "p10": row.p10,
                    "p50": row.p50,
                    "p90": row.p90,
                }
            )

    manifest = {
        "artifact_version": 1,
        "forecast_id": result.forecast_id,
        "request_id": result.request_id,
        "schema_version": result.schema_version,
        "model_id": result.model_id,
        "weather_bundle_id": result.weather_bundle_id,
        "input_hash": result.input_hash,
        "created_at": result.created_at.isoformat(),
        "status": result.status,
        "is_synthetic": result.is_synthetic or weather.is_synthetic,
        "warnings": list(result.warnings),
        "row_count": len(result.rows),
        "trace_run_id": trace.run_id,
        "weather": {
            "bundle_id": weather.bundle_id,
            "provider": weather.provider,
            "weather_model": weather.weather_model,
            "run_init_time": weather.run_init_time.isoformat(),
            "available_at": weather.available_at.isoformat(),
            "retrieved_at": weather.retrieved_at.isoformat(),
            "availability_basis": weather.availability_basis,
            "source_uri": _safe_source_uri(weather.source_uri),
            "source_hash": weather.source_hash,
            "provenance_status": weather.provenance_status,
            "is_synthetic": weather.is_synthetic,
            "row_count": len(weather.rows),
        },
    }
    artifacts.manifest_json.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with artifacts.trace_jsonl.open("w", encoding="utf-8") as stream:
        for event in trace.events:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    return artifacts
