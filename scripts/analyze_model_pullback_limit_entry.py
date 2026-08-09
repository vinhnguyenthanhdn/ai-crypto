"""Model timing + pullback limit fill, triple-barrier outcome từ giá fill."""
import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_multivariate_entry_model import _dataset  # noqa: E402
from scripts.analyze_triple_barrier_entry_model import FEATURES, _metrics  # noqa: E402
from src.engine import risk  # noqa: E402


TP_PCT, SL_PCT, HORIZON = 3.0, 2.0, 288
LIMITS = ((0.25, 12), (0.50, 12), (0.50, 48), (1.00, 48))


def _limit_outcomes(df, side, offset_pct, wait_bars, cost):
    high, low, close = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy()
    values, exits, filled, tp_first = np.full(len(df), np.nan), np.full(len(df), -1), np.zeros(len(df), dtype=bool), np.zeros(len(df), dtype=bool)
    for i in range(len(df) - HORIZON - wait_bars - 1):
        reference = close[i]
        limit = reference * (1 - offset_pct / 100) if side == "LONG" else reference * (1 + offset_pct / 100)
        fill_window = low[i + 1:i + wait_bars + 1] <= limit if side == "LONG" else high[i + 1:i + wait_bars + 1] >= limit
        hits = np.flatnonzero(fill_window)
        if not len(hits):
            exits[i] = i + wait_bars
            continue
        fill_idx = i + 1 + int(hits[0]); filled[i] = True
        end = fill_idx + HORIZON
        if side == "LONG":
            take_hits = np.flatnonzero(high[fill_idx:end] >= limit * (1 + TP_PCT / 100))
            stop_hits = np.flatnonzero(low[fill_idx:end] <= limit * (1 - SL_PCT / 100))
            timeout = (close[end - 1] / limit - 1) * 100 - cost
        else:
            take_hits = np.flatnonzero(low[fill_idx:end] <= limit * (1 - TP_PCT / 100))
            stop_hits = np.flatnonzero(high[fill_idx:end] >= limit * (1 + SL_PCT / 100))
            timeout = (limit - close[end - 1]) / limit * 100 - cost
        take_i = int(take_hits[0]) if len(take_hits) else HORIZON + 1
        stop_i = int(stop_hits[0]) if len(stop_hits) else HORIZON + 1
        if take_i < stop_i:
            values[i], exits[i], tp_first[i] = TP_PCT - cost, fill_idx + take_i, True
        elif stop_i <= HORIZON:
            values[i], exits[i] = -SL_PCT - cost, fill_idx + stop_i
        else:
            values[i], exits[i] = timeout, end - 1
    index = df.index
    exit_ts = pd.Series(pd.NaT, index=index)
    valid = exits >= 0
    exit_ts.iloc[np.flatnonzero(valid)] = index[np.minimum(exits[valid], len(index) - 1)]
    return pd.Series(values, index=index), exit_ts, pd.Series(filled, index=index), pd.Series(tp_first, index=index)


def _simulate(frame, side_scores, thresholds, outcomes, exit_times, regimes):
    returns, side_counts, available_at = [], {"LONG": 0, "SHORT": 0}, None
    for ts, row in frame.iterrows():
        side = "LONG" if row[regimes] >= 0 else "SHORT"
        if side_scores[side].get(ts, np.nan) < thresholds[side].get(ts, np.nan):
            continue
        if available_at is not None and ts < available_at:
            continue
        exit_ts = exit_times[side].get(ts, pd.NaT)
        if pd.isna(exit_ts):
            continue
        available_at = pd.Timestamp(exit_ts)
        value = outcomes[side].get(ts, np.nan)
        if pd.notna(value):
            returns.append(float(value)); side_counts[side] += 1
    result = _metrics(returns)
    result.update({"long_trades": side_counts["LONG"], "short_trades": side_counts["SHORT"]})
    return result


def main():
    parser = argparse.ArgumentParser()
    for name in ("dataset-cache", "spot-flow", "futures-flow", "derivatives-context", "eth-spot-flow", "eth-futures-flow", "out"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    cost = risk.round_trip_cost_pct()
    df = _dataset(Path(args.dataset_cache), Path(args.spot_flow), Path(args.futures_flow), Path(args.derivatives_context), cost, Path(args.eth_spot_flow), Path(args.eth_futures_flow))
    df["return_2016"] = df.close.pct_change(2016)
    usable = df.dropna(subset=FEATURES)
    end = usable.index[-1]
    fit_end, validation_start, test_start = end - pd.Timedelta(days=90), end - pd.Timedelta(days=60), end - pd.Timedelta(days=30)
    periods = {"train_eval": usable[(usable.index >= fit_end) & (usable.index < validation_start)], "validation": usable[(usable.index >= validation_start) & (usable.index < test_start)], "test": usable[(usable.index >= test_start) & (usable.index < end)]}
    candidates = []
    for offset, wait_bars in LIMITS:
        outcomes, exits, scores = {}, {}, {}
        for side in ("LONG", "SHORT"):
            outcome, exit_ts, filled, tp_first = _limit_outcomes(usable, side, offset, wait_bars, cost)
            outcomes[side], exits[side] = outcome, exit_ts
            fit_mask = (usable.index < fit_end - pd.Timedelta(hours=24)) & filled & outcome.notna()
            model = lgb.LGBMClassifier(objective="binary", n_estimators=250, learning_rate=0.03, num_leaves=15, max_depth=4, min_child_samples=100, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, random_state=42, verbosity=-1)
            model.fit(usable.loc[fit_mask, FEATURES], tp_first.loc[fit_mask].astype(int))
            scores[side] = pd.Series(model.predict_proba(usable[FEATURES])[:, 1], index=usable.index)
        for days in (7, 30):
            window = days * 288
            for quantile in (0.50, 0.60, 0.70, 0.80):
                thresholds = {side: score.rolling(window, min_periods=288).quantile(quantile).shift(1) for side, score in scores.items()}
                for regime in ("return_288", "return_2016", "ema50_200_atr"):
                    metrics = {name: _simulate(frame, scores, thresholds, outcomes, exits, regime) for name, frame in periods.items()}
                    candidates.append({"offset_pct": offset, "wait_minutes": wait_bars * 5, "lookback_days": days, "quantile": quantile, "regime": regime, "metrics": metrics})
    eligible = [x for x in candidates if x["metrics"]["train_eval"]["n"] >= 20]
    selected = max(eligible, key=lambda x: (x["metrics"]["train_eval"]["mean_net_return_pct"], x["metrics"]["train_eval"]["profit_factor"] or 0))
    values = [selected["metrics"][name] for name in ("train_eval", "validation", "test")]
    passes = [selected] if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in values) else []
    output = {"contract": {"signal_timeframe": "5m", "entry": "pullback limit from signal close", "tp_pct": TP_PCT, "sl_pct": SL_PCT, "horizon_minutes_after_fill": 1440, "selection": "execution and threshold selected on train_eval only", "round_trip_cost_pct": cost}, "dataset": {"fit_end": str(fit_end), "validation_start": str(validation_start), "test_start": str(test_start), "end": str(end)}, "passes": passes, "selected": selected, "candidates": candidates}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
