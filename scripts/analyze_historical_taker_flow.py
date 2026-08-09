"""Ablation Binance 5m taker-flow trên các entry event OKX đã cố định."""
import argparse
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import _metrics, _segments  # noqa: E402


def _load_flow(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    df = pd.DataFrame(raw["rows"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df["imbalance_3"] = df["taker_imbalance"].rolling(3).mean()
    df["imbalance_12"] = df["taker_imbalance"].rolling(12).mean()
    df["imbalance_prev3"] = df["taker_imbalance"].shift(1).rolling(3).mean()
    df["volume_ratio_12"] = df["volume"] / df["volume"].shift(1).rolling(12).mean()
    return raw["metadata"], df.set_index("ts")


def _attach(rows, flow):
    attached = []
    for row in rows:
        signal_ts = pd.Timestamp(row["signal_ts"]) - pd.Timedelta(minutes=5)
        if signal_ts not in flow.index:
            continue
        value = flow.loc[signal_ts]
        if isinstance(value, pd.DataFrame):
            value = value.iloc[-1]
        attached.append({
            **row,
            "taker_flow": {
                "imbalance": float(value["taker_imbalance"]),
                "imbalance_3": float(value["imbalance_3"]),
                "imbalance_12": float(value["imbalance_12"]),
                "imbalance_prev3": float(value["imbalance_prev3"]),
                "volume_ratio_12": float(value["volume_ratio_12"]),
                "trade_count": int(value["trade_count"]),
            },
        })
    return attached


def _evaluate(rows, train_end, validation_end):
    gates = {
        "positive_current": lambda f: f["imbalance"] > 0,
        "positive_3bar": lambda f: f["imbalance_3"] > 0,
        "positive_12bar": lambda f: f["imbalance_12"] > 0,
        "current_and_3bar": lambda f: f["imbalance"] > 0 and f["imbalance_3"] > 0,
        "flow_flip_positive": lambda f: f["imbalance_prev3"] <= 0 and f["imbalance"] > 0,
        "strong_buy_flow": lambda f: f["imbalance"] >= 0.20,
        "buy_flow_volume": lambda f: f["imbalance"] > 0 and f["volume_ratio_12"] >= 1.20,
        "negative_current_absorption": lambda f: f["imbalance"] < 0,
        "negative_3bar_absorption": lambda f: f["imbalance_3"] < 0,
        "negative_12bar_absorption": lambda f: f["imbalance_12"] < 0,
        "strong_sell_absorption": lambda f: f["imbalance"] <= -0.20,
        "sell_flow_recovery": lambda f: f["imbalance_prev3"] < 0 and f["imbalance"] > f["imbalance_prev3"],
        "sell_absorption_volume": lambda f: f["imbalance"] < 0 and f["volume_ratio_12"] >= 1.20,
    }
    result = {}
    for name, predicate in gates.items():
        selected = [row for row in rows if predicate(row["taker_flow"])]
        segmented = _segments(selected, train_end, validation_end)
        result[name] = {segment: _metrics(values) for segment, values in segmented.items()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--artifacts", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    flow_meta, flow = _load_flow(Path(args.flow_cache))
    results, coverage = {}, {}
    for artifact_path in args.artifacts:
        artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        dataset_end = pd.Timestamp(artifact["dataset"]["end"])
        validation_end = dataset_end - pd.Timedelta(days=30)
        train_end = validation_end - pd.Timedelta(days=30)
        for variant, rows in artifact["events"].items():
            key = f"{Path(artifact_path).stem}:{variant}"
            attached = _attach(rows, flow)
            coverage[key] = {"source_events": len(rows), "matched_events": len(attached)}
            results[key] = _evaluate(attached, train_end, validation_end)
    passes = []
    for key, gates in results.items():
        for gate, segments in gates.items():
            metrics = [segments[name] for name in ("train", "validation", "test")]
            if all(
                metric["n"] >= 20 and metric["mean_net_return_pct"] is not None
                and metric["mean_net_return_pct"] > 0
                and (metric["profit_factor"] is None or metric["profit_factor"] > 1)
                for metric in metrics
            ):
                passes.append({"event": key, "flow_gate": gate, "metrics": segments})
    output = {
        "contract": {"feature_source": "Binance Public Data 5m taker buy base volume", "alignment": "same 5m candle start timestamp; signal uses closed candle", "minimum_events_per_segment": 20},
        "flow_metadata": flow_meta, "coverage": coverage,
        "passes": passes, "results": results,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"coverage": coverage, "passes": passes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
