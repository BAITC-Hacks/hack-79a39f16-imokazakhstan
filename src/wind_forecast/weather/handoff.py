"""Small immutable weather-only handoff for model and integration owners."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

from wind_forecast.contracts import ForecastRequest
from wind_forecast.weather.gfs_extract import load_cached_bundle


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish_weather_handoff(
    manifest_path: str | Path,
    request: ForecastRequest,
    coordinates: dict[str, tuple[float, float]],
    output_root: str | Path,
) -> Path:
    """Export verified canonical rows and their audit manifests without raw GRIBs."""
    manifest_path = Path(manifest_path)
    bundle = load_cached_bundle(manifest_path, request, coordinates, verify_messages=True)
    if bundle.provenance_status != "verified_original":
        raise ValueError("only verified original weather can be handed off")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"weather-{bundle.bundle_id}"
    if target.exists():
        raise FileExistsError(f"immutable weather handoff already exists: {target}")
    temporary = root / f".{target.name}.{os.getpid()}.tmp"
    temporary.mkdir()
    try:
        forecast_path = temporary / "weather.csv"
        with forecast_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "turbine_id",
                    "valid_time",
                    "wind_ms",
                    "temp_c",
                    "wind_direction_deg",
                    "wind_height_m",
                )
            )
            for row in sorted(bundle.rows, key=lambda item: (item.turbine_id, item.valid_time)):
                writer.writerow(
                    (
                        row.turbine_id,
                        row.valid_time.isoformat(),
                        row.wind_ms,
                        row.temp_c,
                        "" if row.wind_direction_deg is None else row.wind_direction_deg,
                        row.wind_height_m,
                    )
                )
        (temporary / "verified_archive_manifest.json").write_bytes(manifest_path.read_bytes())
        proof_path = manifest_path.with_name(f"{bundle.bundle_id}.json")
        (temporary / "origin_proof.json").write_bytes(proof_path.read_bytes())
        checksums = {item.name: _sha256(item) for item in temporary.iterdir() if item.is_file()}
        metadata = {
            "schema_version": 1,
            "artifact_id": target.name,
            "status": "verified_original_weather_only",
            "is_synthetic": False,
            "issue_time": request.issue_time.isoformat(),
            "horizon_hours": request.horizon_hours,
            "run_init_time": bundle.run_init_time.isoformat(),
            "available_at": bundle.available_at.isoformat(),
            "weather_bundle_id": bundle.bundle_id,
            "source_hash": bundle.source_hash,
            "row_count": len(bundle.rows),
            "turbine_ids": list(request.turbine_ids),
            "availability_basis": bundle.availability_basis,
            "checksums": checksums,
            "note": "Original hashed GRIB message cache is separate; this is the small canonical handoff.",
        }
        (temporary / "handoff_manifest.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(target)
    finally:
        if temporary.exists():
            for item in temporary.iterdir():
                item.unlink()
            temporary.rmdir()
    return target
