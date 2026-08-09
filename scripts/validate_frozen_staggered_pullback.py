"""Audit độc lập champion staggered-pullback theo portfolio-profit contract."""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.optimize_staggered_portfolio import _portfolio_metrics  # noqa: E402
from src.engine import staggered_pullback as strategy  # noqa: E402


EXPECTED = {
    "train": {"portfolio_net_return_pct": 4.550053, "portfolio_profit_factor": 2.624387,
              "max_drawdown_pct": 1.454373, "tickets": 88, "excursions": 30},
    "validation": {"portfolio_net_return_pct": 0.336363, "portfolio_profit_factor": 1.316404,
                   "max_drawdown_pct": 1.037415, "tickets": 18, "excursions": 5},
    "test": {"portfolio_net_return_pct": 1.497148, "portfolio_profit_factor": 3.194469,
             "max_drawdown_pct": 0.633931, "tickets": 30, "excursions": 10},
}
STRESS_COSTS_PCT = (0.30, 0.40, 0.50, 0.60, 0.80)
REQUIRED_STRESS_COST_PCT = 0.60


def _load_source(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    return frame


def _core(metric):
    return {key: metric[key] for key in (
        "portfolio_net_return_pct", "portfolio_profit_factor",
        "max_drawdown_pct", "tickets", "excursions",
    )}


def _matches(actual, expected):
    return all(
        actual[key] == value if isinstance(value, int)
        else np.isclose(actual[key], value, rtol=0, atol=1e-6)
        for key, value in expected.items()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--split-artifact", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source = Path(args.flow_cache)
    split = json.loads(Path(args.split_artifact).read_text(encoding="utf-8"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != split["dataset"]["sha256"]:
        raise AssertionError("dataset hash không khớp split artifact")

    bars = strategy.aggregate_closed_4h(_load_source(source))
    splits = {
        "train": (pd.Timestamp(split["dataset"]["start"]), pd.Timestamp(split["dataset"]["train_end"])),
        "validation": (pd.Timestamp(split["dataset"]["train_end"]), pd.Timestamp(split["dataset"]["validation_end"])),
        "test": (pd.Timestamp(split["dataset"]["validation_end"]), pd.Timestamp(split["dataset"]["end"])),
    }
    featured = strategy.add_features(bars)
    result = {
        "passed": False,
        "status": "FROZEN_RESEARCH_CHAMPION_RUNTIME_OFF",
        "dataset": {**split["dataset"], "sha256": digest},
        "contract": strategy.FROZEN_CONTRACT.manifest(),
        "selection_note": "Selected on train-only restricted grid; retained after expanded challenger failed cost stress.",
        "risk_contract": {
            "risk_per_excursion_pct": 1.0,
            "capital_cap_per_tranche": "1/max_tranches of current equity",
            "portfolio_return": "compounded equity return",
        },
        "segments": {},
        "cost_stress": {},
    }

    for name, bounds in splits.items():
        trades = strategy.replay(featured, *bounds)
        metric = _core(_portfolio_metrics(trades, *bounds, strategy.FROZEN_CONTRACT))
        result["segments"][name] = {
            **metric, "matches_frozen_expected": _matches(metric, EXPECTED[name]),
        }

    for cost in STRESS_COSTS_PCT:
        contract = strategy.Contract(**{
            **strategy.FROZEN_CONTRACT.manifest(), "round_trip_cost_pct": cost,
        })
        result["cost_stress"][str(cost)] = {}
        for name, bounds in splits.items():
            trades = strategy.replay(featured, *bounds, contract)
            metric = _portfolio_metrics(trades, *bounds, contract)
            result["cost_stress"][str(cost)][name] = {
                key: metric[key] for key in (
                    "portfolio_net_return_pct", "portfolio_profit_factor", "max_drawdown_pct",
                )
            }

    required_stress = result["cost_stress"][str(REQUIRED_STRESS_COST_PCT)]
    result["required_cost_stress_pass"] = all(
        metric["portfolio_net_return_pct"] > 0
        and metric["portfolio_profit_factor"] is not None
        and metric["portfolio_profit_factor"] > 1
        for metric in required_stress.values()
    )
    result["passed"] = (
        all(segment["matches_frozen_expected"] for segment in result["segments"].values())
        and result["required_cost_stress_pass"]
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": result["passed"], "segments": result["segments"],
        "required_cost_stress_pct": REQUIRED_STRESS_COST_PCT,
        "required_cost_stress": required_stress,
    }, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
