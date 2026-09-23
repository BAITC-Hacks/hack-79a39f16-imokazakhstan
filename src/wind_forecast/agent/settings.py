"""Explicit application inputs; runtime secrets are kept outside run configuration."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from wind_forecast.contracts import ForecastRequest, require_utc

CASE_COORDINATES = {"T1": (43.645150, 78.535604), "T2": (43.643198, 78.538828)}


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return require_utc(value, "timestamp")


@dataclass(frozen=True)
class RunConfig:
    issue_time: datetime
    turbine_ids: tuple[str, ...] = ("T1", "T2")
    horizon_hours: int = 48
    mode: str = "fixture"
    data_paths: dict[str, str] = field(default_factory=dict)
    input_format: str = "organizer"
    source_timezone: str = "UTC"
    interval_label: str = "start"
    reporting_delay_minutes: int = 0
    assumptions_confirmed: bool = False
    observation_policy: str = "frozen_jan31"
    weather_source: str = "mock"
    weather_path: str = ""
    weather_factory: str = ""
    wind_height_m: int = 100
    coordinates: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(CASE_COORDINATES)
    )
    model_factory: str = ""
    model_path: str = ""
    model_metadata_path: str = ""
    model_kind: str = "auto"
    wind_model_path: str = ""
    wind_metadata_path: str = ""
    controller: str = "deterministic"
    jev_enabled: bool = False
    jev_model: str = "jev-1.13.0"
    operational_notes: tuple[dict, ...] = ()
    operational_notes_path: str = ""
    output_root: str = "runs/application"
    cache_dir: str = "data/cache/weather"

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_time", parse_time(self.issue_time))
        if isinstance(self.turbine_ids, str):
            raise ValueError("turbine_ids must be a list or tuple of turbine IDs")
        object.__setattr__(self, "turbine_ids", tuple(self.turbine_ids))
        self.request()
        if self.mode not in {"fixture", "historical", "live"}:
            raise ValueError("mode must be fixture, historical, or live")
        if self.input_format not in {"organizer", "canonical"}:
            raise ValueError("input_format must be organizer or canonical")
        ZoneInfo(self.source_timezone)
        if self.interval_label not in {"start", "end"}:
            raise ValueError("interval_label must be start or end")
        if not isinstance(self.assumptions_confirmed, bool):
            raise ValueError("assumptions_confirmed must be a boolean")
        if type(self.reporting_delay_minutes) is not int or self.reporting_delay_minutes < 0:
            raise ValueError("reporting_delay_minutes must be a nonnegative integer")
        if self.observation_policy not in {"frozen_jan31", "available"}:
            raise ValueError("observation_policy must be frozen_jan31 or available")
        if self.weather_source not in {"mock", "bundles", "noaa_gfs", "python", "none"}:
            raise ValueError("weather_source must be mock, bundles, noaa_gfs, or python")
        if self.mode != "fixture" and self.weather_source == "mock":
            raise ValueError("mock weather is only allowed in fixture mode")
        if self.mode == "fixture" and self.weather_source != "mock":
            raise ValueError("fixture mode uses mock weather; use historical/live for real inputs")
        if self.weather_source == "none" and self.model_kind != "local_history":
            raise ValueError("No-weather mode requires local_history models")
        if self.model_kind == "local_history" and (self.mode == "fixture" or self.weather_source != "none"):
            raise ValueError("local_history requires real observations and weather_source=none")
        if self.controller not in {"deterministic", "openai"}:
            raise ValueError("controller must be deterministic or openai")
        if not isinstance(self.jev_enabled, bool):
            raise ValueError("jev_enabled must be a boolean")
        if not isinstance(self.jev_model, str) or not self.jev_model.startswith("jev-"):
            raise ValueError("jev_model must name a TypeSafe Jev model")
        if not isinstance(self.operational_notes, (list, tuple)):
            raise ValueError("operational_notes must be a list or tuple")
        object.__setattr__(self, "operational_notes", tuple(self.operational_notes))
        if self.operational_notes and self.operational_notes_path:
            raise ValueError("provide inline operational_notes or operational_notes_path, not both")
        if self.model_kind not in {"auto", "gradient_boosting", "empirical", "local_history"}:
            raise ValueError("model_kind must be auto, gradient_boosting, or empirical")
        if self.wind_height_m not in {10, 100}:
            raise ValueError("wind_height_m must be 10 or 100 for the GFS provider")
        if not self.output_root or not self.cache_dir:
            raise ValueError("output_root and cache_dir must be nonempty paths")
        for turbine_id in self.turbine_ids:
            if turbine_id not in self.coordinates:
                raise ValueError(f"coordinates are missing for {turbine_id}")
            lat, lon = self.coordinates[turbine_id]
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"invalid coordinates for {turbine_id}")

    def request(self) -> ForecastRequest:
        return ForecastRequest(
            request_id=f"{self.mode}-{self.issue_time:%Y%m%dT%H%MZ}-{uuid4().hex[:10]}",
            issue_time=self.issue_time,
            turbine_ids=self.turbine_ids,
            horizon_hours=self.horizon_hours,
            mode=self.mode,
            observation_policy=self.observation_policy,
        )

    @property
    def training_limit(self) -> datetime:
        if self.observation_policy == "available":
            return self.issue_time
        january_end = datetime(2026, 2, 1, tzinfo=ZoneInfo(self.source_timezone))
        return min(self.issue_time, january_end.astimezone(timezone.utc))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["issue_time"] = self.issue_time.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "RunConfig":
        return cls(**data)


def runtime_settings() -> dict[str, str]:
    """Load host settings; callers decide which optional services are enabled."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path.cwd() / ".env", override=False)
    except ImportError:
        pass
    return {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_MODEL", "") or "gpt-4.1-mini",
        "typesafe_api_key": os.getenv("TYPESAFE_API_KEY", ""),
        "typesafe_model": os.getenv("TYPESAFE_MODEL", "") or "jev-1.13.0",
        "model_factory": os.getenv("WIND_MODEL_FACTORY", ""),
        "model_path": os.getenv("WIND_MODEL_PATH", ""),
        "model_metadata_path": os.getenv("WIND_MODEL_METADATA", ""),
    }
