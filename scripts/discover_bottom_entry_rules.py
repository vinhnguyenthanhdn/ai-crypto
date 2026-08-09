"""Suy luận entry rule từ các potential bottom trên nến 5m.

Future bars chỉ dùng để tạo label/outcome offline. Mọi feature và rule đều có
sẵn tại close của signal bar; fill giả định tại open nến 5m kế tiếp.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import WARMUP_BARS  # noqa: E402
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


HORIZON_MINUTES = 1440
BOTTOM_LEFT_BARS = 3
BOTTOM_RIGHT_BARS = 3
MIN_TP_PCT = 0.75
RISK_REWARD = 1.5
SL_BUFFER_ATR = 0.20
COOLDOWN_MINUTES = 60
MIN_TRAIN_LEAF_EVENTS = 30
CONFIRMATION_WINDOWS = (1, 2, 3, 5, 8)

FEATURE_GROUPS = {
    "reversal": [
        "rsi", "return_1", "return_3", "body_atr", "lower_wick_atr",
        "upper_wick_atr", "close_location", "volume_ratio",
        "vwap_distance_atr", "consecutive_down",
    ],
    "trend_momentum": [
        "ema20_50_atr", "ema50_200_atr", "ema20_slope_3_atr",
        "ema50_slope_12_atr", "return_3", "return_12", "return_48",
        "adx", "supertrend_dir", "vwap_distance_atr",
    ],
    "structure_volatility": [
        "new_low_depth_atr", "drawdown_high_12_atr", "drawdown_high_48_atr",
        "position_48", "atr_pct", "atr_ratio_288", "range_atr",
        "range_compression", "volume_ratio", "adx",
    ],
    "regime_normalized": [
        "atr_rank_30d", "adx_rank_30d", "ema20_above_50",
        "ema50_above_200", "return_3_atr", "return_12_atr",
        "return_48_atr", "new_low_depth_atr", "drawdown_high_12_atr",
        "position_48", "range_atr", "range_compression", "volume_ratio",
        "lower_wick_atr", "close_location", "rsi",
    ],
}
FEATURE_GROUPS["all"] = sorted({name for names in FEATURE_GROUPS.values() for name in names})
CONFIRMED_FEATURES = FEATURE_GROUPS["all"] + [
    "rebound_from_bottom_atr", "entry_risk_atr", "bottom_lower_wick_atr",
    "bottom_close_location", "bottom_volume_ratio", "bottom_rsi",
]


def _load(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _path_arrays(tick: pd.DataFrame):
    timestamps, prices = [], []
    step = pd.Timedelta(seconds=15)
    for row in tick.itertuples():
        # Adverse-first cho Long khi chỉ có OHLC 1m.
        for offset, field in enumerate(("open", "low", "high", "close")):
            timestamps.append(row.ts + step * offset)
            prices.append(float(getattr(row, field)))
    return np.asarray(timestamps, dtype="datetime64[ns]"), np.asarray(prices, dtype=float)


def _features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    atr = out["atr"].replace(0, np.nan)
    candle_range = (out["high"] - out["low"]).replace(0, np.nan)
    body_high = out[["open", "close"]].max(axis=1)
    body_low = out[["open", "close"]].min(axis=1)
    prior_low_3 = out["low"].shift(1).rolling(BOTTOM_LEFT_BARS).min()
    high_12 = out["high"].shift(1).rolling(12).max()
    high_48 = out["high"].shift(1).rolling(48).max()
    low_48 = out["low"].shift(1).rolling(48).min()
    range_48 = (high_48 - low_48).replace(0, np.nan)
    rolling_range = out["high"].rolling(12).max() - out["low"].rolling(12).min()
    long_range = (out["high"].rolling(48).max() - out["low"].rolling(48).min()).replace(0, np.nan)

    out["return_1"] = out["close"].pct_change(1) * 100
    out["return_3"] = out["close"].pct_change(3) * 100
    out["return_12"] = out["close"].pct_change(12) * 100
    out["return_48"] = out["close"].pct_change(48) * 100
    out["body_atr"] = (out["close"] - out["open"]) / atr
    out["lower_wick_atr"] = (body_low - out["low"]) / atr
    out["upper_wick_atr"] = (out["high"] - body_high) / atr
    out["close_location"] = (out["close"] - out["low"]) / candle_range
    out["volume_ratio"] = out["volume"] / out["vol_sma20"].replace(0, np.nan)
    out["vwap_distance_atr"] = (out["close"] - out["vwap"]) / atr
    out["ema20_50_atr"] = (out["ema20"] - out["ema50"]) / atr
    out["ema50_200_atr"] = (out["ema50"] - out["ema200"]) / atr
    out["ema20_slope_3_atr"] = (out["ema20"] - out["ema20"].shift(3)) / atr
    out["ema50_slope_12_atr"] = (out["ema50"] - out["ema50"].shift(12)) / atr
    out["new_low_depth_atr"] = (prior_low_3 - out["low"]) / atr
    out["drawdown_high_12_atr"] = (out["close"] - high_12) / atr
    out["drawdown_high_48_atr"] = (out["close"] - high_48) / atr
    out["position_48"] = (out["close"] - low_48) / range_48
    out["atr_pct"] = out["atr"] / out["close"] * 100
    out["atr_ratio_288"] = out["atr"] / out["atr"].rolling(288).median().replace(0, np.nan)
    out["range_atr"] = candle_range / atr
    out["range_compression"] = rolling_range / long_range
    rolling_window = 8640  # 30 ngày trên 5m
    out["atr_rank_30d"] = out["atr_pct"].rolling(rolling_window, min_periods=576).rank(pct=True)
    out["adx_rank_30d"] = out["adx"].rolling(rolling_window, min_periods=576).rank(pct=True)
    out["ema20_above_50"] = (out["ema20"] > out["ema50"]).astype(float)
    out["ema50_above_200"] = (out["ema50"] > out["ema200"]).astype(float)
    out["return_3_atr"] = (out["close"] - out["close"].shift(3)) / atr
    out["return_12_atr"] = (out["close"] - out["close"].shift(12)) / atr
    out["return_48_atr"] = (out["close"] - out["close"].shift(48)) / atr

    down = (out["close"] < out["open"]).astype(int)
    groups = (down == 0).cumsum()
    out["consecutive_down"] = down.groupby(groups).cumsum()
    out["potential_bottom"] = out["low"] <= prior_low_3
    future_low = pd.concat([out["low"].shift(-offset) for offset in range(1, BOTTOM_RIGHT_BARS + 1)], axis=1).min(axis=1)
    out["retrospective_local_bottom"] = out["low"] < future_low
    return out


def _outcome(path_ts, path_price, entry_time, entry_price, tp_pct, sl_pct, cost_pct):
    start = int(np.searchsorted(path_ts, np.datetime64(entry_time), side="left"))
    end_time = np.datetime64(entry_time + pd.Timedelta(minutes=HORIZON_MINUTES))
    if not len(path_ts) or path_ts[-1] < end_time:
        return None
    end = int(np.searchsorted(path_ts, end_time, side="left"))
    prices = path_price[start:end]
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    for price in prices:
        if price <= sl_price:
            return "STOP_LOSS", -sl_pct - cost_pct
        if price >= tp_price:
            return "TAKE_PROFIT", tp_pct - cost_pct
    gross = (float(prices[-1]) / entry_price - 1) * 100
    return "TIMEOUT", gross - cost_pct


def _build_samples(df, path_ts, path_price, cost_pct):
    rows = []
    feature_names = FEATURE_GROUPS["all"]
    for i in range(WARMUP_BARS, len(df) - 1):
        signal = df.iloc[i]
        if not bool(signal["potential_bottom"]) or pd.isna(signal["atr"]) or signal["atr"] <= 0:
            continue
        entry = df.iloc[i + 1]
        entry_time, entry_price = pd.Timestamp(entry["ts"]), float(entry["open"])
        stop_price = float(signal["low"] - SL_BUFFER_ATR * signal["atr"])
        sl_pct = (entry_price - stop_price) / entry_price * 100
        if sl_pct <= 0:
            continue
        tp_pct = max(MIN_TP_PCT, RISK_REWARD * sl_pct)
        outcome = _outcome(path_ts, path_price, entry_time, entry_price, tp_pct, sl_pct, cost_pct)
        if outcome is None:
            continue
        reason, net_return = outcome
        is_local = bool(signal["retrospective_local_bottom"])
        row = {
            "signal_idx": i,
            "signal_ts": str(pd.Timestamp(signal["ts"]) + pd.Timedelta(minutes=5)),
            "entry_ts": str(entry_time),
            "entry_price": entry_price,
            "signal_low": float(signal["low"]),
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "exit_reason": reason,
            "net_return_pct": float(net_return),
            "retrospective_local_bottom": is_local,
            "good_bottom": bool(is_local and reason == "TAKE_PROFIT"),
        }
        for name in feature_names:
            value = signal.get(name)
            row[name] = None if pd.isna(value) else float(value)
        rows.append(row)
    return pd.DataFrame(rows)


def _build_confirmed_bottom_samples(df, path_ts, path_price, cost_pct, right_bars):
    """Entry sau khi swing low đã được xác nhận causal bởi right-side bars."""
    rows = []
    prior_low = df["low"].shift(1).rolling(BOTTOM_LEFT_BARS).min()
    future_low = pd.concat(
        [df["low"].shift(-offset) for offset in range(1, right_bars + 1)], axis=1,
    ).min(axis=1)
    for bottom_idx in range(WARMUP_BARS, len(df) - right_bars - 1):
        bottom = df.iloc[bottom_idx]
        if not (bottom["low"] <= prior_low.iloc[bottom_idx] and bottom["low"] < future_low.iloc[bottom_idx]):
            continue
        confirmation_idx = bottom_idx + right_bars
        entry = df.iloc[confirmation_idx + 1]
        entry_time, entry_price = pd.Timestamp(entry["ts"]), float(entry["open"])
        stop_price = float(bottom["low"] - SL_BUFFER_ATR * bottom["atr"])
        sl_pct = (entry_price - stop_price) / entry_price * 100
        if sl_pct <= 0:
            continue
        tp_pct = max(MIN_TP_PCT, RISK_REWARD * sl_pct)
        outcome = _outcome(path_ts, path_price, entry_time, entry_price, tp_pct, sl_pct, cost_pct)
        if outcome is None:
            continue
        reason, net_return = outcome
        confirmation = df.iloc[confirmation_idx]
        row = {
            "bottom_ts": str(pd.Timestamp(bottom["ts"])),
            "signal_ts": str(pd.Timestamp(confirmation["ts"]) + pd.Timedelta(minutes=5)),
            "entry_ts": str(entry_time),
            "entry_price": entry_price,
            "signal_low": float(bottom["low"]),
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "exit_reason": reason,
            "net_return_pct": float(net_return),
            "good_bottom": reason == "TAKE_PROFIT",
            "retrospective_local_bottom": True,
            "rebound_from_bottom_atr": float((confirmation["close"] - bottom["low"]) / bottom["atr"]),
            "entry_risk_atr": float((entry_price - stop_price) / bottom["atr"]),
            "bottom_lower_wick_atr": float(bottom["lower_wick_atr"]),
            "bottom_close_location": float(bottom["close_location"]),
            "bottom_volume_ratio": float(bottom["volume_ratio"]),
            "bottom_rsi": float(bottom["rsi"]),
        }
        for name in FEATURE_GROUPS["all"]:
            value = confirmation.get(name)
            row[name] = None if pd.isna(value) else float(value)
        rows.append(row)
    return pd.DataFrame(rows)


def _cooldown(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    selected, last = [], None
    for idx, row in rows.sort_values("entry_ts").iterrows():
        ts = pd.Timestamp(row["entry_ts"])
        if last is None or ts - last >= pd.Timedelta(minutes=COOLDOWN_MINUTES):
            selected.append(idx)
            last = ts
    return rows.loc[selected].copy()


def _metrics(rows: pd.DataFrame):
    rows = _cooldown(rows)
    if rows.empty:
        return {"n": 0, "wins": 0, "win_rate_pct": None, "mean_net_return_pct": None, "profit_factor": None}
    returns = rows["net_return_pct"].astype(float)
    wins = int((rows["exit_reason"] == "TAKE_PROFIT").sum())
    positive = float(returns[returns > 0].sum())
    negative = abs(float(returns[returns < 0].sum()))
    return {
        "n": len(rows),
        "good_bottoms": int(rows["good_bottom"].sum()),
        "wins": wins,
        "stop_losses": int((rows["exit_reason"] == "STOP_LOSS").sum()),
        "timeouts": int((rows["exit_reason"] == "TIMEOUT").sum()),
        "win_rate_pct": round(wins / len(rows) * 100, 4),
        "mean_net_return_pct": round(float(returns.mean()), 6),
        "sum_net_return_pct": round(float(returns.sum()), 6),
        "profit_factor": round(positive / negative, 6) if negative else None,
    }


def _tree_paths(model, feature_names):
    tree = model.tree_
    paths = {}

    def visit(node, clauses):
        if tree.children_left[node] == tree.children_right[node]:
            paths[int(node)] = list(clauses)
            return
        name = feature_names[tree.feature[node]]
        threshold = float(tree.threshold[node])
        visit(tree.children_left[node], clauses + [f"{name} <= {threshold:.6g}"])
        visit(tree.children_right[node], clauses + [f"{name} > {threshold:.6g}"])

    visit(0, [])
    return paths


def _segment(samples, train_end, validation_end):
    ts = pd.to_datetime(samples["entry_ts"])
    purge = pd.Timedelta(minutes=HORIZON_MINUTES)
    return {
        "train": samples[ts < train_end - purge].copy(),
        "validation": samples[(ts >= train_end) & (ts < validation_end - purge)].copy(),
        "test": samples[ts >= validation_end].copy(),
    }


def _fit_method(name, feature_names, segments, *, fit_on_confirmed_bottoms=False):
    imputer = SimpleImputer(strategy="median")
    fit_rows = segments["train"]
    if fit_on_confirmed_bottoms:
        # Hồi cứu chỉ dùng để chọn tập học: trong những đáy đã biết, phân biệt
        # đáy nào thật sự đạt TP. Rule sau đó vẫn được apply/gate trên toàn bộ
        # potential bottom causal, không được dùng cờ này ở runtime.
        fit_rows = fit_rows[fit_rows["retrospective_local_bottom"]].copy()
    x_train = imputer.fit_transform(fit_rows[feature_names])
    y_train = (fit_rows["exit_reason"] == "TAKE_PROFIT").astype(int) if fit_on_confirmed_bottoms else fit_rows["good_bottom"].astype(int)
    model = DecisionTreeClassifier(
        max_depth=3, min_samples_leaf=MIN_TRAIN_LEAF_EVENTS,
        class_weight="balanced", random_state=20260807,
    )
    model.fit(x_train, y_train)
    paths = _tree_paths(model, feature_names)
    leaves_by_segment = {}
    for segment_name, rows in segments.items():
        leaves_by_segment[segment_name] = model.apply(imputer.transform(rows[feature_names]))

    leaf_stats = {}
    train_rows = segments["train"].copy()
    train_rows["leaf"] = leaves_by_segment["train"]
    for leaf_id in sorted(set(leaves_by_segment["train"])):
        selected = train_rows[train_rows["leaf"] == leaf_id]
        leaf_stats[int(leaf_id)] = {
            "rule": paths[int(leaf_id)],
            "train": _metrics(selected),
            "raw_train_samples": len(selected),
            "raw_good_bottom_rate_pct": round(float(selected["good_bottom"].mean() * 100), 4),
        }

    eligible = []
    for leaf_id, stats in leaf_stats.items():
        metric = stats["train"]
        if (
            metric["n"] >= MIN_TRAIN_LEAF_EVENTS
            and metric["mean_net_return_pct"] is not None
            and metric["mean_net_return_pct"] > 0
            and metric["profit_factor"] is not None
            and metric["profit_factor"] > 1
        ):
            eligible.append(leaf_id)

    selected_metrics, selected_rows = {}, {}
    for segment_name, rows in segments.items():
        tagged = rows.copy()
        tagged["leaf"] = leaves_by_segment[segment_name]
        chosen = tagged[tagged["leaf"].isin(eligible)]
        selected_metrics[segment_name] = _metrics(chosen)
        selected_rows[segment_name] = chosen[
            ["signal_ts", "entry_ts", "entry_price", "tp_pct", "sl_pct", "exit_reason", "net_return_pct", "good_bottom", "leaf"]
        ].to_dict("records")

    return {
        "method": name,
        "fit_population": "retrospective_local_bottoms" if fit_on_confirmed_bottoms else "all_potential_bottoms",
        "features": feature_names,
        "selected_leaf_ids": eligible,
        "selected_rules": [paths[leaf_id] for leaf_id in eligible],
        "metrics": selected_metrics,
        "leaf_discovery": leaf_stats,
        "selected_events": selected_rows,
    }


def _fit_ranker(name, feature_names, segments, *, fit_on_confirmed_bottoms=False):
    imputer = SimpleImputer(strategy="median")
    fit_rows = segments["train"]
    if fit_on_confirmed_bottoms:
        fit_rows = fit_rows[fit_rows["retrospective_local_bottom"]].copy()
    x_fit = imputer.fit_transform(fit_rows[feature_names])
    y_fit = (fit_rows["exit_reason"] == "TAKE_PROFIT").astype(int) if fit_on_confirmed_bottoms else fit_rows["good_bottom"].astype(int)
    model = RandomForestClassifier(
        n_estimators=300, max_depth=6, min_samples_leaf=30,
        max_features="sqrt", class_weight="balanced_subsample",
        random_state=20260807, n_jobs=-1,
    )
    model.fit(x_fit, y_fit)
    probabilities = {
        segment: model.predict_proba(imputer.transform(rows[feature_names]))[:, 1]
        for segment, rows in segments.items()
    }
    train_probs = probabilities["train"]
    candidates = []
    for top_pct in (20, 10, 5, 2, 1):
        threshold = float(np.quantile(train_probs, 1 - top_pct / 100))
        chosen = segments["train"][train_probs >= threshold]
        metric = _metrics(chosen)
        if (
            metric["n"] >= MIN_TRAIN_LEAF_EVENTS
            and metric["mean_net_return_pct"] is not None
            and metric["mean_net_return_pct"] > 0
            and metric["profit_factor"] is not None
            and metric["profit_factor"] > 1
        ):
            candidates.append((metric["mean_net_return_pct"], top_pct, threshold, metric))
    selected = max(candidates, default=None, key=lambda row: row[0])
    metrics, events = {}, {}
    if selected:
        _, top_pct, threshold, _ = selected
        for segment, rows in segments.items():
            chosen = rows[probabilities[segment] >= threshold]
            metrics[segment] = _metrics(chosen)
            events[segment] = chosen[
                ["signal_ts", "entry_ts", "entry_price", "tp_pct", "sl_pct", "exit_reason", "net_return_pct", "good_bottom"]
            ].to_dict("records")
    else:
        top_pct, threshold = None, None
        metrics = {segment: _metrics(rows.iloc[0:0]) for segment, rows in segments.items()}
        events = {segment: [] for segment in segments}
    importances = sorted(
        ({"feature": feature, "importance": round(float(value), 6)} for feature, value in zip(feature_names, model.feature_importances_)),
        key=lambda row: row["importance"], reverse=True,
    )
    return {
        "method": name,
        "model": "random_forest_depth_6",
        "fit_population": "retrospective_local_bottoms" if fit_on_confirmed_bottoms else "all_potential_bottoms",
        "features": feature_names,
        "selected_top_train_pct": top_pct,
        "probability_threshold": threshold,
        "feature_importance": importances,
        "metrics": metrics,
        "selected_events": events,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    raw = _load(Path(args.dataset_cache))
    primary = technical.add_indicators(technical.to_dataframe(raw["primary"]))
    tick = technical.to_dataframe(raw["tick"])
    path_ts, path_price = _path_arrays(tick)
    featured = _features(primary)
    cost_pct = risk.round_trip_cost_pct()
    samples = _build_samples(featured, path_ts, path_price, cost_pct)

    dataset_end = pd.Timestamp(primary["ts"].iloc[-1])
    validation_end = dataset_end - pd.Timedelta(days=30)
    train_end = validation_end - pd.Timedelta(days=30)
    segments = _segment(samples, train_end, validation_end)
    confirmation_sweep = {}
    confirmed_by_window = {}
    for right_bars in CONFIRMATION_WINDOWS:
        confirmed = _build_confirmed_bottom_samples(
            featured, path_ts, path_price, cost_pct, right_bars,
        )
        confirmed_by_window[right_bars] = confirmed
        confirmed_segments = _segment(confirmed, train_end, validation_end)
        confirmation_sweep[str(right_bars)] = {
            "confirmation_minutes": right_bars * 5,
            "raw_samples": len(confirmed),
            "metrics": {name: _metrics(rows) for name, rows in confirmed_segments.items()},
        }
    selected_confirmation_bars = max(
        CONFIRMATION_WINDOWS,
        key=lambda bars: confirmation_sweep[str(bars)]["metrics"]["train"]["win_rate_pct"] or -1,
    )
    selected_confirmed_segments = _segment(
        confirmed_by_window[selected_confirmation_bars], train_end, validation_end,
    )
    confirmed_methods = {
        "tree": _fit_method("confirmed_bottom_tree", CONFIRMED_FEATURES, selected_confirmed_segments),
        "random_forest": _fit_ranker("confirmed_bottom_rf", CONFIRMED_FEATURES, selected_confirmed_segments),
    }
    methods = {}
    for name, features in FEATURE_GROUPS.items():
        methods[f"joint_{name}"] = _fit_method(f"joint_{name}", features, segments)
        methods[f"bottom_{name}"] = _fit_method(
            f"bottom_{name}", features, segments, fit_on_confirmed_bottoms=True,
        )
    methods["rf_joint_all"] = _fit_ranker("rf_joint_all", FEATURE_GROUPS["all"], segments)
    methods["rf_bottom_all"] = _fit_ranker(
        "rf_bottom_all", FEATURE_GROUPS["all"], segments, fit_on_confirmed_bottoms=True,
    )

    output = {
        "contract": {
            "side": "long_spot",
            "signal_timeframe": "5m closed candle only",
            "potential_bottom": f"current low <= minimum low of previous {BOTTOM_LEFT_BARS} bars",
            "retrospective_bottom_label": f"current low < every low of next {BOTTOM_RIGHT_BARS} bars; label only",
            "fill": "next 5m open",
            "horizon_minutes": HORIZON_MINUTES,
            "sl": f"signal low - {SL_BUFFER_ATR} ATR",
            "tp_pct": f"max({MIN_TP_PCT}, {RISK_REWARD} * structural_SL_distance_pct)",
            "round_trip_cost_pct": cost_pct,
            "path": "1m OHLC open-low-high-close adverse-first",
            "cooldown_minutes": COOLDOWN_MINUTES,
            "model": "balanced decision tree max_depth=3; min_samples_leaf=30",
            "train_end_exclusive": str(train_end),
            "validation_end_exclusive": str(validation_end),
            "purge_minutes": HORIZON_MINUTES,
        },
        "dataset": {
            "primary_bars": len(primary), "tick_bars": len(tick),
            "start": str(primary["ts"].iloc[0]), "end": str(primary["ts"].iloc[-1]),
            "potential_bottom_samples": len(samples),
            "retrospective_local_bottoms": int(samples["retrospective_local_bottom"].sum()),
            "good_bottoms": int(samples["good_bottom"].sum()),
        },
        "segment_baselines": {name: _metrics(rows) for name, rows in segments.items()},
        "segment_raw_samples": {name: len(rows) for name, rows in segments.items()},
        "confirmed_bottom_confirmation_sweep": confirmation_sweep,
        "confirmed_bottom_selected_window": {
            "right_bars": selected_confirmation_bars,
            "confirmation_minutes": selected_confirmation_bars * 5,
            "selection": "highest train win rate among predeclared windows",
        },
        "confirmed_bottom_methods": confirmed_methods,
        "methods": methods,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "dataset": output["dataset"],
        "segment_baselines": output["segment_baselines"],
        "confirmed_bottom_confirmation_sweep": confirmation_sweep,
        "confirmed_bottom_selected_window": output["confirmed_bottom_selected_window"],
        "confirmed_bottom_methods": {
            name: {"rules": result.get("selected_rules", []), "metrics": result["metrics"]}
            for name, result in confirmed_methods.items()
        },
        "methods": {
            name: {"rules": result.get("selected_rules", []), "metrics": result["metrics"]}
            for name, result in methods.items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
