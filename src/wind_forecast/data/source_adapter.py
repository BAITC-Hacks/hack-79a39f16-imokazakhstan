"""Configurable SCADA CSV preparation with explicit time and availability semantics.

No mapping or time convention is bundled: callers must supply facts established
from the original file and its publication process before canonical export.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from wind_forecast.contracts import ForecastRequest, Observation, require_utc

TimeLabel = Literal["instant", "interval_start", "interval_end"]


@dataclass(frozen=True)
class SourceColumns:
    turbine_id: str
    timestamp: str
    power: str
    wind: str
    temperature: str | None = None
    available_at: str | None = None


@dataclass(frozen=True)
class SourceConfig:
    columns: SourceColumns
    turbine_ids: dict[str, str]
    timezone_name: str
    time_label: TimeLabel
    target_unit: str
    availability_basis: str
    interval_minutes: int | None = None
    publication_lag_minutes: int | None = None
    delimiter: str = ","
    decimal_separator: Literal[".", ","] = "."
    missing_tokens: tuple[str, ...] = ("",)
    encoding: str = "utf-8-sig"
    source_verified: bool = False
    verification_evidence: str = ""
    target_definition: str = ""

    def __post_init__(self) -> None:
        if not self.turbine_ids or any(
            not source or not target for source, target in self.turbine_ids.items()
        ):
            raise ValueError("explicit nonempty source-to-canonical turbine mapping required")
        if len(set(self.turbine_ids.values())) != len(self.turbine_ids):
            raise ValueError("source turbine IDs must map to distinct canonical IDs")
        if self.time_label not in ("instant", "interval_start", "interval_end"):
            raise ValueError("invalid time_label")
        if self.time_label != "instant" and (
            self.interval_minutes is None or self.interval_minutes <= 0
        ):
            raise ValueError("interval duration required for interval timestamps")
        if self.time_label == "instant" and self.interval_minutes is not None:
            raise ValueError("instant timestamps cannot have an interval duration")
        if (self.columns.available_at is None) == (self.publication_lag_minutes is None):
            raise ValueError("configure exactly one availability source: column or explicit lag")
        if self.publication_lag_minutes is not None and self.publication_lag_minutes < 0:
            raise ValueError("publication lag cannot be negative")
        if not self.availability_basis.strip():
            raise ValueError("documented availability_basis required")
        if not self.target_unit.strip():
            raise ValueError("explicit target_unit required; do not infer normalization")
        if self.source_verified and (
            not self.verification_evidence.strip() or not self.target_definition.strip()
        ):
            raise ValueError(
                "verified source requires evidence and target normalization definition"
            )
        if len(self.delimiter) != 1:
            raise ValueError("delimiter must be one character")
        if self.decimal_separator not in (".", ","):
            raise ValueError("decimal_separator must be '.' or ','")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown time zone {self.timezone_name!r}; install tzdata if needed"
            ) from exc


@dataclass(frozen=True)
class RowProblem:
    line_number: int
    reasons: tuple[str, ...]
    turbine_id: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True)
class PreparationReport:
    source_sha256: str
    source_rows: int
    retained_rows: int
    duplicate_identical: int
    quality_counts: dict[str, int]
    turbine_counts: dict[str, int]
    time_range_by_turbine: dict[str, tuple[str, str]]
    missing_intervals_by_turbine: dict[str, int]
    missing_examples_by_turbine: dict[str, tuple[str, ...]]
    problems: tuple[RowProblem, ...]
    target_unit: str
    availability_basis: str
    source_verified: bool


@dataclass(frozen=True)
class PreparedObservations:
    observations: tuple[Observation, ...]
    report: PreparationReport


def profile_source(
    path: str | Path, *, delimiter: str = ",", encoding: str = "utf-8-sig"
) -> dict[str, object]:
    """Return a safe structural profile without assuming source time or units."""
    source = Path(path)
    with source.open(newline="", encoding=encoding) as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        headers = tuple(reader.fieldnames or ())
        row_count = sum(1 for _ in reader)
    return {"sha256": _sha256_file(source), "headers": headers, "rows": row_count}


def load_source_config(path: str | Path) -> SourceConfig:
    """Read a reviewed, versionable source-specific mapping from JSON."""
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    if settings.get("version") != 1:
        raise ValueError("unsupported source configuration version")
    settings = dict(settings)
    settings.pop("version")
    settings["columns"] = SourceColumns(**settings["columns"])
    if "missing_tokens" in settings:
        settings["missing_tokens"] = tuple(settings["missing_tokens"])
    return SourceConfig(**settings)


def prepare_observations(path: str | Path, config: SourceConfig) -> PreparedObservations:
    """Map a confirmed source CSV into deterministic canonical records and an audit."""
    source = Path(path)
    selected: dict[tuple[str, datetime], tuple[Observation, int]] = {}
    conflicts: set[tuple[str, datetime]] = set()
    seen_times: dict[str, set[datetime]] = {
        turbine: set() for turbine in config.turbine_ids.values()
    }
    problems: list[RowProblem] = []
    identical = 0
    row_count = 0
    with source.open(newline="", encoding=config.encoding) as stream:
        reader = csv.DictReader(stream, delimiter=config.delimiter)
        required = {
            config.columns.turbine_id,
            config.columns.timestamp,
            config.columns.power,
            config.columns.wind,
        }
        required.update(x for x in (config.columns.temperature, config.columns.available_at) if x)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"source CSV missing configured columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            row_count += 1
            if None in row:
                problems.append(RowProblem(line_number, ("extra_columns",)))
                continue
            source_id = (row[config.columns.turbine_id] or "").strip()
            turbine_id = config.turbine_ids.get(source_id)
            if turbine_id is None:
                problems.append(RowProblem(line_number, ("unknown_turbine",), source_id))
                continue
            try:
                label_time = _parse_time(row[config.columns.timestamp], config.timezone_name)
                observed_at = label_time
                if config.time_label == "interval_start":
                    observed_at += timedelta(minutes=config.interval_minutes or 0)
                if config.columns.available_at:
                    available_at = _parse_time(
                        row[config.columns.available_at], config.timezone_name
                    )
                else:
                    available_at = observed_at + timedelta(
                        minutes=config.publication_lag_minutes or 0
                    )
                if available_at < observed_at:
                    raise ValueError("available_before_observed")
            except (TypeError, ValueError) as exc:
                problems.append(RowProblem(line_number, (str(exc),), turbine_id))
                continue
            seen_times[turbine_id].add(observed_at)
            reasons: list[str] = []
            power = _source_float(row[config.columns.power], config, "power", reasons)
            wind = _source_float(row[config.columns.wind], config, "wind", reasons)
            temperature = (
                _source_float(row[config.columns.temperature], config, "temperature", reasons)
                if config.columns.temperature
                else None
            )
            if wind is not None and wind < 0:
                wind = None
                reasons.append("invalid_wind")
            # Optional temperature is reported but does not make wind/power unusable.
            eligibility_reasons = sorted(
                reason for reason in reasons if not reason.endswith("temperature")
            )
            quality = "ok" if not eligibility_reasons else "|".join(eligibility_reasons)
            observation = Observation(
                turbine_id, observed_at, available_at, power, wind, temperature, quality
            )
            if reasons:
                problems.append(
                    RowProblem(line_number, tuple(sorted(reasons)), turbine_id, observed_at)
                )
            key = (turbine_id, observed_at)
            if key in conflicts:
                problems.append(
                    RowProblem(line_number, ("duplicate_conflict",), turbine_id, observed_at)
                )
                continue
            previous = selected.get(key)
            if previous is None:
                selected[key] = (observation, line_number)
            elif previous[0] == observation:
                identical += 1
                problems.append(
                    RowProblem(line_number, ("duplicate_identical",), turbine_id, observed_at)
                )
            else:
                conflicts.add(key)
                selected.pop(key)
                problems.append(
                    RowProblem(previous[1], ("duplicate_conflict",), turbine_id, observed_at)
                )
                problems.append(
                    RowProblem(line_number, ("duplicate_conflict",), turbine_id, observed_at)
                )
    observations = tuple(
        record
        for record, _ in sorted(
            selected.values(), key=lambda item: (item[0].turbine_id, item[0].observed_at)
        )
    )
    quality = Counter(row.quality_flag for row in observations)
    turbines = Counter(row.turbine_id for row in observations)
    retained_times: dict[str, set[datetime]] = {turbine: set() for turbine in seen_times}
    for row in observations:
        retained_times[row.turbine_id].add(row.observed_at)
    ranges: dict[str, tuple[str, str]] = {}
    missing_counts: dict[str, int] = {}
    missing_examples: dict[str, tuple[str, ...]] = {}
    for turbine_id, seen in sorted(seen_times.items()):
        if not seen:
            continue
        earliest, latest = min(seen), max(seen)
        ranges[turbine_id] = (earliest.isoformat(), latest.isoformat())
        if config.interval_minutes is not None:
            interval = timedelta(minutes=config.interval_minutes)
            missing = []
            cursor = earliest
            while cursor <= latest:
                if cursor not in retained_times[turbine_id]:
                    missing.append(cursor.isoformat())
                cursor += interval
            missing_counts[turbine_id] = len(missing)
            missing_examples[turbine_id] = tuple(missing[:10])
    return PreparedObservations(
        observations,
        PreparationReport(
            source_sha256=_sha256_file(source),
            source_rows=row_count,
            retained_rows=len(observations),
            duplicate_identical=identical,
            quality_counts=dict(sorted(quality.items())),
            turbine_counts=dict(sorted(turbines.items())),
            time_range_by_turbine=ranges,
            missing_intervals_by_turbine=missing_counts,
            missing_examples_by_turbine=missing_examples,
            problems=tuple(problems),
            target_unit=config.target_unit,
            availability_basis=config.availability_basis,
            source_verified=config.source_verified,
        ),
    )


def publish_prepared_artifact(
    prepared: PreparedObservations,
    config: SourceConfig,
    output_root: str | Path,
) -> Path:
    """Publish immutable canonical CSV/report files only for a verified mapping."""
    if not config.source_verified or not config.verification_evidence.strip():
        raise ValueError("unverified source cannot be published as a usable input artifact")
    if not prepared.observations or not any(
        row.quality_flag == "ok" for row in prepared.observations
    ):
        raise ValueError("no usable observations to publish")
    config_json = json.dumps(
        {"version": 1, **asdict(config)}, sort_keys=True, separators=(",", ":")
    )
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    identity = f"{prepared.report.source_sha256}:{config_hash}"
    artifact_id = f"scada-{hashlib.sha256(identity.encode('ascii')).hexdigest()[:24]}"
    root = Path(output_root)
    target = root / artifact_id
    if target.exists():
        raise FileExistsError(f"immutable artifact already exists: {target}")
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{artifact_id}.{os.getpid()}.tmp"
    temporary.mkdir()
    try:
        observations_path = temporary / "observations.csv"
        with observations_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "turbine_id",
                    "observed_at",
                    "available_at",
                    "power_norm",
                    "wind_ms",
                    "temp_c",
                    "quality_flag",
                )
            )
            for row in prepared.observations:
                writer.writerow(
                    (
                        row.turbine_id,
                        row.observed_at.isoformat(),
                        row.available_at.isoformat(),
                        "" if row.power_norm is None else row.power_norm,
                        "" if row.wind_ms is None else row.wind_ms,
                        "" if row.temp_c is None else row.temp_c,
                        row.quality_flag,
                    )
                )
        report = asdict(prepared.report)
        for problem in report["problems"]:
            if problem["observed_at"] is not None:
                problem["observed_at"] = problem["observed_at"].isoformat()
        (temporary / "quality_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        (temporary / "source_config.json").write_text(config_json, encoding="utf-8")
        checksums = {
            name: _sha256_file(temporary / name)
            for name in ("observations.csv", "quality_report.json", "source_config.json")
        }
        manifest = {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "status": "verified_source",
            "source_sha256": prepared.report.source_sha256,
            "configuration_sha256": config_hash,
            "target_unit": config.target_unit,
            "target_definition": config.target_definition,
            "source_timezone": config.timezone_name,
            "time_label": config.time_label,
            "interval_minutes": config.interval_minutes,
            "availability_basis": config.availability_basis,
            "verification_evidence": config.verification_evidence,
            "row_count": len(prepared.observations),
            "checksums": checksums,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(target)
    finally:
        if temporary.exists():
            for item in temporary.iterdir():
                item.unlink()
            temporary.rmdir()
    return target


def select_as_of(
    observations: tuple[Observation, ...] | list[Observation],
    request: ForecastRequest,
    *,
    frozen_jan31_end: datetime | None = None,
    freeze_inclusive: bool | None = None,
) -> tuple[Observation, ...]:
    """Apply availability and an explicitly configured January observation freeze."""
    cutoff = None
    if request.observation_policy == "frozen_jan31":
        if frozen_jan31_end is None or freeze_inclusive is None:
            raise ValueError("frozen_jan31 needs an explicit aware cutoff and inclusive policy")
        cutoff = require_utc(frozen_jan31_end, "frozen_jan31_end")
    elif request.observation_policy != "rolling_available":
        raise ValueError(f"unknown observation_policy {request.observation_policy!r}")
    ids = set(request.turbine_ids)
    return tuple(
        row
        for row in observations
        if (
            row.turbine_id in ids
            and row.observed_at <= request.issue_time
            and row.available_at <= request.issue_time
            and (
                cutoff is None
                or row.observed_at < cutoff
                or (freeze_inclusive and row.observed_at == cutoff)
            )
        )
    )


def _parse_time(value: str | None, zone_name: str) -> datetime:
    if value is None or not value.strip():
        raise ValueError("missing_timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("invalid_timestamp") from exc
    if parsed.tzinfo is not None:
        return require_utc(parsed, "source timestamp")
    zone = ZoneInfo(zone_name)
    a, b = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
    valid = [
        candidate
        for candidate in (a, b)
        if (candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == parsed)
    ]
    if not valid:
        raise ValueError("nonexistent_local_time")
    if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
        raise ValueError("ambiguous_local_time")
    return valid[0].astimezone(UTC)


def _source_float(
    value: str | None, config: SourceConfig, field_name: str, reasons: list[str]
) -> float | None:
    raw = (value or "").strip()
    if raw in config.missing_tokens:
        reasons.append(f"missing_{field_name}")
        return None
    if config.decimal_separator == ",":
        raw = raw.replace(",", ".")
    try:
        parsed = float(raw)
    except ValueError:
        reasons.append(f"invalid_{field_name}")
        return None
    if not math.isfinite(parsed):
        reasons.append(f"invalid_{field_name}")
        return None
    return parsed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
