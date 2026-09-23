"""Person 1 SCADA inspection and explicitly configured preparation commands."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from wind_forecast.data.source_adapter import (
    load_source_config,
    prepare_observations,
    profile_source,
    publish_prepared_artifact,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and prepare original SCADA observations")
    parser.add_argument("command", choices=("profile", "prepare"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--config", type=Path, help="Reviewed JSON mapping; required for prepare")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    if args.command == "profile":
        print(json.dumps(profile_source(args.source), indent=2))
        return
    if args.config is None:
        parser.error("prepare requires --config")
    config = load_source_config(args.config)
    prepared = prepare_observations(args.source, config)
    summary = {
        "status": "verified_source" if config.source_verified else "staging_unverified",
        "source_sha256": prepared.report.source_sha256,
        "source_rows": prepared.report.source_rows,
        "retained_rows": prepared.report.retained_rows,
        "identical_duplicates": prepared.report.duplicate_identical,
        "quality_counts": prepared.report.quality_counts,
        "problem_counts": dict(
            sorted(
                Counter(
                    reason for problem in prepared.report.problems for reason in problem.reasons
                ).items()
            )
        ),
    }
    if config.source_verified:
        summary["artifact_dir"] = str(publish_prepared_artifact(prepared, config, args.output_root))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
