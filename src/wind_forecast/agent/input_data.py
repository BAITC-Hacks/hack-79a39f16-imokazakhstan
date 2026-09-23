"""Adapt the supplied SCADA exports into complete, hourly UTC observations.

The export does not declare its timezone or interval convention. Both belong to
the caller's recorded configuration, never to an inferred station location.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from wind_forecast.contracts import Observation

SOURCE_COLUMNS = (
    "ID",
    "Статистическое время",
    "Средняя скорость ветра(m/s)",
    "Нормализованная активная мощность",
    "Средняя температура окружающей среды(°C)",
)
_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{1,2}):(\d{2}):(\d{2})$")
_STEP = timedelta(minutes=10)
_UTC = timezone.utc


@dataclass(frozen=True)
class ObservationDataset:
    observations: tuple[Observation, ...]
    report: dict[str, object]


def load_scada(
    source: Path | str | bytes,
    turbine_id: str,
    *,
    source_timezone: str,
    interval_label: str = "start",
    reporting_delay_minutes: int = 0,
) -> ObservationDataset:
    """Read raw ten-minute SCADA and keep only complete UTC hourly means.

    ``source`` is a filesystem path or CSV bytes (including uploaded bytes).
    ``interval_label`` declares whether each timestamp begins or ends its
    ten-minute interval. ``available_at`` is the resulting hour's end plus the
    caller's nonnegative reporting delay. Invalid or duplicate records raise
    ``ValueError``; incomplete hours are omitted and counted in the report.
    """
    if not isinstance(turbine_id, str) or not turbine_id.strip():
        raise ValueError("turbine_id must be a nonempty string")
    if interval_label not in {"start", "end"}:
        raise ValueError("interval_label must be 'start' or 'end'")
    if (
        not isinstance(reporting_delay_minutes, int)
        or isinstance(reporting_delay_minutes, bool)
        or reporting_delay_minutes < 0
    ):
        raise ValueError("reporting_delay_minutes must be a nonnegative integer")
    if not isinstance(source_timezone, str) or not source_timezone.strip():
        raise ValueError("source_timezone must be an explicit IANA timezone")
    try:
        source_zone = ZoneInfo(source_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown source_timezone: {source_timezone}") from exc

    if isinstance(source, bytes):
        raw = source
        source_name = "uploaded CSV"
    else:
        source_path = Path(source)
        raw = source_path.read_bytes()
        source_name = source_path.name
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("SCADA CSV must use UTF-8 encoding") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError("SCADA CSV is empty or missing its header")
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("SCADA CSV contains duplicate column headers")
    missing = set(SOURCE_COLUMNS) - set(reader.fieldnames)
    if missing:
        raise ValueError("SCADA CSV is missing columns: " + ", ".join(sorted(missing)))

    hours: dict[datetime, dict[datetime, tuple[float, float, float]]] = defaultdict(dict)
    timestamps: set[datetime] = set()
    raw_timestamps: list[datetime] = []
    out_of_range_power = 0
    for row in reader:
        line = reader.line_num
        if None in row or any(row.get(column) is None for column in SOURCE_COLUMNS):
            raise ValueError(f"CSV row {line} has an inconsistent number of fields")
        raw_time = _parse_timestamp(row[SOURCE_COLUMNS[1]], line)
        stamp = _localize(raw_time, source_zone, line)
        if stamp.minute % 10 or stamp.second or stamp.microsecond:
            raise ValueError(f"CSV row {line} is not aligned to a UTC ten-minute boundary")
        if stamp in timestamps:
            raise ValueError(f"duplicate timestamp at CSV row {line}: {raw_time.isoformat()}")
        timestamps.add(stamp)
        raw_timestamps.append(raw_time)
        wind, power, temperature = (
            _finite_number(row[column], column, line) for column in SOURCE_COLUMNS[2:]
        )
        if wind < 0:
            raise ValueError(f"negative wind speed at CSV row {line}")
        if not 0 <= power <= 1:
            out_of_range_power += 1
        sample_end = stamp + _STEP if interval_label == "start" else stamp
        hour_end = sample_end.replace(minute=0, second=0, microsecond=0)
        if sample_end != hour_end:
            hour_end += timedelta(hours=1)
        hours[hour_end][sample_end] = (wind, power, temperature)
    if not timestamps:
        raise ValueError("SCADA CSV has no data rows")

    observations: list[Observation] = []
    incomplete_hours = 0
    incomplete_samples = 0
    incomplete_examples: list[dict[str, object]] = []
    for hour_end, samples in sorted(hours.items()):
        expected = {hour_end - _STEP * i for i in range(6)}
        if set(samples) != expected:
            incomplete_hours += 1
            incomplete_samples += len(samples)
            if len(incomplete_examples) < 10:
                incomplete_examples.append(
                    {"hour_end": hour_end.isoformat(), "sample_count": len(samples)}
                )
            continue
        values = list(samples.values())
        observations.append(
            Observation(
                turbine_id=turbine_id,
                observed_at=hour_end,
                available_at=hour_end + timedelta(minutes=reporting_delay_minutes),
                wind_ms=math.fsum(value[0] / 6 for value in values),
                power_norm=math.fsum(value[1] / 6 for value in values),
                temp_c=math.fsum(value[2] / 6 for value in values),
            )
        )

    first_stamp, last_stamp = min(timestamps), max(timestamps)
    expected_samples = (last_stamp - first_stamp) // _STEP + 1
    missing_slots = expected_samples - len(timestamps)
    warnings = [
        "Source timestamps do not declare a timezone or interval label; "
        "the selected conventions are assumptions until the organizers confirm them.",
        "Normalized active power is preserved without conversion to MW or MWh.",
    ]
    if missing_slots:
        warnings.append(f"{missing_slots} ten-minute samples are missing inside source coverage.")
    if incomplete_hours:
        warnings.append(
            f"Dropped {incomplete_hours} incomplete hours containing {incomplete_samples} samples."
        )
    if out_of_range_power:
        warnings.append(
            f"{out_of_range_power} normalized power values are outside [0, 1]; "
            "values are preserved and require source review."
        )
    if not observations:
        warnings.append("No complete six-sample hours are available for forecasting.")
    report: dict[str, object] = {
        "turbine_id": turbine_id,
        "source_name": source_name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_timezone": source_timezone,
        "interval_label": interval_label,
        "reporting_delay_minutes": reporting_delay_minutes,
        "output_interval_label": "end",
        "output_timezone": "UTC",
        "input_rows": len(timestamps),
        "hourly_rows": len(observations),
        "dropped_incomplete_hours": incomplete_hours,
        "dropped_incomplete_samples": incomplete_samples,
        "incomplete_hour_examples": incomplete_examples,
        "missing_10min_slots": missing_slots,
        "raw_start": min(raw_timestamps).isoformat(),
        "raw_end": max(raw_timestamps).isoformat(),
        "utc_start": first_stamp.isoformat(),
        "utc_end": last_stamp.isoformat(),
        "hourly_start": observations[0].observed_at.isoformat() if observations else None,
        "hourly_end": observations[-1].observed_at.isoformat() if observations else None,
        "power_values_outside_0_1": out_of_range_power,
        "warnings": warnings,
    }
    return ObservationDataset(observations=tuple(observations), report=report)


def _parse_timestamp(value: str, line: int) -> datetime:
    match = _TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid source timestamp at CSV row {line}: expected YYYY-MM-DD H:MM:SS")
    day, hour, minute, second = match.groups()
    try:
        result = datetime.fromisoformat(f"{day}T{hour.zfill(2)}:{minute}:{second}")
    except ValueError as exc:
        raise ValueError(f"invalid source timestamp at CSV row {line}") from exc
    if result.minute % 10 or result.second:
        raise ValueError(f"CSV row {line} is not aligned to a ten-minute boundary")
    return result


def _localize(value: datetime, zone: ZoneInfo, line: int) -> datetime:
    candidates = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(_UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(candidate)
    if not candidates:
        raise ValueError(f"nonexistent local time at CSV row {line}: {value} in {zone.key}")
    if len(candidates) != 1:
        raise ValueError(f"ambiguous local time at CSV row {line}: {value} in {zone.key}")
    return candidates.pop()


def _finite_number(value: str, column: str, line: int) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid numeric value for {column} at CSV row {line}") from exc
    if not math.isfinite(number):
        raise ValueError(f"nonfinite numeric value for {column} at CSV row {line}")
    return number
