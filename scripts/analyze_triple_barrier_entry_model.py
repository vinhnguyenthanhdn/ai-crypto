"""LightGBM classifier dự báo TP-before-SL bằng causal triple-barrier label."""
import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_multivariate_entry_model import _dataset  # noqa: E402
from scripts.analyze_choch_entry import _load  # noqa: E402
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


FEATURES = [
    "return_1", "return_3", "return_12", "return_48", "return_288",
    "atr_pct", "volume_ratio", "ema20_50_atr", "ema50_200_atr",
    "macd_atr", "macd_signal_atr", "vwap_distance_atr", "body_atr",
    "lower_wick_atr", "upper_wick_atr", "rsi", "adx", "supertrend_dir",
    "spot_imb", "spot_imb3", "spot_imb12", "spot_volume_ratio",
    "fut_imb", "fut_imb3", "fut_imb12", "fut_volume_ratio", "basis_pct",
    "oi_change_1", "oi_change_3", "oi_change_12", "top_accounts_long_short",
    "top_positions_long_short", "global_long_short", "taker_long_short",
    "funding_rate", "hour_sin", "hour_cos", "day_sin", "day_cos",
    "eth_return_1", "eth_return_12", "eth_return_288", "eth_basis_pct",
    "eth_spot_imb", "eth_spot_imb3", "eth_spot_imb12", "eth_spot_volume_ratio",
    "eth_fut_imb", "eth_fut_imb3", "eth_fut_imb12", "eth_fut_volume_ratio",
    "btc_eth_relative_12", "btc_eth_relative_288",
]
BARRIERS = ((1.5, 1.0), (2.0, 1.5), (3.0, 2.0))
HORIZON = 288


def _outcomes(df, side, tp_pct, sl_pct, cost):
    high, low, close, entry = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy(), df.open.shift(-1).to_numpy()
    values = np.full(len(df), np.nan)
    tp_first = np.zeros(len(df), dtype=bool)
    for i in range(len(df) - HORIZON - 1):
        start, end = i + 1, i + HORIZON + 1
        price = entry[i]
        if side == "LONG":
            take_hits = np.flatnonzero(high[start:end] >= price * (1 + tp_pct / 100))
            stop_hits = np.flatnonzero(low[start:end] <= price * (1 - sl_pct / 100))
            timeout = (close[end - 1] / price - 1) * 100 - cost
        else:
            take_hits = np.flatnonzero(low[start:end] <= price * (1 - tp_pct / 100))
            stop_hits = np.flatnonzero(high[start:end] >= price * (1 + sl_pct / 100))
            timeout = (price - close[end - 1]) / price * 100 - cost
        take_i = int(take_hits[0]) if len(take_hits) else HORIZON + 1
        stop_i = int(stop_hits[0]) if len(stop_hits) else HORIZON + 1
        # Cùng nến 5m xử lý adverse-first để không phóng đại kết quả.
        if take_i < stop_i:
            values[i], tp_first[i] = tp_pct - cost, True
        elif stop_i <= HORIZON:
            values[i] = -sl_pct - cost
        else:
            values[i] = timeout
    return pd.Series(values, index=df.index), pd.Series(tp_first, index=df.index)


