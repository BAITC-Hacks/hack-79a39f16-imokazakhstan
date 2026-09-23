"""CSV adapter for the canonical observation schema. Person 1 maps real source columns here."""

from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path

from wind_forecast.contracts import Observation


def load_observations(path: str | Path) -> list[Observation]:
    """Load canonical CSV rows. Source-specific parsing belongs in a separate adapter."""
    observations: list[Observation] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"turbine_id", "observed_at", "available_at", "power_norm", "wind_ms"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"canonical CSV missing columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            try:
                if None in row:
                    raise ValueError("too many columns")
                observations.append(
                    Observation(
                        turbine_id=row["turbine_id"],
                        observed_at=datetime.fromisoformat(row["observed_at"]),
                        available_at=datetime.fromisoformat(row["available_at"]),
                        power_norm=_optional_float(row.get("power_norm")),
                        wind_ms=_optional_float(row.get("wind_ms")),
                        temp_c=_optional_float(row.get("temp_c")),
                        quality_flag=row.get("quality_flag") or "ok",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"canonical CSV line {line_number}: {exc}") from exc
    return observations


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("numeric values must be finite")
    return parsed
