"""Person 1's bounded NOAA GFS probe and local cache replay commands."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from wind_forecast.contracts import ForecastRequest
from wind_forecast.weather.archive_probe import probe_run
from wind_forecast.weather.handoff import publish_weather_handoff
from wind_forecast.weather.noaa_gfs import NoaaGfsProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe or fetch original NOAA GFS weather")
    parser.add_argument("command", choices=("probe", "fetch", "export"))
    parser.add_argument("--issue-time", required=True, help="UTC hourly ISO timestamp")
    parser.add_argument("--run-init", help="Required for probe; UTC GFS cycle")
    parser.add_argument("--horizon-hours", type=int, choices=(24, 48), default=48)
    parser.add_argument(
        "--locations", type=Path, default=Path(__file__).with_name("locations.json")
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/person1-noaa-gfs"))
    parser.add_argument("--output-root", type=Path, default=Path("data/processed"))
    parser.add_argument(
        "--network", action="store_true", help="Permit original NOAA S3 downloads in fetch"
    )
    args = parser.parse_args()
    location_data = json.loads(args.locations.read_text(encoding="utf-8"))
    coords = {
        name: (float(record["latitude"]), float(record["longitude"]))
        for name, record in location_data.items()
    }
    request = ForecastRequest(
        request_id="person1-weather-cli",
        issue_time=datetime.fromisoformat(args.issue_time),
        turbine_ids=tuple(sorted(coords)),
        horizon_hours=args.horizon_hours,
        mode="historical",
    )
    if args.command == "probe":
        if not args.run_init:
            parser.error("probe requires --run-init")
        run = datetime.fromisoformat(args.run_init)
        proof = probe_run(request, run)
        print(
            json.dumps(
                {
                    "provenance_status": proof.provenance_status,
                    "issue_time": proof.issue_time.isoformat(),
                    "run_init_time": proof.run_init_time.isoformat(),
                    "source_leads": [
                        proof.leads[0].source_lead_hours,
                        proof.leads[-1].source_lead_hours,
                    ],
                    "lead_count": len(proof.leads),
                    "latest_object_last_modified": proof.metadata_available_at.isoformat(),
                    "note": "Metadata only; not a verified weather bundle.",
                },
                indent=2,
            )
        )
    else:
        provider = NoaaGfsProvider(coords, args.cache_dir, allow_network=args.network)
        bundle = provider.fetch(request)
        manifest_path = args.cache_dir / "bundles" / f"{bundle.bundle_id}.verified.json"
        if args.command == "export":
            output = publish_weather_handoff(manifest_path, request, coords, args.output_root)
            print(
                json.dumps(
                    {"handoff_dir": str(output), "weather_bundle_id": bundle.bundle_id}, indent=2
                )
            )
            return
        print(
            json.dumps(
                {
                    "provenance_status": bundle.provenance_status,
                    "bundle_id": bundle.bundle_id,
                    "source_hash": bundle.source_hash,
                    "row_count": len(bundle.rows),
                    "run_init_time": bundle.run_init_time.isoformat(),
                    "available_at": bundle.available_at.isoformat(),
                    "manifest": str(manifest_path),
                    "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