def _outcomes_1m(df, tick, side, tp_pct, sl_pct, cost):
    times = tick.ts.to_numpy(dtype="datetime64[ns]")
    high, low, close = tick.high.to_numpy(), tick.low.to_numpy(), tick.close.to_numpy()
    entry = df.open.shift(-1).to_numpy()
    values = np.full(len(df), np.nan); tp_first = np.zeros(len(df), dtype=bool)
    for i, ts in enumerate(df.index[:-1]):
        entry_ts = np.datetime64(ts + pd.Timedelta(minutes=5))
        start = int(np.searchsorted(times, entry_ts, side="left"))
        end_ts = entry_ts + np.timedelta64(1440, "m")
        end = int(np.searchsorted(times, end_ts, side="left"))
        if start >= len(times) or end >= len(times):
            continue
        price = entry[i]
        if side == "LONG":
            take_hits = np.flatnonzero(high[start:end] >= price * (1 + tp_pct / 100))
            stop_hits = np.flatnonzero(low[start:end] <= price * (1 - sl_pct / 100))
            timeout = (close[end - 1] / price - 1) * 100 - cost
        else:
            take_hits = np.flatnonzero(low[start:end] <= price * (1 - tp_pct / 100))
            stop_hits = np.flatnonzero(high[start:end] >= price * (1 + sl_pct / 100))
            timeout = (price - close[end - 1]) / price * 100 - cost
        take_i = int(take_hits[0]) if len(take_hits) else end - start + 1
        stop_i = int(stop_hits[0]) if len(stop_hits) else end - start + 1
        if take_i < stop_i:
            values[i], tp_first[i] = tp_pct - cost, True
        elif stop_i <= end - start:
            values[i] = -sl_pct - cost
        else:
            values[i] = timeout
    return pd.Series(values, index=df.index), pd.Series(tp_first, index=df.index)


def _metrics(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None}
    gain, loss = values[values > 0].sum(), abs(values[values < 0].sum())
    return {"n": len(values), "win_rate_pct": round(float((values > 0).mean() * 100), 4), "mean_net_return_pct": round(float(values.mean()), 6), "sum_net_return_pct": round(float(values.sum()), 6), "profit_factor": round(float(gain / loss), 6) if loss else None}


def _simulate(frame, long_score, short_score, threshold, long_outcome, short_outcome):
    joined = frame.assign(long_score=long_score.reindex(frame.index), short_score=short_score.reindex(frame.index), threshold=threshold.reindex(frame.index))
    joined["score"] = joined[["long_score", "short_score"]].max(axis=1)
    joined = joined[joined.score >= joined.threshold]
    returns, sides, available_at = [], [], None
    for ts, row in joined.iterrows():
        entry_ts = ts + pd.Timedelta(minutes=5)
        if available_at is not None and entry_ts < available_at:
            continue
        side = "LONG" if row.long_score >= row.short_score else "SHORT"
        value = long_outcome.loc[ts] if side == "LONG" else short_outcome.loc[ts]
        if pd.notna(value):
            returns.append(float(value))
            sides.append(side)
            available_at = entry_ts + pd.Timedelta(hours=24)
    result = _metrics(returns)
    result["long_trades"] = sides.count("LONG")
    result["short_trades"] = sides.count("SHORT")
    result["long_sum_net_return_pct"] = round(sum(value for value, side in zip(returns, sides) if side == "LONG"), 6)
    result["short_sum_net_return_pct"] = round(sum(value for value, side in zip(returns, sides) if side == "SHORT"), 6)
    return result


def _simulate_side_calibrated(frame, long_score, short_score, long_threshold, short_threshold, long_mean, short_mean, long_std, short_std, long_outcome, short_outcome):
    joined = frame.assign(
        long_score=long_score.reindex(frame.index), short_score=short_score.reindex(frame.index),
        long_threshold=long_threshold.reindex(frame.index), short_threshold=short_threshold.reindex(frame.index),
        long_z=((long_score - long_mean) / long_std.replace(0, np.nan)).reindex(frame.index),
        short_z=((short_score - short_mean) / short_std.replace(0, np.nan)).reindex(frame.index),
    )
    joined = joined[(joined.long_score >= joined.long_threshold) | (joined.short_score >= joined.short_threshold)]
    returns, sides, available_at = [], [], None
    for ts, row in joined.iterrows():
        entry_ts = ts + pd.Timedelta(minutes=5)
        if available_at is not None and entry_ts < available_at:
            continue
        long_ok, short_ok = row.long_score >= row.long_threshold, row.short_score >= row.short_threshold
        side = "LONG" if long_ok and (not short_ok or row.long_z >= row.short_z) else "SHORT"
        value = long_outcome.loc[ts] if side == "LONG" else short_outcome.loc[ts]
        if pd.notna(value):
            returns.append(float(value)); sides.append(side)
            available_at = entry_ts + pd.Timedelta(hours=24)
    result = _metrics(returns)
    result.update({
        "long_trades": sides.count("LONG"), "short_trades": sides.count("SHORT"),
        "long_sum_net_return_pct": round(sum(value for value, side in zip(returns, sides) if side == "LONG"), 6),
        "short_sum_net_return_pct": round(sum(value for value, side in zip(returns, sides) if side == "SHORT"), 6),
    })
    return result


