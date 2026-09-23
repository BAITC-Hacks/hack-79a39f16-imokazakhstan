"""Watch inputs and automatically recalculate changed wind forecasts."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wind_forecast.agent.monitor import ForecastMonitor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="RunConfig JSON; reloaded each poll")
    parser.add_argument("--state-dir", type=Path, default=Path("runs/monitor"))
    parser.add_argument("--interval-seconds", type=float, default=300, help="30–86400 seconds; default 300")
    parser.add_argument("--live", action="store_true", help="Require live mode and roll issue time to the current UTC hour")
    limits = parser.add_mutually_exclusive_group()
    limits.add_argument("--once", action="store_true", help="One poll; reuse persistent state if present")
    limits.add_argument("--max-cycles", type=int, help="Stop after this many polling cycles")
    args = parser.parse_args(argv)
    if args.max_cycles is not None and args.max_cycles < 1:
        parser.error("--max-cycles must be at least 1")
    try:
        monitor = ForecastMonitor(args.state_dir, poll_interval_seconds=args.interval_seconds,
                                  rolling_live=args.live)
    except ValueError as exc:
        parser.error(str(exc))
    cycle_limit = 1 if args.once else args.max_cycles
    cycle_count, exit_code = 0, 0
    try:
        while cycle_limit is None or cycle_count < cycle_limit:
            try:
                # Passing a dict lets the monitor record invalid configuration
                # updates without losing the last successful forecast.
                raw_config = json.loads(args.config.read_text(encoding="utf-8"))
                if not isinstance(raw_config, dict):
                    raise TypeError("configuration must be an object")
                result = monitor.poll(raw_config)
                print(json.dumps(result.to_dict(), ensure_ascii=False), flush=True)
                exit_code = 2 if result.state in {"failed", "blocked"} else 0
                next_check = result.next_check_at
            except (OSError, ValueError, TypeError) as exc:
                print(json.dumps({"state": "failed", "error": f"{type(exc).__name__}: cannot read monitor configuration/state"}), flush=True)
                exit_code, next_check = 2, None
            cycle_count += 1
            if cycle_limit is not None and cycle_count >= cycle_limit:
                break
            delay = (next_check - datetime.now(UTC)).total_seconds() if next_check else args.interval_seconds
            time.sleep(max(1, delay))
    except KeyboardInterrupt:
        print(json.dumps({"state": "stopped", "message": "Forecast versions and monitor state were retained."}), flush=True)
        return 0
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
