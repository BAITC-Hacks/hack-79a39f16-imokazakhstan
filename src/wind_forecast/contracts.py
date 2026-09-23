"""Small shared records. Times crossing module boundaries must be timezone-aware UTC."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

RunMode = Literal["fixture", "historical", "live"]
Provenance = Literal["verified_original", "unverified", "synthetic"]


def require_utc(value: datetime, field_name: str) -> datetime:
    """Validate and normalize a timestamp used by the cross-module contract."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ForecastRequest:
    request_id: str
    issue_time: datetime
    turbine_ids: tuple[str, ...]
    horizon_hours: int = 48
    mode: RunMode = "fixture"
    observation_policy: str = "frozen_jan31"

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_time", require_utc(self.issue_time, "issue_time"))
        if any((self.issue_time.minute, self.issue_time.second, self.issue_time.microsecond)):
            raise ValueError("issue_time must align to a UTC hour")
        if self.horizon_hours not in (24, 48):
            raise ValueError("horizon_hours must be 24 or 48")
        if not self.turbine_ids:
            raise ValueError("at least one turbine_id is required")
        if len(set(self.turbine_ids)) != len(self.turbine_ids):
            raise ValueError("turbine_ids must be unique")


@dataclass(frozen=True)
class Observation:
    turbine_id: str
    observed_at: datetime
    available_at: datetime
    power_norm: float | None
    wind_ms: float | None
    temp_c: float | None = None
    quality_flag: str = "ok"

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "available_at", require_utc(self.available_at, "available_at"))


@dataclass(frozen=True)
class WeatherPoint:
    turbine_id: str
    valid_time: datetime
    wind_ms: float | None
    temp_c: float | None
    wind_direction_deg: float | None = None
    wind_height_m: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_time", require_utc(self.valid_time, "valid_time"))


@dataclass(frozen=True)
class WeatherBundle:
    bundle_id: str
    provider: str
    weather_model: str
    run_init_time: datetime
    available_at: datetime
    retrieved_at: datetime
    availability_basis: str
    source_uri: str
    source_hash: str
    provenance_status: Provenance
    is_synthetic: bool
    rows: tuple[WeatherPoint, ...]

    def __post_init__(self) -> None:
        for name in ("run_init_time", "available_at", "retrieved_at"):
            object.__setattr__(self, name, require_utc(getattr(self, name), name))


@dataclass(frozen=True)
class ForecastRow:
    turbine_id: str
    issue_time: datetime
    valid_time: datetime
    lead_hours: int
    prediction: float
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_time", require_utc(self.issue_time, "issue_time"))
        object.__setattr__(self, "valid_time", require_utc(self.valid_time, "valid_time"))


@dataclass(frozen=True)
class ForecastResult:
    forecast_id: str
    request_id: str
    schema_version: str
    model_id: str
    weather_bundle_id: str
    input_hash: str
    created_at: datetime
    status: Literal["ok", "degraded", "failed"]
    is_synthetic: bool
    warnings: tuple[str, ...]
    rows: tuple[ForecastRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", require_utc(self.created_at, "created_at"))


@dataclass
class WorkflowTrace:
    run_id: str
    events: list[dict[str, object]] = field(default_factory=list)
