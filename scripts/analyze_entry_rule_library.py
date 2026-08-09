"""So sánh thư viện trigger Long bằng cùng fixed-barrier outcome."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import (  # noqa: E402
    HORIZON_MINUTES, _attach_context, _load, _metrics, _path_arrays,
    _segments, _trade_outcome,
)
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


TP_PCT = 0.75
SL_PCT = 0.50


def _signals(df):
    prev = df.shift(1)
    bullish = df["close"] > df["open"]
    prior_high20 = df["high"].shift(1).rolling(20).max()
    atr_pct = df["atr"] / df["close"] * 100
    atr_q25 = atr_pct.shift(1).rolling(288).quantile(0.25)
    lower_wick = (df[["open", "close"]].min(axis=1) - df["low"]) / df["atr"].replace(0, np.nan)
    prev_bear = prev["close"] < prev["open"]
    engulf = bullish & prev_bear & (df["close"] >= prev["open"]) & (df["open"] <= prev["close"])
    return {
        "rsi30_reclaim": (prev["rsi"] < 30) & (df["rsi"] >= 30) & bullish,
        "rsi40_reclaim": (prev["rsi"] < 40) & (df["rsi"] >= 40) & bullish,
        "ema20_cross50": (prev["ema20"] <= prev["ema50"]) & (df["ema20"] > df["ema50"]),
        "macd_bull_cross": (prev["macd"] <= prev["macd_signal"]) & (df["macd"] > df["macd_signal"]) & bullish,
        "supertrend_flip": (prev["supertrend_dir"] < 0) & (df["supertrend_dir"] > 0),
        "vwap_reclaim": (prev["close"] <= prev["vwap"]) & (df["close"] > df["vwap"]) & bullish,
        "bullish_engulfing": engulf,
        "volume_wick_reversal": bullish & (df["volume"] >= 2 * df["vol_sma20"]) & (lower_wick >= 0.5),
        "squeeze_breakout": (atr_pct <= atr_q25) & (df["close"] > prior_high20 + 0.1 * df["atr"]) & (df["volume"] >= 1.2 * df["vol_sma20"]),
        "trend_pullback_reclaim": (df["ema20"] > df["ema50"]) & (df["low"] <= df["ema20"]) & (df["close"] > df["ema20"]) & bullish,
        "three_bar_reversal": (df["close"].shift(2) < df["open"].shift(2)) & prev_bear & bullish & (df["close"] > prev["high"]),
    }


def _build_events(df, masks, path_ts, path_price, cost_pct):
    output = {}
    for name, mask in masks.items():
        rows = []
        for i in np.flatnonzero(mask.fillna(False).to_numpy()):
            if i < 210 or i + 1 >= len(df):
                continue
            signal, entry = df.iloc[i], df.iloc[i + 1]
            entry_time, entry_price = pd.Timestamp(entry["ts"]), float(entry["open"])
            stop_price, tp_price = entry_price * (1 - SL_PCT / 100), entry_price * (1 + TP_PCT / 100)
            outcome = _trade_outcome(path_ts, path_price, entry_time, entry_price, stop_price, tp_price, cost_pct)
            if outcome is None:
                continue
            reason, net_return = outcome
            atr = float(signal["atr"])
            rows.append({
                "variant": name, "signal_idx": int(i),
                "signal_ts": str(pd.Timestamp(signal["ts"]) + pd.Timedelta(minutes=5)),
                "entry_ts": str(entry_time), "entry_price": entry_price,
                "risk_pct": SL_PCT, "tp_pct": TP_PCT,
                "exit_reason": reason, "net_return_pct": float(net_return),
                "features": {
                    "adx": float(signal["adx"]), "rsi": float(signal["rsi"]),
                    "ema20_50_atr": float((signal["ema20"] - signal["ema50"]) / atr),
                    "ema50_200_atr": float((signal["ema50"] - signal["ema200"]) / atr),
                    "volume_ratio": float(signal["volume"] / signal["vol_sma20"]),
                },
            })
        output[name] = rows
    return output


def _context_variants(rows, train_end, validation_end):
    gates = {
        "base": lambda f: True,
        "h1_up": lambda f: f["h1_ema20_50_atr"] > 0,
        "h4_up": lambda f: f["h4_ema20_50_atr"] > 0,
        "h1_h4_up": lambda f: f["h1_ema20_50_atr"] > 0 and f["h4_ema20_50_atr"] > 0,
        "h1_full_h4_up": lambda f: f["h1_ema20_50_atr"] > 0 and f["h1_ema50_200_atr"] > 0 and f["h4_ema20_50_atr"] > 0,
    }
    result = {}
    for gate_name, predicate in gates.items():
        selected = [row for row in rows if predicate(row["features"])]
        segments = _segments(selected, train_end, validation_end)
        result[gate_name] = {name: _metrics(values) for name, values in segments.items()}
    return result


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
    events = _build_events(primary, _signals(primary), path_ts, path_price, cost_pct)
    _attach_context(events, primary)
    dataset_end = pd.Timestamp(primary["ts"].iloc[-1])
    validation_end = dataset_end - pd.Timedelta(days=30)
    train_end = validation_end - pd.Timedelta(days=30)
    results = {name: _context_variants(rows, train_end, validation_end) for name, rows in events.items()}
    output = {
        "contract": {"timeframe": "5m", "tp_pct": TP_PCT, "sl_pct": SL_PCT, "cost_pct": cost_pct, "horizon_minutes": HORIZON_MINUTES, "train_end_exclusive": str(train_end), "validation_end_exclusive": str(validation_end)},
        "dataset": {"primary_bars": len(primary), "tick_bars": len(tick), "start": str(primary["ts"].iloc[0]), "end": str(primary["ts"].iloc[-1])},
        "results": results, "events": events,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": output["dataset"], "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
