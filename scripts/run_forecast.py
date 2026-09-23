"""Reproducible application entry point: one issue or a chronological replay."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.settings import RunConfig, parse_time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--replay-start", help="Inclusive ISO timestamp with timezone")
    parser.add_argument("--replay-end", help="Inclusive ISO timestamp with timezone")
    parser.add_argument("--step-hours", type=int, default=24)
    parser.add_argument("--strict-submission", action="store_true", help="Fail unless February coverage is complete, every issue succeeds, and inputs/results are non-synthetic")
    args = parser.parse_args()
    try:
        config = RunConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
        if args.replay_start or args.replay_end:
            if not (args.replay_start and args.replay_end):
                parser.error("provide both --replay-start and --replay-end")
            from wind_forecast.agent.replay import run_replay
            replay = run_replay(config,parse_time(args.replay_start),parse_time(args.replay_end),step_hours=args.step_hours)
            print(json.dumps(replay.to_dict(),indent=2,default=str))
            return 0 if (replay.summary["submission_check"]["ready"] if args.strict_submission else replay.summary["state"] == "completed") else 2
        run = run_forecast(config)
        print(json.dumps({"state":run.state,"summary":run.summary,"run_dir":str(run.run_dir)},indent=2))
        return 0 if run.state == "completed" else 2
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}",file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
