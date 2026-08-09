"""Ablation divergence Binance Spot vs USD-M taker flow trên entry OKX."""
import argparse
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import _metrics, _segments  # noqa: E402


def _flow(path, prefix):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    df = pd.DataFrame(raw["rows"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df[f"{prefix}_imb"] = df["taker_imbalance"]
    df[f"{prefix}_imb3"] = df["taker_imbalance"].rolling(3).mean()
    df[f"{prefix}_vol_ratio"] = df["volume"] / df["volume"].shift(1).rolling(12).mean()
    keep = ["ts", "close", f"{prefix}_imb", f"{prefix}_imb3", f"{prefix}_vol_ratio"]
    return raw["metadata"], df[keep].rename(columns={"close": f"{prefix}_close"}).set_index("ts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spot-flow", required=True)
    parser.add_argument("--futures-flow", required=True)
    parser.add_argument("--artifacts", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    spot_meta, spot = _flow(Path(args.spot_flow), "spot")
    futures_meta, futures = _flow(Path(args.futures_flow), "fut")
    flow = spot.join(futures, how="inner")
    flow["basis_pct"] = (flow["fut_close"] / flow["spot_close"] - 1) * 100
    gates = {
        "both_buy": lambda f: f.spot_imb > 0 and f.fut_imb > 0,
        "futures_leads_buy": lambda f: f.fut_imb > 0 and f.spot_imb <= 0,
        "spot_leads_buy": lambda f: f.spot_imb > 0 and f.fut_imb <= 0,
        "both_sell_absorption": lambda f: f.spot_imb < 0 and f.fut_imb < 0,
        "both_3bar_buy": lambda f: f.spot_imb3 > 0 and f.fut_imb3 > 0,
        "futures_3bar_leads": lambda f: f.fut_imb3 > 0 and f.spot_imb3 <= 0,
        "futures_discount_buy": lambda f: f.basis_pct < 0 and f.fut_imb > 0,
        "discount_absorption": lambda f: f.basis_pct < 0 and f.spot_imb < 0 and f.fut_imb < 0,
        "high_volume_both_buy": lambda f: f.spot_imb > 0 and f.fut_imb > 0 and f.spot_vol_ratio >= 1.2 and f.fut_vol_ratio >= 1.2,
    }
    results, coverage = {}, {}
    for artifact_path in args.artifacts:
        artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        dataset_end = pd.Timestamp(artifact["dataset"]["end"])
        validation_end = dataset_end - pd.Timedelta(days=30)
        train_end = validation_end - pd.Timedelta(days=30)
        for variant, events in artifact["events"].items():
            key = f"{Path(artifact_path).stem}:{variant}"
            matched = []
            for event in events:
                ts = pd.Timestamp(event["signal_ts"]) - pd.Timedelta(minutes=5)
                if ts in flow.index:
                    matched.append((event, flow.loc[ts]))
            coverage[key] = {"source_events": len(events), "matched_events": len(matched)}
            results[key] = {}
            for gate_name, predicate in gates.items():
                selected = [event for event, values in matched if predicate(values)]
                segmented = _segments(selected, train_end, validation_end)
                results[key][gate_name] = {name: _metrics(rows) for name, rows in segmented.items()}
    passes = []
    for event, event_results in results.items():
        for gate, segments in event_results.items():
            values = [segments[name] for name in ("train", "validation", "test")]
            if all(m["n"] >= 20 and m["mean_net_return_pct"] is not None and m["mean_net_return_pct"] > 0 and (m["profit_factor"] is None or m["profit_factor"] > 1) for m in values):
                passes.append({"event": event, "gate": gate, "metrics": segments})
    output = {"contract": {"alignment": "same closed 5m candle", "minimum_events_per_segment": 20}, "spot_metadata": spot_meta, "futures_metadata": futures_meta, "coverage": coverage, "passes": passes, "results": results}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"matched_flow_bars": len(flow), "passes": passes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
