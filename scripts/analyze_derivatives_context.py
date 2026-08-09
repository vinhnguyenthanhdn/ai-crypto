"""Ablation causal funding/OI/positioning trên entry events đã cố định."""
import argparse
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import _metrics, _segments  # noqa: E402


def _load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    metrics = pd.DataFrame(raw["metrics"])
    metrics["ts"] = pd.to_datetime(metrics["ts"], unit="ms")
    metrics = metrics.sort_values("ts").set_index("ts")
    metrics["oi_change_1"] = metrics["open_interest"].pct_change()
    metrics["oi_change_3"] = metrics["open_interest"].pct_change(3)
    metrics["oi_change_12"] = metrics["open_interest"].pct_change(12)
    metrics["top_position_change_3"] = metrics["top_positions_long_short"].pct_change(3)
    metrics["global_change_3"] = metrics["global_long_short"].pct_change(3)
    funding = pd.DataFrame(raw["funding"])
    funding["ts"] = pd.to_datetime(funding["ts"], unit="ms")
    funding = funding.sort_values("ts").set_index("ts")
    return raw["metadata"], metrics, funding


def _event_features(event, metrics, funding):
    # signal_ts là open của entry bar; chỉ dùng snapshot của bar 5m liền trước.
    ts = pd.Timestamp(event["signal_ts"]) - pd.Timedelta(minutes=5)
    if ts not in metrics.index:
        return None
    m = metrics.loc[ts]
    prior_funding = funding.loc[:ts]
    if prior_funding.empty:
        return None
    f = prior_funding.iloc[-1]
    return {
        "funding": float(f.funding_rate),
        "oi1": float(m.oi_change_1), "oi3": float(m.oi_change_3), "oi12": float(m.oi_change_12),
        "top_accounts": float(m.top_accounts_long_short),
        "top_positions": float(m.top_positions_long_short),
        "global_ratio": float(m.global_long_short),
        "taker_ratio": float(m.taker_long_short),
        "top_position_change_3": float(m.top_position_change_3),
        "global_change_3": float(m.global_change_3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--artifacts", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    metadata, metrics, funding = _load(Path(args.context))
    gates = {
        "negative_funding": lambda x: x["funding"] < 0,
        "positive_funding": lambda x: x["funding"] > 0,
        "low_funding": lambda x: x["funding"] <= -0.00005,
        "oi_rising_1": lambda x: x["oi1"] > 0,
        "oi_rising_3": lambda x: x["oi3"] > 0,
        "oi_falling_3_absorption": lambda x: x["oi3"] < 0,
        "oi_rising_12": lambda x: x["oi12"] > 0,
        "taker_buy": lambda x: x["taker_ratio"] > 1,
        "taker_sell_absorption": lambda x: x["taker_ratio"] < 1,
        "global_crowded_short": lambda x: x["global_ratio"] < 1,
        "top_positions_short": lambda x: x["top_positions"] < 1,
        "top_accumulating_long": lambda x: x["top_position_change_3"] > 0,
        "retail_delever_top_accumulate": lambda x: x["global_change_3"] < 0 and x["top_position_change_3"] > 0,
        "negative_funding_oi_rising": lambda x: x["funding"] < 0 and x["oi3"] > 0,
        "negative_funding_taker_buy": lambda x: x["funding"] < 0 and x["taker_ratio"] > 1,
        "oi_rising_taker_buy": lambda x: x["oi3"] > 0 and x["taker_ratio"] > 1,
        "sell_absorption_oi_falling": lambda x: x["taker_ratio"] < 1 and x["oi3"] < 0,
    }
    results, coverage = {}, {}
    for artifact_path in args.artifacts:
        artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        dataset_end = pd.Timestamp(artifact["dataset"]["end"])
        validation_end = dataset_end - pd.Timedelta(days=30)
        train_end = validation_end - pd.Timedelta(days=30)
        for variant, events in artifact["events"].items():
            key = f"{Path(artifact_path).stem}:{variant}"
            attached = [(event, _event_features(event, metrics, funding)) for event in events]
            attached = [(event, values) for event, values in attached if values is not None]
            coverage[key] = {"source_events": len(events), "matched_events": len(attached)}
            results[key] = {}
            for gate, predicate in gates.items():
                selected = [event for event, values in attached if predicate(values)]
                split = _segments(selected, train_end, validation_end)
                results[key][gate] = {name: _metrics(rows) for name, rows in split.items()}
    passes = []
    for event, variants in results.items():
        for gate, segments in variants.items():
            values = [segments[name] for name in ("train", "validation", "test")]
            if all(m["n"] >= 20 and m["mean_net_return_pct"] is not None and m["mean_net_return_pct"] > 0 and (m["profit_factor"] is None or m["profit_factor"] > 1) for m in values):
                passes.append({"event": event, "gate": gate, "metrics": segments})
    output = {"contract": {"alignment": "last closed 5m metrics; last published funding", "minimum_events_per_segment": 20}, "metadata": metadata, "coverage": coverage, "passes": passes, "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"metric_bars": len(metrics), "funding_rows": len(funding), "passes": passes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