def _simulate_regime_side(frame, long_score, short_score, long_threshold, short_threshold, regime_column, long_outcome, short_outcome):
    joined = frame.assign(
        long_score=long_score.reindex(frame.index), short_score=short_score.reindex(frame.index),
        long_threshold=long_threshold.reindex(frame.index), short_threshold=short_threshold.reindex(frame.index),
    )
    returns, sides, available_at = [], [], None
    for ts, row in joined.iterrows():
        side = "LONG" if row[regime_column] >= 0 else "SHORT"
        score_ok = row.long_score >= row.long_threshold if side == "LONG" else row.short_score >= row.short_threshold
        if not score_ok:
            continue
        entry_ts = ts + pd.Timedelta(minutes=5)
        if available_at is not None and entry_ts < available_at:
            continue
        value = long_outcome.loc[ts] if side == "LONG" else short_outcome.loc[ts]
        if pd.notna(value):
            returns.append(float(value)); sides.append(side)
            available_at = entry_ts + pd.Timedelta(hours=24)
    result = _metrics(returns)
    result.update({
        "long_trades": sides.count("LONG"), "short_trades": sides.count("SHORT"),
        "long_sum_net_return_pct": round(sum(value for value, side in zip(returns, sides) if side == "LONG"), 6),
        "short_sum_net_return_pct": round(sum(value for value, side in zip(returns, sides) if side == "SHORT"), 6),
    })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--spot-flow", required=True)
    parser.add_argument("--futures-flow", required=True)
    parser.add_argument("--derivatives-context", required=True)
    parser.add_argument("--eth-spot-flow", required=True)
    parser.add_argument("--eth-futures-flow", required=True)
    parser.add_argument("--analysis-end")
    parser.add_argument("--frozen-only", action="store_true")
    parser.add_argument("--exclude-eth", action="store_true")
    parser.add_argument("--one-minute-labels", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    cost = risk.round_trip_cost_pct()
    df = _dataset(
        Path(args.dataset_cache), Path(args.spot_flow), Path(args.futures_flow),
        Path(args.derivatives_context), cost, Path(args.eth_spot_flow),
        Path(args.eth_futures_flow),
    )
    if args.analysis_end:
        df = df[df.index <= pd.Timestamp(args.analysis_end)]
    df["return_2016"] = df.close.pct_change(2016)
    features = [name for name in FEATURES if not (args.exclude_eth and (name.startswith("eth_") or name.startswith("btc_eth_")))]
    usable = df.dropna(subset=features)
    tick = None
    if args.one_minute_labels:
        raw = _load(Path(args.dataset_cache))
        if not raw.get("tick"):
            raise ValueError("--one-minute-labels yêu cầu dataset cache có tick 1m")
        tick = technical.to_dataframe(raw["tick"])
    outcome_builder = (lambda frame, side, tp, sl, fee: _outcomes_1m(frame, tick, side, tp, sl, fee)) if tick is not None else _outcomes
    end = usable.index[-1]
    fit_end, validation_start, test_start = end - pd.Timedelta(days=90), end - pd.Timedelta(days=60), end - pd.Timedelta(days=30)
    periods = {
        "train_eval": usable[(usable.index >= fit_end) & (usable.index < validation_start - pd.Timedelta(hours=24))],
        "validation": usable[(usable.index >= validation_start) & (usable.index < test_start - pd.Timedelta(hours=24))],
        "test": usable[(usable.index >= test_start) & (usable.index < end - pd.Timedelta(hours=24))],
    }
    candidates = []
    barriers = ((2.0, 1.5),) if args.frozen_only else BARRIERS
    days_values = (7,) if args.frozen_only else (7, 30)
    quantile_values = (0.50,) if args.frozen_only else (0.50, 0.60, 0.70, 0.80, 0.90)
    regime_values = ("return_288",) if args.frozen_only else ("return_288", "return_2016", "ema50_200_atr")
    for tp, sl in barriers:
        outcomes = {}
        scores = {}
        for side in ("long", "short"):
            outcome, tp_first = outcome_builder(usable, side.upper(), tp, sl, cost)
            outcomes[side] = outcome
            fit_mask = (usable.index < fit_end - pd.Timedelta(hours=24)) & outcome.notna()
            model = lgb.LGBMClassifier(objective="binary", n_estimators=250, learning_rate=0.03, num_leaves=15, max_depth=4, min_child_samples=100, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, random_state=42, verbosity=-1)
            model.fit(usable.loc[fit_mask, features], tp_first.loc[fit_mask].astype(int))
            scores[side] = pd.Series(model.predict_proba(usable[features])[:, 1], index=usable.index)
        best_score = pd.concat(scores, axis=1).max(axis=1)
        for days in days_values:
            for quantile in quantile_values:
                threshold = best_score.rolling(days * 288, min_periods=288).quantile(quantile).shift(1)
                metrics = {name: _simulate(frame, scores["long"], scores["short"], threshold, outcomes["long"], outcomes["short"]) for name, frame in periods.items()}
                candidates.append({"mode": "raw_cross_side_score", "tp_pct": tp, "sl_pct": sl, "lookback_days": days, "quantile": quantile, "metrics": metrics})
                window = days * 288
                long_mean, short_mean = scores["long"].rolling(window, min_periods=288).mean().shift(1), scores["short"].rolling(window, min_periods=288).mean().shift(1)
                long_std, short_std = scores["long"].rolling(window, min_periods=288).std().shift(1), scores["short"].rolling(window, min_periods=288).std().shift(1)
                long_threshold = scores["long"].rolling(window, min_periods=288).quantile(quantile).shift(1)
                short_threshold = scores["short"].rolling(window, min_periods=288).quantile(quantile).shift(1)
                calibrated = {name: _simulate_side_calibrated(frame, scores["long"], scores["short"], long_threshold, short_threshold, long_mean, short_mean, long_std, short_std, outcomes["long"], outcomes["short"]) for name, frame in periods.items()}
                candidates.append({"mode": "per_side_rolling_calibrated", "tp_pct": tp, "sl_pct": sl, "lookback_days": days, "quantile": quantile, "metrics": calibrated})
                for regime in regime_values:
                    regime_metrics = {name: _simulate_regime_side(frame, scores["long"], scores["short"], long_threshold, short_threshold, regime, outcomes["long"], outcomes["short"]) for name, frame in periods.items()}
                    candidates.append({"mode": "causal_regime_side", "regime": regime, "tp_pct": tp, "sl_pct": sl, "lookback_days": days, "quantile": quantile, "metrics": regime_metrics})
    eligible = [x for x in candidates if x["metrics"]["train_eval"]["n"] >= 20]
    selected = max(eligible, key=lambda x: (x["metrics"]["train_eval"]["mean_net_return_pct"], x["metrics"]["train_eval"]["profit_factor"] or 0))
    values = [selected["metrics"][key] for key in ("train_eval", "validation", "test")]
    passes = [selected] if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in values) else []
    period_bounds = {
        "train_eval": (fit_end, validation_start),
        "validation": (validation_start, test_start),
        "test": (test_start, end),
    }
    walk_candidates = []
    for tp, sl in barriers:
        outcomes, scores = {}, {"long": {}, "short": {}}
        for side in ("long", "short"):
            outcome, tp_first = outcome_builder(usable, side.upper(), tp, sl, cost)
            outcomes[side] = outcome
            for name, (period_start, period_end) in period_bounds.items():
                fit_mask = (usable.index < period_start - pd.Timedelta(hours=24)) & outcome.notna()
                context_frame = usable[(usable.index >= period_start - pd.Timedelta(days=30)) & (usable.index < period_end)]
                model = lgb.LGBMClassifier(objective="binary", n_estimators=250, learning_rate=0.03, num_leaves=15, max_depth=4, min_child_samples=100, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, random_state=42, verbosity=-1)
                model.fit(usable.loc[fit_mask, features], tp_first.loc[fit_mask].astype(int))
                scores[side][name] = pd.Series(model.predict_proba(context_frame[features])[:, 1], index=context_frame.index)
        for days in days_values:
            for quantile in quantile_values:
                metrics = {}
                for name, frame in periods.items():
                    best_score = pd.concat({"long": scores["long"][name], "short": scores["short"][name]}, axis=1).max(axis=1)
                    threshold = best_score.rolling(days * 288, min_periods=288).quantile(quantile).shift(1)
                    metrics[name] = _simulate(frame, scores["long"][name], scores["short"][name], threshold, outcomes["long"], outcomes["short"])
                walk_candidates.append({"mode": "walk_forward_retrain", "tp_pct": tp, "sl_pct": sl, "lookback_days": days, "quantile": quantile, "metrics": metrics})
                calibrated_metrics = {}
                for name, frame in periods.items():
                    long_score, short_score = scores["long"][name], scores["short"][name]
                    window = days * 288
                    long_mean, short_mean = long_score.rolling(window, min_periods=288).mean().shift(1), short_score.rolling(window, min_periods=288).mean().shift(1)
                    long_std, short_std = long_score.rolling(window, min_periods=288).std().shift(1), short_score.rolling(window, min_periods=288).std().shift(1)
                    long_threshold = long_score.rolling(window, min_periods=288).quantile(quantile).shift(1)
                    short_threshold = short_score.rolling(window, min_periods=288).quantile(quantile).shift(1)
                    calibrated_metrics[name] = _simulate_side_calibrated(frame, long_score, short_score, long_threshold, short_threshold, long_mean, short_mean, long_std, short_std, outcomes["long"], outcomes["short"])
                walk_candidates.append({"mode": "walk_forward_per_side_calibrated", "tp_pct": tp, "sl_pct": sl, "lookback_days": days, "quantile": quantile, "metrics": calibrated_metrics})
                for regime in regime_values:
                    regime_metrics = {}
                    for name, frame in periods.items():
                        long_score, short_score = scores["long"][name], scores["short"][name]
                        window = days * 288
                        long_threshold = long_score.rolling(window, min_periods=288).quantile(quantile).shift(1)
                        short_threshold = short_score.rolling(window, min_periods=288).quantile(quantile).shift(1)
                        regime_metrics[name] = _simulate_regime_side(frame, long_score, short_score, long_threshold, short_threshold, regime, outcomes["long"], outcomes["short"])
                    walk_candidates.append({"mode": "walk_forward_causal_regime_side", "regime": regime, "tp_pct": tp, "sl_pct": sl, "lookback_days": days, "quantile": quantile, "metrics": regime_metrics})
    walk_eligible = [x for x in walk_candidates if x["metrics"]["train_eval"]["n"] >= 20]
    walk_selected = max(walk_eligible, key=lambda x: (x["metrics"]["train_eval"]["mean_net_return_pct"], x["metrics"]["train_eval"]["profit_factor"] or 0))
    walk_values = [walk_selected["metrics"][key] for key in ("train_eval", "validation", "test")]
    if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in walk_values):
        passes.append(walk_selected)
    output = {"contract": {"label": "TP-before-SL, adverse-first inside ambiguous 5m bar", "selection": "barrier and causal rolling threshold selected on train_eval only", "horizon_minutes": 1440, "round_trip_cost_pct": cost, "single_concurrent_position": True, "minimum_each_segment": 20}, "dataset": {"fit_end": str(fit_end), "validation_start": str(validation_start), "test_start": str(test_start), "end": str(end)}, "passes": passes, "selected": selected, "walk_forward_selected": walk_selected, "candidates": candidates, "walk_forward_candidates": walk_candidates}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "selected": selected, "walk_forward_selected": walk_selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
