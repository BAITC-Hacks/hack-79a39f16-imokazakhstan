"""Synthetic fixtures for wiring the starter together; never use for scored results."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from wind_forecast.contracts import Observation


def sample_observations() -> list[Observation]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    output = []
    for turbine_index, turbine_id in enumerate(("T1", "T2")):
        for hour in range(24 * 30):
            observed_at = start + timedelta(hours=hour)
            wind = 5.2 + 3.4 * abs(math.sin(hour * math.tau / 31 + turbine_index))
            power = max(0.0, min(1.0, ((wind - 2.5) / 9.0) ** 1.7))
            power += 0.015 * math.sin(hour * 1.7 + turbine_index)
            output.append(
                Observation(
                    turbine_id=turbine_id,
                    observed_at=observed_at,
                    available_at=observed_at,
                    power_norm=round(max(0.0, min(1.0, power)), 4),
                    wind_ms=round(wind, 3),
                    temp_c=-3.0 + 5.0 * math.sin(hour * math.tau / 24),
                    quality_flag="ok",
                )
            )
    return output
