"""Walk-forward test entry 5m với TP/SL/horizon rộng hơn noise ngắn hạn."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import _load, _path_arrays  # noqa: E402
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


TPS = (1.0, 1.5, 2.0, 3.0, 5.0)
SLS = (0.75, 1.0, 1.5, 2.0, 3.0)
HORIZONS = (1440, 4320, 10080)


def _rising_edge(mask):
    mask = mask.fillna(False)
    return mask & ~mask.shift(1).fillna(False)


def _signals(df):
    prior = {n: df["high"].shift(1).rolling(n).max() for n in (12, 36, 72, 288)}
    prior_low = {n: df["low"].shift(1).rolling(n).min() for n in (12, 36, 72, 288)}
    bullish = df["close"] > df["open"]
    bearish = df["close"] < df["open"]
    volume = df["volume"] >= 1.2 * df["vol_sma20"]
    aligned = (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema200"])
    aligned_down = (df["ema20"] < df["ema50"]) & (df["ema50"] < df["ema200"])
    masks = {}
    for bars in (12, 36, 72, 288):
        breakout = df["close"] > prior[bars] + 0.05 * df["atr"]
        masks[f"donchian_{bars}"] = _rising_edge(breakout)
        masks[f"donchian_{bars}_trend"] = _rising_edge(breakout & aligned)
        masks[f"donchian_{bars}_volume"] = _rising_edge(breakout & volume)
        breakdown = df["close"] < prior_low[bars] - 0.05 * df["atr"]
        masks[f"short_donchian_{bars}"] = _rising_edge(breakdown)
        masks[f"short_donchian_{bars}_trend"] = _rising_edge(breakdown & aligned_down)
        masks[f"short_donchian_{bars}_volume"] = _rising_edge(breakdown & volume)
    masks["momentum_1h_trend"] = _rising_edge((df["close"].pct_change(12) >= 0.01) & aligned & bullish)
    masks["momentum_4h_trend"] = _rising_edge((df["close"].pct_change(48) >= 0.02) & aligned & bullish)
    masks["pullback_resume_trend"] = _rising_edge(aligned & (df["low"] <= df["ema20"]) & (df["close"] > df["ema20"]) & bullish)
    masks["short_momentum_1h_trend"] = _rising_edge((df["close"].pct_change(12) <= -0.01) & aligned_down & bearish)
    masks["short_momentum_4h_trend"] = _rising_edge((df["close"].pct_change(48) <= -0.02) & aligned_down & bearish)
    masks["short_pullback_resume_trend"] = _rising_edge(aligned_down & (df["high"] >= df["ema20"]) & (df["close"] < df["ema20"]) & bearish)
    close_mean96 = df["close"].rolling(96).mean()
    close_std96 = df["close"].rolling(96).std().replace(0, np.nan)
    z96 = (df["close"] - close_mean96) / close_std96
    lower_wick_atr = (df[["open", "close"]].min(axis=1) - df["low"]) / df["atr"].replace(0, np.nan)
    upper_wick_atr = (df["high"] - df[["open", "close"]].max(axis=1)) / df["atr"].replace(0, np.nan)
    masks["rsi20_reclaim"] = (df["rsi"].shift(1) < 20) & (df["rsi"] >= 20) & bullish
    masks["rsi25_reclaim"] = (df["rsi"].shift(1) < 25) & (df["rsi"] >= 25) & bullish
    masks["z96_minus2_reclaim"] = (z96.shift(1) < -2) & (z96 >= -2) & bullish
    masks["lower_band_reclaim"] = (df["close"].shift(1) < (close_mean96 - 2 * close_std96).shift(1)) & (df["close"] >= close_mean96 - 2 * close_std96) & bullish
    masks["capitulation_reversal"] = _rising_edge((df["volume"] >= 2 * df["vol_sma20"]) & (lower_wick_atr >= 0.5) & (df["rsi"] < 35) & bullish)
    masks["selloff_1h_reversal"] = _rising_edge((df["close"].pct_change(12) <= -0.02) & bullish & (df["close"] > df["close"].shift(1)))
    masks["short_rsi80_reject"] = (df["rsi"].shift(1) > 80) & (df["rsi"] <= 80) & bearish
    masks["short_rsi75_reject"] = (df["rsi"].shift(1) > 75) & (df["rsi"] <= 75) & bearish
    masks["short_z96_plus2_reject"] = (z96.shift(1) > 2) & (z96 <= 2) & bearish
    masks["short_upper_band_reject"] = (df["close"].shift(1) > (close_mean96 + 2 * close_std96).shift(1)) & (df["close"] <= close_mean96 + 2 * close_std96) & bearish
    masks["short_euphoria_reversal"] = _rising_edge((df["volume"] >= 2 * df["vol_sma20"]) & (upper_wick_atr >= 0.5) & (df["rsi"] > 65) & bearish)
    return masks


def _raw_events(df, mask, side):
    rows = []
    for i in np.flatnonzero(mask.to_numpy()):
        if i < 300 or i + 1 >= len(df):
            continue
        entry = df.iloc[i + 1]
        rows.append({"signal_idx": int(i), "entry_ts": pd.Timestamp(entry.ts), "entry_price": float(entry.open), "side": side})
    return rows


def _outcome(event, path_ts, path_price, tp_pct, sl_pct, horizon, cost_pct):
    entry_ts, entry = event["entry_ts"], event["entry_price"]
    start = int(np.searchsorted(path_ts, np.datetime64(entry_ts), side="left"))
    end_time = entry_ts + pd.Timedelta(minutes=horizon)
    if not len(path_ts) or path_ts[-1] < np.datetime64(end_time):
        return None
    end = int(np.searchsorted(path_ts, np.datetime64(end_time), side="left"))
    prices = path_price[start:end]
    if event["side"] == "LONG":
        stop, take = entry * (1 - sl_pct / 100), entry * (1 + tp_pct / 100)
        stop_hits, take_hits = np.flatnonzero(prices <= stop), np.flatnonzero(prices >= take)
    else:
        stop, take = entry * (1 + sl_pct / 100), entry * (1 - tp_pct / 100)
        stop_hits, take_hits = np.flatnonzero(prices >= stop), np.flatnonzero(prices <= take)
    stop_i = int(stop_hits[0]) if len(stop_hits) else len(prices)
    take_i = int(take_hits[0]) if len(take_hits) else len(prices)
    if stop_i < take_i:
        exit_i, reason, gross = stop_i, "STOP_LOSS", -sl_pct
    elif take_i < len(prices):
        exit_i, reason, gross = take_i, "TAKE_PROFIT", tp_pct
    else:
        exit_i, reason = len(prices) - 1, "TIMEOUT"
        direction = 1 if event["side"] == "LONG" else -1
        gross = direction * (float(prices[-1]) / entry - 1) * 100
    return {"entry_ts": str(entry_ts), "exit_ts": str(pd.Timestamp(path_ts[start + exit_i])), "exit_reason": reason, "net_return_pct": gross - cost_pct}


def _metrics(trades):
    if not trades:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None}
    values = np.asarray([x["net_return_pct"] for x in trades])
    positive, negative = values[values > 0].sum(), abs(values[values < 0].sum())
    return {
        "n": len(trades),
        "win_rate_pct": round(float((values > 0).mean() * 100), 4),
        "mean_net_return_pct": round(float(values.mean()), 6),
        "sum_net_return_pct": round(float(values.sum()), 6),
        "profit_factor": round(float(positive / negative), 6) if negative else None,
        "stop_losses": sum(x["exit_reason"] == "STOP_LOSS" for x in trades),
        "take_profits": sum(x["exit_reason"] == "TAKE_PROFIT" for x in trades),
        "timeouts": sum(x["exit_reason"] == "TIMEOUT" for x in trades),
    }


def _run_segment(events, start_ts, end_ts, path_ts, path_price, tp, sl, horizon, cost):
    trades, available_at = [], None
    for event in events:
        ts = event["entry_ts"]
        if ts < start_ts or ts >= end_ts - pd.Timedelta(minutes=horizon):
            continue
        if available_at is not None and ts < available_at:
            continue
        outcome = _outcome(event, path_ts, path_price, tp, sl, horizon, cost)
        if outcome:
            trades.append(outcome)
            available_at = pd.Timestamp(outcome["exit_ts"])
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = _load(Path(args.dataset_cache))
    primary = technical.add_indicators(technical.to_dataframe(raw["primary"]))
    tick = technical.to_dataframe(raw["tick"])
    path_ts, path_price = _path_arrays(tick)
    cost = risk.round_trip_cost_pct()
    dataset_start, dataset_end = pd.Timestamp(primary.ts.iloc[0]), pd.Timestamp(primary.ts.iloc[-1])
    validation_start, test_start = dataset_end - pd.Timedelta(days=60), dataset_end - pd.Timedelta(days=30)
    segments = {"train": (dataset_start, validation_start), "validation": (validation_start, test_start), "test": (test_start, dataset_end)}

    results, selected = {}, {}
    for name, mask in _signals(primary).items():
        side = "SHORT" if name.startswith("short_") else "LONG"
        events = _raw_events(primary, mask, side)
        grid = []
        for tp in TPS:
            for sl in SLS:
                for horizon in HORIZONS:
                    metrics = {}
                    for segment, bounds in segments.items():
                        trades = _run_segment(events, *bounds, path_ts, path_price, tp, sl, horizon, cost)
                        metrics[segment] = _metrics(trades)
                    grid.append({"tp_pct": tp, "sl_pct": sl, "horizon_minutes": horizon, "metrics": metrics})
        eligible = [x for x in grid if x["metrics"]["train"]["n"] >= 30]
        best = max(eligible, key=lambda x: (x["metrics"]["train"]["mean_net_return_pct"], x["metrics"]["train"]["profit_factor"] or 0)) if eligible else None
        results[name] = {"raw_events": len(events), "train_selected": best, "grid": grid}
        if best:
            selected[name] = {k: best[k] for k in ("tp_pct", "sl_pct", "horizon_minutes", "metrics")}

    passes = []
    for name, item in selected.items():
        values = [item["metrics"][key] for key in ("train", "validation", "test")]
        if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in values):
            passes.append({"entry": name, **item})
    output = {
        "contract": {"selection": "per-entry TP/SL/horizon selected on train only; validation/test untouched", "timeframe": "5m", "sides": ["LONG", "SHORT"], "short_definition": "close breaks below prior rolling low by 0.05 ATR", "round_trip_cost_pct": cost, "single_concurrent_position": True, "minimum_train_trades": 30, "promotion_minimum_each_segment": 20},
        "dataset": {"start": str(dataset_start), "validation_start": str(validation_start), "test_start": str(test_start), "end": str(dataset_end)},
        "passes": passes, "selected": selected, "results": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
