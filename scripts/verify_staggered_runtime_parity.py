"""So sánh trade-for-trade production core với research reference đã frozen."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine import staggered_pullback as production  # noqa: E402


def _load_reference():
    path = Path(__file__).with_name("analyze_staggered_slow_pullback.py")
    spec = importlib.util.spec_from_file_location("research_staggered", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_source(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    return frame


def _compare_trade(reference: dict, actual: dict) -> list[str]:
    differences = []
    exact = ("side", "entry_ts", "exit_ts", "exit_reason", "excursion_id")
    numeric = ("entry_price", "exit_price", "stop", "net_return_pct", "tranche_capital_fraction")
    for key in exact:
        if reference[key] != actual[key]:
            differences.append(f"{key}: {reference[key]!r} != {actual[key]!r}")
    for key in numeric:
        if not np.isclose(float(reference[key]), float(actual[key]), rtol=0, atol=1e-9):
            differences.append(f"{key}: {reference[key]!r} != {actual[key]!r}")
    return differences


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--frozen-artifact", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source_path = Path(args.flow_cache)
    artifact = json.loads(Path(args.frozen_artifact).read_text(encoding="utf-8"))
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if digest != artifact["dataset"]["sha256"]:
        raise AssertionError("dataset hash khác frozen artifact")

    expected_contract = {
        "lookback_4h": production.FROZEN_CONTRACT.z_lookback_bars,
        "entry_z": production.FROZEN_CONTRACT.entry_z,
        "exit_z": production.FROZEN_CONTRACT.exit_z,
        "stop_atr": production.FROZEN_CONTRACT.stop_atr,
        "max_tranches": production.FROZEN_CONTRACT.max_tranches,
        "limit_offset_pct": 0.0,
    }
    if "selected" in artifact:
        selected = {
            "lookback_4h": artifact["selected"]["z_lookback_bars"],
            "entry_z": artifact["selected"]["entry_z"],
            "exit_z": artifact["selected"]["exit_z"],
            "stop_atr": artifact["selected"]["stop_atr"],
            "max_tranches": artifact["selected"]["max_tranches"],
            "limit_offset_pct": 0.0,
        }
    else:
        selected = {
            "lookback_4h": artifact["contract"]["z_lookback_bars"],
            "entry_z": artifact["contract"]["entry_z"],
            "exit_z": artifact["contract"]["exit_z"],
            "stop_atr": artifact["contract"]["stop_atr"],
            "max_tranches": artifact["contract"]["max_tranches"],
            "limit_offset_pct": 0.0,
        }
    if selected != expected_contract:
        raise AssertionError(f"production contract khác artifact: {expected_contract} != {selected}")

    reference = _load_reference()
    reference.COST_PCT = production.FROZEN_CONTRACT.round_trip_cost_pct
    reference_bars = reference._load_bars(source_path)
    production_bars = production.add_features(production.aggregate_closed_4h(_load_source(source_path)))
    if not reference_bars.index.equals(production_bars.index):
        raise AssertionError("4h bar index không khớp")
    for column in ("open", "high", "low", "close", "atr", "ema180"):
        production_column = "trend_ema" if column == "ema180" else column
        if not np.allclose(
            reference_bars[column].to_numpy(), production_bars[production_column].to_numpy(),
            rtol=0, atol=1e-10, equal_nan=True,
        ):
            raise AssertionError(f"feature {column} không khớp")

    splits = {
        "train": (artifact["dataset"]["start"], artifact["dataset"]["train_end"]),
        "validation": (artifact["dataset"]["train_end"], artifact["dataset"]["validation_end"]),
        "test": (artifact["dataset"]["validation_end"], artifact["dataset"]["end"]),
    }
    result = {
        "dataset_sha256": digest,
        "production_contract": production.FROZEN_CONTRACT.manifest(),
        "feature_parity": True,
        "segments": {},
    }
    all_pass = True
    for name, raw_bounds in splits.items():
        start, end = map(pd.Timestamp, raw_bounds)
        expected = reference._run(
            reference_bars, start, end,
            expected_contract["lookback_4h"], expected_contract["entry_z"],
            expected_contract["exit_z"], expected_contract["stop_atr"],
            expected_contract["max_tranches"], expected_contract["limit_offset_pct"],
        )
        actual = production.replay(production_bars, start, end)
        mismatches = []
        if len(expected) != len(actual):
            mismatches.append(f"trade_count: {len(expected)} != {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual)):
            differences = _compare_trade(left, right)
            if differences:
                mismatches.append({"trade_index": index, "differences": differences})
                if len(mismatches) >= 20:
                    break
        segment_pass = not mismatches
        all_pass = all_pass and segment_pass
        result["segments"][name] = {
            "reference_trades": len(expected), "production_trades": len(actual),
            "trade_for_trade_pass": segment_pass, "mismatches": mismatches,
        }
    result["passed"] = all_pass
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not all_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
