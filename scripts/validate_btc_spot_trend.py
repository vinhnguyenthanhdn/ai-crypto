"""Frozen metric, neighborhood and production-core parity validator."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from discover_btc_spot_trend_9y import load_daily, replay  # noqa: E402
from discover_cross_sectional_momentum import metrics  # noqa: E402
from src.engine import btc_spot_trend as strategy  # noqa: E402


NEIGHBORS = (
    (50, 0.0, 30, 30.0), (50, 2.0, 30, 30.0),
    (100, 1.0, 30, 30.0), (50, 1.0, 60, 30.0),
    (50, 1.0, 30, 20.0), (50, 1.0, 30, 10.0),
)


def evaluate(daily, contract, splits, cost):
    featured = strategy.add_features(daily, contract)
    result = {}
    for name, bounds in splits.items():
        values, timestamps, assets = replay(daily, featured.target_exposure, *bounds, cost)
        result[name] = metrics(values, timestamps, assets, *bounds, name == "train")
    return result, featured


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--discovery", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source = Path(args.data)
    discovery = json.loads(Path(args.discovery).read_text(encoding="utf-8"))
    selected = discovery["selected"]
    expected = {"slow_days": 50, "buffer_pct": 1.0, "volatility_days": 30,
                "target_volatility_pct": 30.0}
    observed = {key: selected[key] for key in expected}
    if observed != expected:
        raise AssertionError(f"discovery contract changed: {observed}")
    daily = load_daily(source)
    splits = {
        "train": (pd.Timestamp(discovery["dataset"]["start"]), pd.Timestamp(discovery["dataset"]["train_end"])),
        "validation": (pd.Timestamp(discovery["dataset"]["train_end"]), pd.Timestamp(discovery["dataset"]["validation_end"])),
        "test": (pd.Timestamp(discovery["dataset"]["validation_end"]), pd.Timestamp(discovery["dataset"]["end"])),
    }
    base, featured = evaluate(daily, strategy.FROZEN_CONTRACT, splits, 0.12)
    stress, _ = evaluate(daily, strategy.FROZEN_CONTRACT, splits, 0.24)
    metric_mismatches = []
    for name in splits:
        for key in ("net_return_pct", "profit_factor", "max_drawdown_pct"):
            if not np.isclose(base[name][key], selected["metrics"][name][key], atol=1e-6, rtol=0):
                metric_mismatches.append([name, key, base[name][key], selected["metrics"][name][key]])
            if not np.isclose(stress[name][key], discovery["cost_stress"][name][key], atol=1e-6, rtol=0):
                metric_mismatches.append([f"stress_{name}", key, stress[name][key], discovery["cost_stress"][name][key]])
    neighborhood = []
    for slow, buffer_pct, volatility_days, target_volatility_pct in NEIGHBORS:
        contract = strategy.Contract(slow_days=slow, trend_buffer_pct=buffer_pct,
                                     volatility_days=volatility_days,
                                     target_volatility_pct=target_volatility_pct)
        results, _ = evaluate(daily, contract, splits, 0.24)
        passed = all(results[name]["net_return_pct"] > 0 and
                     (results[name]["profit_factor"] or 0) > 1 for name in splits)
        neighborhood.append({"contract": contract.manifest(), "passed": passed, "stress": results})
    all_gates = all(base[name]["net_return_pct"] > 0 and stress[name]["net_return_pct"] > 0
                    and (stress[name]["profit_factor"] or 0) > 1 for name in splits)
    output = {
        "passed": not metric_mismatches and all_gates and sum(x["passed"] for x in neighborhood) >= 4,
        "dataset": {"source": str(source.resolve()), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    **discovery["dataset"]},
        "contract": strategy.FROZEN_CONTRACT.manifest(),
        "strategy_package_id": strategy.PACKAGE_ID,
        "metric_parity": {"passed": not metric_mismatches, "mismatches": metric_mismatches},
        "base": base, "stress": stress,
        "neighborhood": {"passed_count": sum(x["passed"] for x in neighborhood),
                         "required": 4, "results": neighborhood},
        "feature_rows": len(featured),
    }
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"passed": output["passed"], "metric_parity": output["metric_parity"],
                      "neighborhood_passed": output["neighborhood"]["passed_count"],
                      "base": base, "stress": stress}, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
