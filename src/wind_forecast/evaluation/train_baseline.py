"""Train and diagnose the curve on measured wind, never claim forecast skill."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform

from wind_forecast.contracts import Observation
from wind_forecast.evaluation.metrics import regression_metrics
from wind_forecast.models.power_curve import EmpiricalPowerCurve

TIME = "Статистическое время"
WIND = "Средняя скорость ветра(m/s)"
POWER = "Нормализованная активная мощность"
TEMP = "Средняя температура окружающей среды(°C)"


def read_source(path: Path, turbine: str, zone: timezone, delay: int):
    """Evaluation-only source reader; production adapters remain Person 1's responsibility."""
    rows = []
    seen = set()
    rejected = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {TIME, WIND, POWER, TEMP}.issubset(reader.fieldnames or []):
            raise ValueError(f"missing expected columns: {path.name}")
        for raw in reader:
            stamp = datetime.strptime(raw[TIME], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
            if stamp in seen:
                raise ValueError(f"duplicate timestamp in {turbine}: {stamp}")
            seen.add(stamp)
            try:
                wind, power = float(raw[WIND]), float(raw[POWER])
                if not math.isfinite(wind) or not math.isfinite(power) or wind < 0:
                    raise ValueError("invalid numeric pair")
            except ValueError:
                rejected += 1
                continue
            rows.append(Observation(turbine, stamp, stamp + timedelta(minutes=delay),
                                    power, wind))
    rows.sort(key=lambda row: row.observed_at)
    if not rows:
        raise ValueError(f"no usable rows: {path.name}")
    stamps = sorted(seen)
    grid = timedelta(minutes=10)
    irregular = sum((b - a) % grid != timedelta(0) for a, b in zip(stamps, stamps[1:]))
    if irregular:
        raise ValueError("timestamps do not follow a 10-minute grid")
    return rows, {
        "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_rows": len(seen), "valid_pairs": len(rows), "rejected_pairs": rejected,
        "first_timestamp": stamps[0].isoformat(), "last_timestamp": stamps[-1].isoformat(),
        "missing_10min_slots": int((stamps[-1] - stamps[0]) / grid) + 1 - len(seen),
        "rows_by_month": dict(sorted(Counter(t.strftime("%Y-%m") for t in stamps).items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turbine-1", type=Path, required=True)
    parser.add_argument("--turbine-2", type=Path, required=True)
    parser.add_argument("--utc-offset-hours", type=float, required=True)
    parser.add_argument("--availability-delay-minutes", type=int, default=10)
    parser.add_argument("--validation-start", default="2026-01-01")
    parser.add_argument("--test-start", default="2026-02-01")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.availability_delay_minutes < 0:
        parser.error("availability delay must be nonnegative")
    zone = timezone(timedelta(hours=args.utc_offset_hours))
    split = datetime.fromisoformat(args.validation_start).replace(tzinfo=zone)
    test = datetime.fromisoformat(args.test_start).replace(tzinfo=zone)
    if split >= test:
        parser.error("validation start must precede test start")
    all_rows, sources = [], {}
    for turbine, path in (("T1", args.turbine_1), ("T2", args.turbine_2)):
        rows, summary = read_source(path, turbine, zone, args.availability_delay_minutes)
        all_rows.extend(rows)
        sources[turbine] = summary
    train = [r for r in all_rows if r.observed_at < split and r.available_at <= split]
    valid = [r for r in all_rows if split <= r.observed_at < test and r.available_at <= test]
    final_train = [r for r in all_rows if r.observed_at < test and r.available_at <= test]
    common_metadata = {
        "features": ["turbine_id", "wind_ms"], "target": "power_norm (source units unchanged)",
        "training_wind": "measured SCADA wind, not archived forecast weather",
        "source_utc_offset_hours": args.utc_offset_hours,
        "availability_delay_minutes": args.availability_delay_minutes,
        "availability_basis": "assumed timestamp + delay; publication latency unverified",
        "sources": sources, "python_version": platform.python_version(),
        "dependencies": "Python standard library only",
        "validation_kind": "observed_wind_curve_diagnostic_not_operational_forecast",
    }
    model = EmpiricalPowerCurve().fit(train)
    model.metadata.update(common_metadata)
    model.metadata["split_boundary"] = split.isoformat()
    model.metadata["training_sample_count"] = len(train)
    results = {}
    for turbine in sources:
        tr = [r for r in train if r.turbine_id == turbine]
        va = [r for r in valid if r.turbine_id == turbine]
        if not tr or not va:
            raise ValueError(f"empty training or validation split for {turbine}")
        mean = sum(r.power_norm for r in tr) / len(tr)
        actual = [r.power_norm for r in va]
        prediction = [model.predict_power(turbine, r.wind_ms) for r in va]
        results[turbine] = {
            "train_n": len(tr), "validation_n": len(va),
            "curve": regression_metrics(actual, prediction),
            "training_mean": regression_metrics(actual, [mean] * len(va)),
        }
    # Exclusive output directory prevents overwriting an earlier version.
    args.output.mkdir(parents=True, exist_ok=False)
    validation_hash = model.save(args.output / "validation_model.json")
    restored = EmpiricalPowerCurve.load(args.output / "validation_model.json")
    for row in valid:
        if restored.predict_power(row.turbine_id, row.wind_ms) != model.predict_power(row.turbine_id, row.wind_ms):
            raise AssertionError("model roundtrip changed predictions")
    final = EmpiricalPowerCurve().fit(final_train)
    final.metadata.update(common_metadata)
    final.metadata["split_boundary"] = test.isoformat()
    final.metadata["training_sample_count"] = len(final_train)
    final_hash = final.save(args.output / "model.json")
    report = {
        "evaluation_kind": common_metadata["validation_kind"],
        "validation_start": split.isoformat(), "validation_end_exclusive": test.isoformat(),
        "sources": sources, "metrics_by_turbine": results,
        "validation_model_sha256": validation_hash, "final_model_sha256": final_hash,
        "final_train_n": len(final_train),
        "limitations": [
            "Uses measured wind at target time: not a 24/48-hour forecast evaluation.",
            "Lead-time metrics require archived weather forecasts and are not available.",
            "10-minute source resolution; no hourly aggregation or gap filling performed.",
            "Fixed UTC offset supplied by user; availability delay is an assumption.",
            "Power units preserved; no physical capacity or normalization definition assumed.",
            "Final model includes January; January scores apply only to validation_model.json.",
        ],
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "metrics": results, "final_model_sha256": final_hash}, indent=2))


if __name__ == "__main__":
    main()
