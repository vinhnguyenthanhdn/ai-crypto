"""Causal study: confirmed swing low → bullish CHOCH/BOS → Long entry."""
import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.tree import DecisionTreeClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import WARMUP_BARS  # noqa: E402
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


SWING_WINDOW = 3
BREAKOUT_BUFFER_ATR = 0.10
BREAKOUT_WINDOW_BARS = 48
RETEST_WINDOW_BARS = 48
RETEST_TOUCH_ATR = 0.15
RETEST_CONFIRM_ATR = 0.05
SL_BUFFER_ATR = 0.20
MIN_TP_PCT = 0.75
MIN_RR = 1.5
HORIZON_MINUTES = 1440
COOLDOWN_MINUTES = 60
MODEL_FEATURES = (
    "bars_low_to_breakout", "breakout_displacement_atr", "breakout_body_atr",
    "volume_ratio", "adx", "rsi", "ema20_50_atr", "ema50_200_atr",
    "bottom_lower_wick_atr", "risk_pct",
    "h1_ema20_50_atr", "h1_ema50_200_atr", "h1_adx", "h1_rsi",
    "h4_ema20_50_atr", "h4_ema50_200_atr", "h4_adx", "h4_rsi",
)


def _load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _path_arrays(tick):
    times, prices = [], []
    step = pd.Timedelta(seconds=15)
    for row in tick.itertuples():
        for offset, field in enumerate(("open", "low", "high", "close")):
            times.append(row.ts + step * offset)
            prices.append(float(getattr(row, field)))
    return np.asarray(times, dtype="datetime64[ns]"), np.asarray(prices, dtype=float)


def _swing_mask(series, kind):
    mask = pd.Series(True, index=series.index)
    for offset in range(1, SWING_WINDOW + 1):
        if kind == "low":
            mask &= (series < series.shift(offset)) & (series < series.shift(-offset))
        else:
            mask &= (series > series.shift(offset)) & (series > series.shift(-offset))
    return mask.fillna(False)


def _trade_outcome(path_ts, path_price, entry_time, entry_price, stop_price, tp_price, cost_pct):
    start = int(np.searchsorted(path_ts, np.datetime64(entry_time), side="left"))
    end_time = np.datetime64(entry_time + pd.Timedelta(minutes=HORIZON_MINUTES))
    if not len(path_ts) or path_ts[-1] < end_time:
        return None
    end = int(np.searchsorted(path_ts, end_time, side="left"))
    window = path_price[start:end]
    for price in window:
        if price <= stop_price:
            gross = (stop_price / entry_price - 1) * 100
            return "STOP_LOSS", gross - cost_pct
        if price >= tp_price:
            gross = (tp_price / entry_price - 1) * 100
            return "TAKE_PROFIT", gross - cost_pct
    gross = (float(window[-1]) / entry_price - 1) * 100
    return "TIMEOUT", gross - cost_pct


def _event(df, bottom_idx, high_idx, signal_idx, variant, path_ts, path_price, cost_pct, parent=None):
    bottom, high, signal = df.iloc[bottom_idx], df.iloc[high_idx], df.iloc[signal_idx]
    entry_idx = signal_idx + 1
    if entry_idx >= len(df):
        return None
    entry = df.iloc[entry_idx]
    entry_time, entry_price = pd.Timestamp(entry["ts"]), float(entry["open"])
    stop_price = float(bottom["low"] - SL_BUFFER_ATR * bottom["atr"])
    risk_pct = (entry_price - stop_price) / entry_price * 100
    if risk_pct <= 0:
        return None
    tp_pct = max(MIN_TP_PCT, MIN_RR * risk_pct)
    tp_price = entry_price * (1 + tp_pct / 100)
    outcome = _trade_outcome(
        path_ts, path_price, entry_time, entry_price, stop_price, tp_price, cost_pct,
    )
    if outcome is None:
        return None
    reason, net_return = outcome
    atr = float(signal["atr"])
    volume_ratio = float(signal["volume"] / signal["vol_sma20"]) if signal["vol_sma20"] else 0.0
    return {
        "variant": variant,
        "bottom_idx": bottom_idx,
        "high_idx": high_idx,
        "signal_idx": signal_idx,
        "bottom_ts": str(bottom["ts"]),
        "structure_high_ts": str(high["ts"]),
        "signal_ts": str(pd.Timestamp(signal["ts"]) + pd.Timedelta(minutes=5)),
        "entry_ts": str(entry_time),
        "entry_price": entry_price,
        "bottom_low": float(bottom["low"]),
        "structure_high": float(high["high"]),
        "stop_price": stop_price,
        "tp_price": tp_price,
        "risk_pct": risk_pct,
        "tp_pct": tp_pct,
        "exit_reason": reason,
        "net_return_pct": float(net_return),
        "features": {
            "bars_low_to_breakout": signal_idx - bottom_idx,
            "breakout_displacement_atr": float((signal["close"] - high["high"]) / atr),
            "breakout_body_atr": float((signal["close"] - signal["open"]) / atr),
            "volume_ratio": volume_ratio,
            "adx": float(signal["adx"]),
            "rsi": float(signal["rsi"]),
            "ema20_50_atr": float((signal["ema20"] - signal["ema50"]) / atr),
            "ema50_200_atr": float((signal["ema50"] - signal["ema200"]) / atr),
            "bottom_lower_wick_atr": float((min(bottom["open"], bottom["close"]) - bottom["low"]) / bottom["atr"]),
            "risk_pct": risk_pct,
            "parent_breakout_idx": parent["signal_idx"] if parent else None,
        },
    }


