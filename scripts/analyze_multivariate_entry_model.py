"""LightGBM entry model với temporal train-eval/validation/test tách biệt."""
import argparse
import gzip
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import _load  # noqa: E402
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


HORIZON_BARS = 288
QUANTILES = (0.50, 0.60, 0.70, 0.80, 0.90)


def _json_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _flow(path, prefix):
    rows = pd.DataFrame(_json_gz(path)["rows"])
    rows["ts"] = pd.to_datetime(rows.ts, unit="ms")
    rows[f"{prefix}_imb"] = rows.taker_imbalance
    rows[f"{prefix}_imb3"] = rows.taker_imbalance.rolling(3).mean()
    rows[f"{prefix}_imb12"] = rows.taker_imbalance.rolling(12).mean()
    rows[f"{prefix}_volume_ratio"] = rows.volume / rows.volume.shift(1).rolling(12).mean()
    return rows.set_index("ts")[["close", f"{prefix}_imb", f"{prefix}_imb3", f"{prefix}_imb12", f"{prefix}_volume_ratio"]].rename(columns={"close": f"{prefix}_close"})


def _dataset(candle_path, spot_path, futures_path, derivatives_path, cost, eth_spot_path=None, eth_futures_path=None):
    raw = _load(candle_path)
    df = technical.add_indicators(technical.to_dataframe(raw["primary"])).set_index("ts")
    for bars in (1, 3, 12, 48, 288):
        df[f"return_{bars}"] = df.close.pct_change(bars)
    df["atr_pct"] = df.atr / df.close
    df["volume_ratio"] = df.volume / df.vol_sma20
    df["ema20_50_atr"] = (df.ema20 - df.ema50) / df.atr
    df["ema50_200_atr"] = (df.ema50 - df.ema200) / df.atr
    df["macd_atr"] = df.macd / df.atr
    df["macd_signal_atr"] = df.macd_signal / df.atr
    df["vwap_distance_atr"] = (df.close - df.vwap) / df.atr
    df["body_atr"] = (df.close - df.open) / df.atr
    df["lower_wick_atr"] = (df[["open", "close"]].min(axis=1) - df.low) / df.atr
    df["upper_wick_atr"] = (df.high - df[["open", "close"]].max(axis=1)) / df.atr
    spot, futures = _flow(spot_path, "spot"), _flow(futures_path, "fut")
    df = df.join(spot).join(futures)
    df["basis_pct"] = df.fut_close / df.spot_close - 1
    if eth_spot_path and eth_futures_path:
        eth_spot, eth_futures = _flow(eth_spot_path, "eth_spot"), _flow(eth_futures_path, "eth_fut")
        df = df.join(eth_spot).join(eth_futures)
        df["eth_return_1"] = df.eth_spot_close.pct_change(fill_method=None)
        df["eth_return_12"] = df.eth_spot_close.pct_change(12, fill_method=None)
        df["eth_return_288"] = df.eth_spot_close.pct_change(288, fill_method=None)
        df["eth_basis_pct"] = df.eth_fut_close / df.eth_spot_close - 1
        df["btc_eth_relative_12"] = df.return_12 - df.eth_return_12
        df["btc_eth_relative_288"] = df.return_288 - df.eth_return_288

    context = _json_gz(derivatives_path)
    metrics = pd.DataFrame(context["metrics"])
    metrics["ts"] = pd.to_datetime(metrics.ts, unit="ms")
    metrics = metrics.set_index("ts")
    for bars in (1, 3, 12):
        metrics[f"oi_change_{bars}"] = metrics.open_interest.pct_change(bars)
    df = df.join(metrics.drop(columns=["open_interest_value"], errors="ignore"))
    funding = pd.DataFrame(context["funding"])
    funding["ts"] = pd.to_datetime(funding.ts, unit="ms")
    funding = funding.set_index("ts")[["funding_rate"]]
    df = pd.merge_asof(df.reset_index().sort_values("ts"), funding.reset_index().sort_values("ts"), on="ts", direction="backward").set_index("ts")
    hour = df.index.hour + df.index.minute / 60
    df["hour_sin"], df["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    df["day_sin"], df["day_cos"] = np.sin(2 * np.pi * df.index.dayofweek / 7), np.cos(2 * np.pi * df.index.dayofweek / 7)
    entry = df.open.shift(-1)
    exit_price = df.close.shift(-HORIZON_BARS)
    gross = (exit_price / entry - 1) * 100
    df["target_long"] = gross - cost
    df["target_short"] = -gross - cost
    return df.replace([np.inf, -np.inf], np.nan)


def _simulate(frame, prediction, threshold, target):
    candidates = frame.assign(prediction=prediction)
    candidates = candidates[candidates.prediction >= threshold]
    selected, available_at = [], None
    for ts, row in candidates.iterrows():
        entry_ts = ts + pd.Timedelta(minutes=5)
        if available_at is not None and entry_ts < available_at:
            continue
        selected.append(float(row[target]))
        available_at = entry_ts + pd.Timedelta(hours=24)
    if not selected:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None}
    values = np.asarray(selected)
    gain, loss = values[values > 0].sum(), abs(values[values < 0].sum())
    return {"n": len(values), "win_rate_pct": round(float((values > 0).mean() * 100), 4), "mean_net_return_pct": round(float(values.mean()), 6), "sum_net_return_pct": round(float(values.sum()), 6), "profit_factor": round(float(gain / loss), 6) if loss else None}


