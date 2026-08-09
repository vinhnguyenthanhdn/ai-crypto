"""Causal Long study cho downside liquidity sweep / failed breakdown."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import (  # noqa: E402
    HORIZON_MINUTES, MIN_RR, MIN_TP_PCT, SL_BUFFER_ATR,
    _attach_context, _context_filter_results, _load, _metrics, _path_arrays,
    _segments, _trade_outcome,
)
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


LOOKBACKS = {"1h": 12, "4h": 48, "24h": 288}
SWEEP_ATR = 0.10
RECLAIM_ATR = 0.05
VOLUME_RATIO = 1.20


def _discover(df, path_ts, path_price, cost_pct):
    events = {}
    for label, lookback in LOOKBACKS.items():
        base, volume = [], []
        prior_low = df["low"].shift(1).rolling(lookback).min()
        for i in range(max(210, lookback), len(df) - 1):
            row = df.iloc[i]
            level = prior_low.iloc[i]
            atr = float(row["atr"])
            if pd.isna(level) or atr <= 0:
                continue
            swept = row["low"] < level - SWEEP_ATR * atr
            reclaimed = row["close"] >= level + RECLAIM_ATR * atr and row["close"] > row["open"]
            if not (swept and reclaimed):
                continue
            entry = df.iloc[i + 1]
            entry_time, entry_price = pd.Timestamp(entry["ts"]), float(entry["open"])
            stop_price = float(row["low"] - SL_BUFFER_ATR * atr)
            risk_pct = (entry_price - stop_price) / entry_price * 100
            if risk_pct <= 0:
                continue
            tp_pct = max(MIN_TP_PCT, MIN_RR * risk_pct)
            tp_price = entry_price * (1 + tp_pct / 100)
            outcome = _trade_outcome(path_ts, path_price, entry_time, entry_price, stop_price, tp_price, cost_pct)
            if outcome is None:
                continue
            reason, net_return = outcome
            volume_ratio = float(row["volume"] / row["vol_sma20"]) if row["vol_sma20"] else 0.0
            event = {
                "variant": f"sweep_{label}", "signal_idx": i,
                "signal_ts": str(pd.Timestamp(row["ts"]) + pd.Timedelta(minutes=5)),
                "entry_ts": str(entry_time), "entry_price": entry_price,
                "swept_level": float(level), "sweep_low": float(row["low"]),
                "stop_price": stop_price, "tp_price": tp_price,
                "risk_pct": risk_pct, "tp_pct": tp_pct,
                "exit_reason": reason, "net_return_pct": float(net_return),
                "features": {
                    "lookback_bars": lookback,
                    "sweep_depth_atr": float((level - row["low"]) / atr),
                    "reclaim_strength_atr": float((row["close"] - level) / atr),
                    "body_atr": float((row["close"] - row["open"]) / atr),
                    "lower_wick_atr": float((min(row["open"], row["close"]) - row["low"]) / atr),
                    "volume_ratio": volume_ratio, "risk_pct": risk_pct,
                    "adx": float(row["adx"]), "rsi": float(row["rsi"]),
                    "ema20_50_atr": float((row["ema20"] - row["ema50"]) / atr),
                    "ema50_200_atr": float((row["ema50"] - row["ema200"]) / atr),
                },
            }
            base.append(event)
            if volume_ratio >= VOLUME_RATIO:
                volume.append({**event, "variant": f"sweep_{label}_volume"})
        events[f"sweep_{label}"] = base
        events[f"sweep_{label}_volume"] = volume
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = _load(Path(args.dataset_cache))
    primary = technical.add_indicators(technical.to_dataframe(raw["primary"]))
    tick = technical.to_dataframe(raw["tick"])
    path_ts, path_price = _path_arrays(tick)
    cost_pct = risk.round_trip_cost_pct()
    events = _discover(primary, path_ts, path_price, cost_pct)
    _attach_context(events, primary)
    dataset_end = pd.Timestamp(primary["ts"].iloc[-1])
    validation_end = dataset_end - pd.Timedelta(days=30)
    train_end = validation_end - pd.Timedelta(days=30)
    summaries, context_filters = {}, {}
    for name, rows in events.items():
        segmented = _segments(rows, train_end, validation_end)
        summaries[name] = {"all": _metrics(rows), **{key: _metrics(value) for key, value in segmented.items()}}
        context_filters[name] = _context_filter_results(rows, train_end, validation_end)
    output = {
        "contract": {
            "timeframe": "5m closed", "lookbacks": LOOKBACKS,
            "sweep": f"low < prior rolling low - {SWEEP_ATR} ATR",
            "reclaim": f"bullish close >= prior rolling low + {RECLAIM_ATR} ATR",
            "sl": f"sweep low - {SL_BUFFER_ATR} ATR", "tp": f"max({MIN_TP_PCT}%, {MIN_RR} x risk)",
            "cost_pct": cost_pct, "horizon_minutes": HORIZON_MINUTES,
            "train_end_exclusive": str(train_end), "validation_end_exclusive": str(validation_end),
        },
        "dataset": {"primary_bars": len(primary), "tick_bars": len(tick), "start": str(primary["ts"].iloc[0]), "end": str(primary["ts"].iloc[-1])},
        "summaries": summaries, "context_filters": context_filters, "events": events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": output["dataset"], "summaries": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
