"""Deterministic synthetic weather for the offline demo only."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from wind_forecast.contracts import ForecastRequest, WeatherBundle, WeatherPoint


class MockWeatherProvider:
    def fetch(self, request: ForecastRequest) -> WeatherBundle:
        run_init = request.issue_time - timedelta(hours=6)
        available = request.issue_time - timedelta(hours=1)
        points = []
        for turbine_index, turbine_id in enumerate(request.turbine_ids):
            for lead in range(1, request.horizon_hours + 1):
                valid = request.issue_time + timedelta(hours=lead)
                phase = (valid.hour / 24) * math.tau
                wind = 6.8 + 2.2 * math.sin(phase + turbine_index * 0.45) + 0.6 * math.sin(lead / 7)
                temp = -4.0 + 3.5 * math.sin(phase - 1.1)
                points.append(WeatherPoint(turbine_id, valid, round(max(0.0, wind), 3), round(temp, 2)))
        return WeatherBundle(
            bundle_id=f"synthetic-{request.request_id}",
            provider="local-mock",
            weather_model="synthetic-daily-cycle-v1",
            run_init_time=run_init,
            available_at=available,
            retrieved_at=datetime.now(timezone.utc),
            availability_basis="fixture only; not a real archived forecast",
            source_uri="fixture://generated-weather",
            source_hash="synthetic",
            provenance_status="synthetic",
            is_synthetic=True,
            rows=tuple(points),
        )