def _simulate_rolling(frame, prediction, rolling_threshold, target):
    candidates = frame.assign(prediction=prediction, rolling_threshold=rolling_threshold.reindex(frame.index))
    candidates = candidates[candidates.prediction >= candidates.rolling_threshold]
    return _simulate(candidates, candidates.prediction.to_numpy(), -np.inf, target)


def _simulate_adaptive(frame, long_prediction, short_prediction, rolling_threshold):
    candidates = frame.assign(long_prediction=long_prediction, short_prediction=short_prediction)
    candidates["prediction"] = candidates[["long_prediction", "short_prediction"]].max(axis=1)
    candidates["rolling_threshold"] = rolling_threshold.reindex(frame.index)
    candidates = candidates[candidates.prediction >= candidates.rolling_threshold]
    values, available_at = [], None
    for ts, row in candidates.iterrows():
        entry_ts = ts + pd.Timedelta(minutes=5)
        if available_at is not None and entry_ts < available_at:
            continue
        target = "target_long" if row.long_prediction >= row.short_prediction else "target_short"
        values.append(float(row[target]))
        available_at = entry_ts + pd.Timedelta(hours=24)
    if not values:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None}
    values = np.asarray(values)
    gain, loss = values[values > 0].sum(), abs(values[values < 0].sum())
    return {"n": len(values), "win_rate_pct": round(float((values > 0).mean() * 100), 4), "mean_net_return_pct": round(float(values.mean()), 6), "sum_net_return_pct": round(float(values.sum()), 6), "profit_factor": round(float(gain / loss), 6) if loss else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--spot-flow", required=True)
    parser.add_argument("--futures-flow", required=True)
    parser.add_argument("--derivatives-context", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    cost = risk.round_trip_cost_pct()
    df = _dataset(Path(args.dataset_cache), Path(args.spot_flow), Path(args.futures_flow), Path(args.derivatives_context), cost)
    features = [
        "return_1", "return_3", "return_12", "return_48", "return_288",
        "atr_pct", "volume_ratio", "ema20_50_atr", "ema50_200_atr",
        "macd_atr", "macd_signal_atr", "vwap_distance_atr", "body_atr",
        "lower_wick_atr", "upper_wick_atr", "rsi", "adx", "supertrend_dir",
        "spot_imb", "spot_imb3", "spot_imb12", "spot_volume_ratio",
        "fut_imb", "fut_imb3", "fut_imb12", "fut_volume_ratio", "basis_pct",
        "oi_change_1", "oi_change_3", "oi_change_12",
        "top_accounts_long_short", "top_positions_long_short",
        "global_long_short", "taker_long_short", "funding_rate",
        "hour_sin", "hour_cos", "day_sin", "day_cos",
    ]
    usable = df.dropna(subset=features + ["target_long", "target_short"])
    end = usable.index[-1]
    fit_end, validation_start, test_start = end - pd.Timedelta(days=90), end - pd.Timedelta(days=60), end - pd.Timedelta(days=30)
    fit = usable[usable.index < fit_end - pd.Timedelta(hours=24)]
    periods = {
        "train_eval": usable[(usable.index >= fit_end) & (usable.index < validation_start - pd.Timedelta(hours=24))],
        "validation": usable[(usable.index >= validation_start) & (usable.index < test_start - pd.Timedelta(hours=24))],
        "test": usable[(usable.index >= test_start) & (usable.index < end - pd.Timedelta(hours=24))],
    }
    results, passes, model_predictions = {}, [], {}
    for side in ("long", "short"):
        target = f"target_{side}"
        model = lgb.LGBMRegressor(objective="huber", n_estimators=250, learning_rate=0.03, num_leaves=15, max_depth=4, min_child_samples=100, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, random_state=42, verbosity=-1)
        model.fit(fit[features], fit[target])
        all_prediction = pd.Series(model.predict(usable[features]), index=usable.index)
        model_predictions[side] = all_prediction
        predictions = {name: model.predict(frame[features]) for name, frame in periods.items()}
        candidates = []
        train_pred = predictions["train_eval"]
        for quantile in QUANTILES:
            threshold = float(np.quantile(train_pred, quantile))
            metrics = {name: _simulate(periods[name], predictions[name], threshold, target) for name in periods}
            candidates.append({"mode": "absolute_train_quantile", "quantile": quantile, "threshold": threshold, "metrics": metrics})
        for days in (7, 30):
            window = days * 288
            for quantile in (0.70, 0.80, 0.90):
                rolling_threshold = all_prediction.rolling(window, min_periods=288).quantile(quantile).shift(1)
                metrics = {name: _simulate_rolling(periods[name], predictions[name], rolling_threshold, target) for name in periods}
                candidates.append({"mode": "causal_rolling_quantile", "lookback_days": days, "quantile": quantile, "metrics": metrics})
        eligible = [x for x in candidates if x["metrics"]["train_eval"]["n"] >= 20]
        selected = max(eligible, key=lambda x: (x["metrics"]["train_eval"]["mean_net_return_pct"], x["metrics"]["train_eval"]["profit_factor"] or 0))
        importance = sorted(zip(features, model.feature_importances_.tolist()), key=lambda x: x[1], reverse=True)[:15]
        results[side] = {"selected": selected, "candidates": candidates, "top_feature_importance": importance}
        values = [selected["metrics"][key] for key in ("train_eval", "validation", "test")]
        if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in values):
            passes.append({"side": side, **selected})
    best_score = pd.concat(model_predictions, axis=1).max(axis=1)
    adaptive_candidates = []
    for days in (7, 30):
        for quantile in (0.50, 0.60, 0.70, 0.80, 0.90):
            rolling_threshold = best_score.rolling(days * 288, min_periods=288).quantile(quantile).shift(1)
            metrics = {}
            for name, frame in periods.items():
                metrics[name] = _simulate_adaptive(frame, model_predictions["long"].reindex(frame.index), model_predictions["short"].reindex(frame.index), rolling_threshold)
            adaptive_candidates.append({"mode": "adaptive_side_causal_rolling_quantile", "lookback_days": days, "quantile": quantile, "metrics": metrics})
    adaptive_eligible = [x for x in adaptive_candidates if x["metrics"]["train_eval"]["n"] >= 20]
    adaptive_selected = max(adaptive_eligible, key=lambda x: (x["metrics"]["train_eval"]["mean_net_return_pct"], x["metrics"]["train_eval"]["profit_factor"] or 0))
    results["adaptive_side"] = {"selected": adaptive_selected, "candidates": adaptive_candidates}
    adaptive_values = [adaptive_selected["metrics"][key] for key in ("train_eval", "validation", "test")]
    if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in adaptive_values):
        passes.append({"side": "adaptive", **adaptive_selected})
    period_bounds = {
        "train_eval": (fit_end, validation_start),
        "validation": (validation_start, test_start),
        "test": (test_start, end),
    }
    walk_predictions = {"long": {}, "short": {}}
    for side in ("long", "short"):
        target = f"target_{side}"
        for name, (period_start, period_end) in period_bounds.items():
            walk_fit = usable[usable.index < period_start - pd.Timedelta(hours=24)]
            context_frame = usable[(usable.index >= period_start - pd.Timedelta(days=30)) & (usable.index < period_end)]
            model = lgb.LGBMRegressor(objective="huber", n_estimators=250, learning_rate=0.03, num_leaves=15, max_depth=4, min_child_samples=100, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, random_state=42, verbosity=-1)
            model.fit(walk_fit[features], walk_fit[target])
            walk_predictions[side][name] = pd.Series(model.predict(context_frame[features]), index=context_frame.index)
    walk_candidates = []
    for days in (7, 30):
        for quantile in (0.50, 0.60, 0.70, 0.80, 0.90):
            metrics = {}
            for name, frame in periods.items():
                long_prediction = walk_predictions["long"][name]
                short_prediction = walk_predictions["short"][name]
                best = pd.concat({"long": long_prediction, "short": short_prediction}, axis=1).max(axis=1)
                threshold = best.rolling(days * 288, min_periods=288).quantile(quantile).shift(1)
                metrics[name] = _simulate_adaptive(frame, long_prediction.reindex(frame.index), short_prediction.reindex(frame.index), threshold)
            walk_candidates.append({"mode": "walk_forward_retrain_adaptive_side", "lookback_days": days, "quantile": quantile, "metrics": metrics})
    walk_eligible = [x for x in walk_candidates if x["metrics"]["train_eval"]["n"] >= 20]
    walk_selected = max(walk_eligible, key=lambda x: (x["metrics"]["train_eval"]["mean_net_return_pct"], x["metrics"]["train_eval"]["profit_factor"] or 0))
    results["walk_forward_adaptive"] = {"selected": walk_selected, "candidates": walk_candidates}
    walk_values = [walk_selected["metrics"][key] for key in ("train_eval", "validation", "test")]
    if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in walk_values):
        passes.append({"side": "walk_forward_adaptive", **walk_selected})
    output = {"contract": {"fit": "earliest data only", "threshold_selection": "train_eval only", "untouched_holdouts": ["validation", "test"], "horizon_minutes": 1440, "round_trip_cost_pct": cost, "single_concurrent_position": True, "minimum_each_evaluation_segment": 20}, "dataset": {"fit_rows": len(fit), "period_rows": {k: len(v) for k, v in periods.items()}, "fit_end": str(fit_end), "validation_start": str(validation_start), "test_start": str(test_start), "end": str(end)}, "features": features, "passes": passes, "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "dataset": output["dataset"], "selected": {k: v["selected"] for k, v in results.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