def _discover(df, path_ts, path_price, cost_pct):
    low_indices = np.flatnonzero(_swing_mask(df["low"], "low").to_numpy())
    high_indices = np.flatnonzero(_swing_mask(df["high"], "high").to_numpy())
    events = {name: {} for name in (
        "choch_direct", "choch_direct_volume", "choch_retest", "choch_retest_volume",
    )}
    for bottom_idx in low_indices:
        confirmation_idx = bottom_idx + SWING_WINDOW
        if bottom_idx < WARMUP_BARS or confirmation_idx >= len(df) - 1:
            continue
        eligible_highs = high_indices[(high_indices < bottom_idx) & (high_indices + SWING_WINDOW <= confirmation_idx)]
        if not len(eligible_highs):
            continue
        high_idx = int(eligible_highs[-1])
        level = float(df.iloc[high_idx]["high"])
        breakout = None
        for j in range(confirmation_idx, min(confirmation_idx + BREAKOUT_WINDOW_BARS + 1, len(df) - 1)):
            row = df.iloc[j]
            if row["close"] > level + BREAKOUT_BUFFER_ATR * row["atr"]:
                breakout = _event(
                    df, int(bottom_idx), high_idx, j, "choch_direct",
                    path_ts, path_price, cost_pct,
                )
                break
        if not breakout:
            continue
        # Nếu nhiều bottom cùng dẫn tới một breakout, runtime dùng bottom mới nhất.
        key = breakout["signal_idx"]
        if key not in events["choch_direct"] or bottom_idx > events["choch_direct"][key]["bottom_idx"]:
            events["choch_direct"][key] = breakout
        breakout_row = df.iloc[breakout["signal_idx"]]
        volume_confirmed = bool(
            breakout_row["close"] > breakout_row["open"]
            and breakout["features"]["volume_ratio"] >= 1.20
        )
        if volume_confirmed:
            volume_event = {**breakout, "variant": "choch_direct_volume"}
            events["choch_direct_volume"][key] = volume_event

        for j in range(breakout["signal_idx"] + 1, min(breakout["signal_idx"] + RETEST_WINDOW_BARS + 1, len(df) - 1)):
            row = df.iloc[j]
            if row["close"] < level - RETEST_TOUCH_ATR * row["atr"]:
                break
            touched = row["low"] <= level + RETEST_TOUCH_ATR * row["atr"]
            reclaimed = row["close"] >= level + RETEST_CONFIRM_ATR * row["atr"] and row["close"] > row["open"]
            if touched and reclaimed:
                retest = _event(
                    df, int(bottom_idx), high_idx, j, "choch_retest",
                    path_ts, path_price, cost_pct, parent=breakout,
                )
                if retest:
                    retest_key = retest["signal_idx"]
                    if retest_key not in events["choch_retest"] or bottom_idx > events["choch_retest"][retest_key]["bottom_idx"]:
                        events["choch_retest"][retest_key] = retest
                    if volume_confirmed:
                        events["choch_retest_volume"][retest_key] = {**retest, "variant": "choch_retest_volume"}
                break
    return {name: sorted(rows.values(), key=lambda row: row["entry_ts"]) for name, rows in events.items()}


