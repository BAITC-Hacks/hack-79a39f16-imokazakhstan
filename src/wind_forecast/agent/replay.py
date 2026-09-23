"""Chronological, bounded replay and provisional February submission export."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from wind_forecast.agent.settings import RunConfig
from wind_forecast.contracts import require_utc

_FIELDS = (
    "turbine_id", "issue_time", "interval_start", "valid_time", "lead_hours",
    "prediction", "p10", "p50", "p90", "forecast_id", "model_id", "model_hash",
    "input_hash", "weather_bundle_id", "weather_source_hash", "is_synthetic",
)


@dataclass(frozen=True)
class ReplayResult:
    run_dir: Path
    summary: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {"run_dir": str(self.run_dir), "summary": self.summary}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _model_hash(report: dict[str, object]) -> str:
    for name in ("model", "model_metadata"):
        metadata = report.get(name)
        if isinstance(metadata, dict) and metadata.get("artifact_sha256"):
            return str(metadata["artifact_sha256"])
    return str(report.get("model_hash") or "")


def _evaluation(rows: list[dict[str, object]], datasets: dict) -> dict[str, object]:
    # Targets enter this reporting function only after every forecast has finished.
    targets = {
        (observation.turbine_id, observation.observed_at.isoformat()): observation.power_norm
        for dataset in datasets.values()
        for observation in dataset.observations
        if observation.power_norm is not None and math.isfinite(observation.power_norm)
        and observation.quality_flag == "ok"
    }
    errors: dict[str, list[float]] = {}
    for row in rows:
        target = targets.get((row["turbine_id"], row["valid_time"]))
        if target is not None:
            errors.setdefault(str(row["turbine_id"]), []).append(float(row["prediction"]) - target)
    flat = [error for values in errors.values() for error in values]
    if not flat:
        return {
            "status": "not_scored", "matched_target_rows": 0, "mae": None, "rmse": None,
            "reason": "No observed February targets match the exported predictions; no score is fabricated.",
            "per_turbine": {},
        }

    def metrics(values: list[float]) -> dict[str, float | int]:
        return {
            "matched_target_rows": len(values),
            "mae": math.fsum(abs(error) for error in values) / len(values),
            "rmse": math.sqrt(math.fsum(error * error for error in values) / len(values)),
        }

    return {
        "status": "synthetic_diagnostic" if any(row["is_synthetic"] for row in rows) else "diagnostic",
        **metrics(flat), "target_units": "normalized_active_power",
        "reason": "Post-run diagnostics on available targets; the official scoring metric is unconfirmed.",
        "per_turbine": {turbine: metrics(values) for turbine, values in sorted(errors.items())},
    }


def run_replay(
    base_config: RunConfig, issue_start: datetime, issue_end: datetime, step_hours: int = 24,
) -> ReplayResult:
    """Run up to 64 inclusive hourly-aligned issue times, in chronological order.

    Observation files are loaded once. Every issue calls the same application
    boundary with a deterministic controller, preserving its as-of checks and
    individual artifacts. ``submission.csv`` uses the latest issued eligible
    forecast per turbine/hour and remains provisional until organizers confirm
    the submission schema and forecast selection rule.
    """
    from wind_forecast.agent.application import load_datasets, run_forecast

    if not isinstance(base_config, RunConfig):
        raise ValueError("base_config must be a RunConfig")
    if not isinstance(step_hours, int) or isinstance(step_hours, bool) or step_hours <= 0:
        raise ValueError("step_hours must be a positive integer")
    start = require_utc(issue_start, "issue_start")
    end = require_utc(issue_end, "issue_end")
    if any((start.minute, start.second, start.microsecond, end.minute, end.second, end.microsecond)):
        raise ValueError("replay issue times must align to a UTC hour")
    if end < start:
        raise ValueError("issue_end must be at or after issue_start")
    count = (end - start) // timedelta(hours=step_hours) + 1
    if count > 64:
        raise ValueError("a replay is bounded to 64 issue times; use a larger step or shorter range")
    issues = [start + timedelta(hours=step_hours * index) for index in range(count)]
    directory = Path(base_config.output_root) / "replay" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex
    )
    directory.mkdir(parents=True, exist_ok=False)
    config = replace(base_config, issue_time=start, controller="deterministic",
                     output_root=str(directory / "issues"))
    request_config = {
        "base_config": base_config.to_dict(), "effective_config": config.to_dict(),
        "issue_start": start.isoformat(), "issue_end": end.isoformat(),
        "step_hours": step_hours, "issue_count": count,
        "issue_times": [issue.isoformat() for issue in issues],
        "controller_policy": "deterministic; no OpenAI API calls in batch replay",
    }
    _write_json(directory / "request.json", request_config)
    datasets: dict = {}
    load_error: str | None = None
    load_error_state = "blocked"
    try:
        datasets = load_datasets(config)
    except (ValueError, OSError) as exc:
        load_error = str(exc)
    except Exception as exc:
        load_error, load_error_state = str(exc), "failed"
    rows: list[dict[str, object]] = []
    issue_reports = []
    for issue in issues:
        if load_error is not None:
            issue_reports.append({"issue_time": issue.isoformat(), "state": load_error_state,
                                  "error": load_error, "run_dir": None, "row_count": 0})
            continue
        try:
            run = run_forecast(replace(config, issue_time=issue), datasets=datasets)
            report = {
                "issue_time": issue.isoformat(), "state": run.state,
                "error": run.error, "run_dir": str(run.run_dir) if run.run_dir else None,
                "row_count": len(run.result.rows) if run.result and run.state == "completed" else 0,
            }
            if run.state not in {"completed", "blocked", "failed"}:
                raise ValueError(f"application returned an unknown state: {run.state}")
            if run.state == "completed":
                if run.result is None or run.weather is None:
                    raise ValueError("completed application run lacks a result or weather bundle")
                model_hash = _model_hash(run.report)
                for row in run.result.rows:
                    if row.issue_time != issue or row.valid_time <= issue:
                        raise ValueError("application forecast row does not match the replay issue")
                    rows.append({
                        "turbine_id": row.turbine_id, "issue_time": row.issue_time.isoformat(),
                        "interval_start": (row.valid_time - timedelta(hours=1)).isoformat(),
                        "valid_time": row.valid_time.isoformat(), "lead_hours": row.lead_hours,
                        "prediction": row.prediction, "p10": row.p10, "p50": row.p50, "p90": row.p90,
                        "forecast_id": run.result.forecast_id, "model_id": run.result.model_id,
                        "model_hash": model_hash, "input_hash": run.result.input_hash,
                        "weather_bundle_id": run.result.weather_bundle_id,
                        "weather_source_hash": run.weather.source_hash,
                        "is_synthetic": run.result.is_synthetic or run.weather.is_synthetic,
                    })
            issue_reports.append(report)
        except Exception as exc:
            # A failed issue is visible and does not erase earlier successful issues.
            rows = [row for row in rows if row["issue_time"] != issue.isoformat()]
            issue_reports.append({"issue_time": issue.isoformat(), "state": "failed",
                                  "error": str(exc), "run_dir": None, "row_count": 0})
    rows.sort(key=lambda row: (row["issue_time"], row["turbine_id"], row["valid_time"]))
    zone = ZoneInfo(base_config.source_timezone)
    month_start = datetime(2026, 2, 1, tzinfo=zone).astimezone(timezone.utc)
    month_end = datetime(2026, 3, 1, tzinfo=zone).astimezone(timezone.utc)
    february = [
        row for row in rows
        if datetime.fromisoformat(str(row["interval_start"])) >= month_start
        and datetime.fromisoformat(str(row["valid_time"])) <= month_end
    ]
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for row in february:
        key = (str(row["turbine_id"]), str(row["valid_time"]))
        previous = latest.get(key)
        if previous is None or str(row["issue_time"]) > str(previous["issue_time"]):
            latest[key] = row
    submission = sorted(latest.values(), key=lambda row: (row["turbine_id"], row["valid_time"]))
    hours = int((month_end - month_start).total_seconds() // 3600)
    expected = {
        (turbine, (month_start + timedelta(hours=lead)).isoformat())
        for turbine in base_config.turbine_ids for lead in range(1, hours + 1)
    }
    missing = sorted(expected - latest.keys())
    coverage = {
        "month": "2026-02", "source_timezone": base_config.source_timezone,
        "interval_label": "end", "month_start_utc": month_start.isoformat(),
        "month_end_utc": month_end.isoformat(), "expected_hours_per_turbine": hours,
        "expected_rows": len(expected), "unique_rows": len(submission),
        "missing_count": len(missing), "complete": not missing,
        "missing": [{"turbine_id": turbine, "valid_time": valid} for turbine, valid in missing],
        "per_turbine": {
            turbine: {
                "expected": hours,
                "present": sum(row["turbine_id"] == turbine for row in submission),
                "missing": sum(key[0] == turbine for key in missing),
            } for turbine in base_config.turbine_ids
        },
    }
    counts = {state: sum(report["state"] == state for report in issue_reports)
              for state in ("completed", "blocked", "failed")}
    if counts["completed"] == count:
        state = "completed"
    elif counts["completed"]:
        state = "partial"
    elif counts["blocked"] == count:
        state = "blocked"
    else:
        state = "failed"
    synthetic = base_config.mode == "fixture" or any(row["is_synthetic"] for row in rows)
    warnings = [
        "Submission columns and latest-issue selection are provisional and require organizer confirmation.",
        "February filtering uses complete hourly intervals in the configured source timezone.",
    ]
    if synthetic:
        warnings.append("Synthetic fixture outputs must not be submitted as real forecasts.")
    if missing:
        warnings.append(f"February export is incomplete: {len(missing)} turbine/hour rows are missing.")
    if not base_config.assumptions_confirmed:
        warnings.append("The operator has not acknowledged the source timezone and interval assumptions.")
    summary: dict[str, object] = {
        "schema_version": "replay-v1", "state": state, "run_dir": str(directory),
        "created_at": datetime.now(timezone.utc).isoformat(), "issue_count": count,
        "issue_counts": counts, "issues": issue_reports, "rolling_rows": len(rows),
        "february_rows_with_overlaps": len(february), "submission_rows": len(submission),
        "is_synthetic": synthetic, "coverage": coverage,
        "submission_format_status": "provisional_requires_organizer_confirmation",
        "evaluation": _evaluation(submission, datasets), "warnings": warnings,
        "budget": {"controller": "deterministic", "openai_api_calls": 0, "maximum_issues": 64,
                   "weather_network": "Only requested issue windows; provider cache applies. Original NOAA downloads can total several GB for a month."},
        "files": {"rolling": "rolling.csv", "february": "february.csv",
                  "submission": "submission.csv", "configuration": "request.json"},
    }
    _write_csv(directory / "rolling.csv", rows)
    _write_csv(directory / "february.csv", february)
    _write_csv(directory / "submission.csv", submission)
    _write_json(directory / "summary.json", summary)
    return ReplayResult(directory, summary)
