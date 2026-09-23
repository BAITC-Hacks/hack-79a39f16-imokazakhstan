"""Original NOAA GFS weather provider with strict historical provenance guards."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from wind_forecast.contracts import ForecastRequest, WeatherBundle
from wind_forecast.weather.archive_probe import probe_run
from wind_forecast.weather.gfs_extract import (
    attest_origin_s3_manifest,
    extract_probe,
    load_cached_bundle,
)


class NoaaGfsProvider:
    """Fetch a complete, eligible original run or replay a verified local cache.

    Coordinates are explicit because the SCADA IDs have not yet been confirmed.
    Offline/cache-only operation is the default and requires no ecCodes install.
    """

    def __init__(
        self,
        coordinates: dict[str, tuple[float, float]],
        cache_dir: str | Path,
        *,
        allow_network: bool = False,
        max_candidate_runs: int = 4,
    ) -> None:
        self.coordinates = dict(coordinates)
        self.cache_dir = Path(cache_dir)
        self.allow_network = allow_network
        if not 1 <= max_candidate_runs <= 8:
            raise ValueError("max_candidate_runs must be between 1 and 8")
        self.max_candidate_runs = max_candidate_runs

    def fetch(self, request: ForecastRequest) -> WeatherBundle:
        unknown = set(request.turbine_ids) - set(self.coordinates)
        if unknown:
            raise ValueError(f"unknown turbine coordinates: {', '.join(sorted(unknown))}")
        requested_coordinates = {
            turbine: self.coordinates[turbine] for turbine in request.turbine_ids
        }
        existing = self._matching_manifests(request, requested_coordinates)
        if existing:
            # A matching cached run must pass every integrity and provenance
            # check. Never hide a corrupt bundle by silently fetching another.
            return load_cached_bundle(existing[0], request, requested_coordinates)
        if not self.allow_network:
            raise FileNotFoundError(
                "no verified GFS cache bundle for this request; network is disabled"
            )
        cycle = request.issue_time.replace(hour=(request.issue_time.hour // 6) * 6)
        failures: list[str] = []
        for offset in range(self.max_candidate_runs):
            run = cycle - timedelta(hours=6 * offset)
            try:
                probe_run(request, run)
            except (ValueError, OSError) as exc:
                failures.append(f"{run.isoformat()}: {exc}")
                continue
            proof = extract_probe(request, run, requested_coordinates, self.cache_dir)
            verified = attest_origin_s3_manifest(proof, request, requested_coordinates)
            return load_cached_bundle(verified, request, requested_coordinates)
        raise ValueError("no complete eligible GFS cycle: " + "; ".join(failures))

    def _matching_manifests(
        self, request: ForecastRequest, coordinates: dict[str, tuple[float, float]]
    ) -> list[Path]:
        matches: list[tuple[str, Path]] = []
        expected_coordinates = {
            turbine: list(coordinates[turbine]) for turbine in sorted(coordinates)
        }
        for path in (self.cache_dir / "bundles").glob("*.verified.json"):
            manifest = json.loads(path.read_text(encoding="ascii"))
            if (
                manifest.get("issue_time") == request.issue_time.isoformat()
                and manifest.get("horizon_hours") == request.horizon_hours
                and manifest.get("turbine_ids") == sorted(request.turbine_ids)
                and manifest.get("coordinates") == expected_coordinates
            ):
                matches.append((manifest["run_init_time"], path))
        matches.sort(reverse=True)
        return [path for _, path in matches]
