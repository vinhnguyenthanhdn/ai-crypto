"""Fail-closed health check for the no-order composite forward collector."""
import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_ROOT = Path("/Users/administrator/Library/Application Support/ai-crypto-funding-forward")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--maximum-age-hours", type=float, default=2.5)
    args = parser.parse_args()
    status_path = args.root / "data/backtests/composite_forward_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    observed = pd.Timestamp(status["observed_at"])
    now = pd.Timestamp.now(tz="UTC")
    age_hours = (now - observed).total_seconds() / 3600

    failures = []
    if status.get("mode") != "COMPOSITE_FRESH_FORWARD_PAPER_NO_ORDER":
        failures.append("unexpected mode")
    if status.get("live_execution") is not False:
        failures.append("live_execution is not false")
    if age_hours < 0 or age_hours > args.maximum_age_hours:
        failures.append(f"observation age {age_hours:.2f}h outside allowed range")

    if status.get("collection_healthy") is not True:
        failures.append("composite collection integrity is not healthy")

    result = {
        "healthy": not failures,
        "mode": status.get("mode"),
        "live_execution": status.get("live_execution"),
        "observation_age_hours": age_hours,
        "promotion_ready": status.get("promotion_ready"),
        "progress": status.get("progress"),
        "failures": failures,
    }
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