def _context_frame(primary, timeframe):
    indexed = primary.set_index("ts")
    context = indexed.resample(timeframe, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna().reset_index()
    context = technical.add_indicators(context)
    context["available_ts"] = context["ts"] + pd.Timedelta(timeframe)
    return context


def _attach_context(events, primary):
    frames = {"h1": _context_frame(primary, "1h"), "h4": _context_frame(primary, "4h")}
    for rows in events.values():
        for event in rows:
            signal_time = pd.Timestamp(event["signal_ts"])
            for prefix, frame in frames.items():
                pos = int(np.searchsorted(frame["available_ts"].to_numpy(dtype="datetime64[ns]"), np.datetime64(signal_time), side="right") - 1)
                if pos < 0:
                    values = {"ema20_50_atr": 0.0, "ema50_200_atr": 0.0, "adx": 0.0, "rsi": 50.0}
                else:
                    row = frame.iloc[pos]
                    atr = float(row["atr"]) if not pd.isna(row["atr"]) and row["atr"] > 0 else 1.0
                    values = {
                        "ema20_50_atr": float((row["ema20"] - row["ema50"]) / atr) if not pd.isna(row["ema50"]) else 0.0,
                        "ema50_200_atr": float((row["ema50"] - row["ema200"]) / atr) if not pd.isna(row["ema200"]) else 0.0,
                        "adx": float(row["adx"]) if not pd.isna(row["adx"]) else 0.0,
                        "rsi": float(row["rsi"]) if not pd.isna(row["rsi"]) else 50.0,
                    }
                for name, value in values.items():
                    event["features"][f"{prefix}_{name}"] = value


def _context_filter_results(rows, train_end, validation_end):
    filters = {
        "h1_up": lambda f: f["h1_ema20_50_atr"] > 0,
        "h1_full_stack": lambda f: f["h1_ema20_50_atr"] > 0 and f["h1_ema50_200_atr"] > 0,
        "h4_up": lambda f: f["h4_ema20_50_atr"] > 0,
        "h1_h4_up": lambda f: f["h1_ema20_50_atr"] > 0 and f["h4_ema20_50_atr"] > 0,
        "h1_full_h4_up": lambda f: f["h1_ema20_50_atr"] > 0 and f["h1_ema50_200_atr"] > 0 and f["h4_ema20_50_atr"] > 0,
        "h1_up_adx20": lambda f: f["h1_ema20_50_atr"] > 0 and f["h1_adx"] >= 20,
    }
    output = {}
    for name, predicate in filters.items():
        segmented = _segments([row for row in rows if predicate(row["features"])], train_end, validation_end)
        output[name] = {segment: _metrics(values) for segment, values in segmented.items()}
    return output


def _cooldown(rows):
    selected, last = [], None
    for row in sorted(rows, key=lambda value: value["entry_ts"]):
        ts = pd.Timestamp(row["entry_ts"])
        if last is None or ts - last >= pd.Timedelta(minutes=COOLDOWN_MINUTES):
            selected.append(row)
            last = ts
    return selected


def _metrics(rows):
    rows = _cooldown(rows)
    if not rows:
        return {"n": 0, "wins": 0, "win_rate_pct": None, "mean_net_return_pct": None, "profit_factor": None}
    returns = np.asarray([row["net_return_pct"] for row in rows])
    wins = sum(row["exit_reason"] == "TAKE_PROFIT" for row in rows)
    positive, negative = returns[returns > 0].sum(), abs(returns[returns < 0].sum())
    return {
        "n": len(rows), "wins": wins,
        "stop_losses": sum(row["exit_reason"] == "STOP_LOSS" for row in rows),
        "timeouts": sum(row["exit_reason"] == "TIMEOUT" for row in rows),
        "win_rate_pct": round(wins / len(rows) * 100, 4),
        "mean_net_return_pct": round(float(returns.mean()), 6),
        "sum_net_return_pct": round(float(returns.sum()), 6),
        "profit_factor": round(float(positive / negative), 6) if negative else None,
        "median_risk_pct": round(float(np.median([row["risk_pct"] for row in rows])), 6),
        "median_tp_pct": round(float(np.median([row["tp_pct"] for row in rows])), 6),
    }


def _segments(rows, train_end, validation_end):
    purge = pd.Timedelta(minutes=HORIZON_MINUTES)
    return {
        "train": [row for row in rows if pd.Timestamp(row["entry_ts"]) < train_end - purge],
        "validation": [row for row in rows if train_end <= pd.Timestamp(row["entry_ts"]) < validation_end - purge],
        "test": [row for row in rows if pd.Timestamp(row["entry_ts"]) >= validation_end],
    }


def _frame(rows):
    return pd.DataFrame([
        {**row["features"], "row": row, "win": row["exit_reason"] == "TAKE_PROFIT"}
        for row in rows
    ])


def _tree_paths(model):
    tree, paths = model.tree_, {}
    def visit(node, clauses):
        if tree.children_left[node] == tree.children_right[node]:
            paths[int(node)] = clauses
            return
        feature = MODEL_FEATURES[tree.feature[node]]
        threshold = float(tree.threshold[node])
        visit(tree.children_left[node], clauses + [f"{feature} <= {threshold:.6g}"])
        visit(tree.children_right[node], clauses + [f"{feature} > {threshold:.6g}"])
    visit(0, [])
    return paths


def _learn_filters(rows, train_end, validation_end):
    segmented_rows = _segments(rows, train_end, validation_end)
    frames = {name: _frame(values) for name, values in segmented_rows.items()}
    if any(frame.empty for frame in frames.values()):
        return {}
    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(frames["train"][list(MODEL_FEATURES)])
    y_train = frames["train"]["win"].astype(int)
    results = {}

    tree = DecisionTreeClassifier(
        max_depth=3, min_samples_leaf=30, class_weight="balanced", random_state=20260807,
    ).fit(x_train, y_train)
    paths = _tree_paths(tree)
    tree_leaves = {
        name: tree.apply(imputer.transform(frame[list(MODEL_FEATURES)]))
        for name, frame in frames.items()
    }
    eligible = []
    for leaf in sorted(set(tree_leaves["train"])):
        chosen = [row for row, leaf_id in zip(segmented_rows["train"], tree_leaves["train"]) if leaf_id == leaf]
        metric = _metrics(chosen)
        if (
            metric["n"] >= 30 and metric["mean_net_return_pct"] > 0
            and (metric["profit_factor"] is None or metric["profit_factor"] > 1)
        ):
            eligible.append(int(leaf))
    results["tree"] = {
        "selected_rules": [paths[leaf] for leaf in eligible],
        "metrics": {
            name: _metrics([row for row, leaf in zip(segmented_rows[name], tree_leaves[name]) if int(leaf) in eligible])
            for name in frames
        },
    }

    forest = RandomForestClassifier(
        n_estimators=300, max_depth=6, min_samples_leaf=20, max_features="sqrt",
        class_weight="balanced_subsample", random_state=20260807, n_jobs=-1,
    ).fit(x_train, y_train)
    probabilities = {
        name: forest.predict_proba(imputer.transform(frame[list(MODEL_FEATURES)]))[:, 1]
        for name, frame in frames.items()
    }
    candidates = []
    for top_pct in (20, 10, 5, 2):
        threshold = float(np.quantile(probabilities["train"], 1 - top_pct / 100))
        chosen = [row for row, probability in zip(segmented_rows["train"], probabilities["train"]) if probability >= threshold]
        metric = _metrics(chosen)
        if (
            metric["n"] >= 30 and metric["mean_net_return_pct"] > 0
            and (metric["profit_factor"] is None or metric["profit_factor"] > 1)
        ):
            candidates.append((metric["mean_net_return_pct"], top_pct, threshold))
    selected = max(candidates, default=None)
    if selected:
        _, top_pct, threshold = selected
        forest_metrics = {
            name: _metrics([row for row, probability in zip(segmented_rows[name], probabilities[name]) if probability >= threshold])
            for name in frames
        }
    else:
        top_pct, threshold = None, None
        forest_metrics = {name: _metrics([]) for name in frames}
    results["random_forest"] = {
        "selected_top_train_pct": top_pct,
        "probability_threshold": threshold,
        "feature_importance": sorted(
            ({"feature": feature, "importance": round(float(value), 6)} for feature, value in zip(MODEL_FEATURES, forest.feature_importances_)),
            key=lambda row: row["importance"], reverse=True,
        ),
        "metrics": forest_metrics,
    }
    return results


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
    summaries = {}
    learned_filters = {}
    context_filters = {}
    for name, rows in events.items():
        segmented = _segments(rows, train_end, validation_end)
        summaries[name] = {
            "all": _metrics(rows),
            **{segment: _metrics(segment_rows) for segment, segment_rows in segmented.items()},
        }
        learned_filters[name] = _learn_filters(rows, train_end, validation_end)
        context_filters[name] = _context_filter_results(rows, train_end, validation_end)
    output = {
        "contract": {
            "timeframe": "5m closed candles", "swing_window_bars": SWING_WINDOW,
            "breakout": f"close > prior minor swing high + {BREAKOUT_BUFFER_ATR} ATR within {BREAKOUT_WINDOW_BARS * 5}m",
            "retest": f"touch within {RETEST_TOUCH_ATR} ATR then bullish close >= level + {RETEST_CONFIRM_ATR} ATR within {RETEST_WINDOW_BARS * 5}m",
            "sl": f"confirmed swing low - {SL_BUFFER_ATR} ATR_at_low",
            "tp": f"max({MIN_TP_PCT}%, {MIN_RR} x risk)",
            "cost_pct": cost_pct, "horizon_minutes": HORIZON_MINUTES,
            "fill": "next 5m open", "tick_path": "1m open-low-high-close adverse-first",
            "train_end_exclusive": str(train_end), "validation_end_exclusive": str(validation_end),
        },
        "dataset": {
            "primary_bars": len(primary), "tick_bars": len(tick),
            "start": str(primary["ts"].iloc[0]), "end": str(primary["ts"].iloc[-1]),
        },
        "summaries": summaries,
        "learned_filters": learned_filters,
        "context_filters": context_filters,
        "events": events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": output["dataset"], "summaries": summaries, "context_filters": context_filters}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
