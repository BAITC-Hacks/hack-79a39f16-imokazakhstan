"""CSV adapter for the canonical observation schema. Person 1 maps real source columns here."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from wind_forecast.contracts import Observation


def load_observations(path: str | Path) -> list[Observation]:
    """Load canonical CSV rows. Source-specific parsing belongs in a separate adapter."""
    observations: list[Observation] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            observations.append(
                Observation(
                    turbine_id=row["turbine_id"],
                    observed_at=datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")),
                    available_at=datetime.fromisoformat(row["available_at"].replace("Z", "+00:00")),
                    power_norm=_optional_float(row.get("power_norm")),
                    wind_ms=_optional_float(row.get("wind_ms")),
                    temp_c=_optional_float(row.get("temp_c")),
                    quality_flag=row.get("quality_flag", "ok"),
                )
            )
    return observations


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)
