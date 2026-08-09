"""Discover a short-horizon, causal multivariate BTC Swap candidate.

The test holdout is never used for model, barrier, or threshold selection.
Signals are produced from a completed 1h candle and filled at the next 1h open.
"""
from __future__ import annotations

import argparse
import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd


FEATURES = [
    "return_1", "return_3", "return_6", "return_24", "return_72",
    "atr_pct", "atr_ratio_24", "volume_ratio", "ema20_50_atr",
    "ema50_200_atr", "close_ema20_atr", "rsi", "adx", "macd_atr",
    "body_atr", "lower_wick_atr", "upper_wick_atr", "range_atr",
    "spot_imb", "spot_imb_3", "spot_imb_12", "spot_volume_ratio",
    "fut_imb", "fut_imb_3", "fut_imb_12", "fut_volume_ratio",
    "basis_pct", "oi_change_1", "oi_change_4", "oi_change_24",
    "top_accounts_long_short", "top_positions_long_short",
    "global_long_short", "taker_long_short", "funding_rate",
    "hour_sin", "hour_cos", "day_sin", "day_cos",
]
BARRIERS = (
    # (take-profit ATR, stop-loss ATR, maximum holding hours)
    (1.5, 1.0, 6),
    (2.0, 1.25, 12),
    (2.5, 1.5, 24),
)
QUANTILES = (0.50, 0.60, 0.70, 0.80, 0.90)


def _load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["ts"], unit="ms", utc=True).dt.tz_localize(None)
    return frame.set_index("ts").sort_index()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / length, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def _adx(frame: pd.DataFrame, atr: pd.Series, length: int = 14) -> pd.Series:
    up = frame.high.diff()
    down = -frame.low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def _market(path: Path) -> pd.DataFrame:
    raw = _frame(_load(path)["primary"])
    return raw.resample("1h", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna()


def _flow(path: Path, prefix: str) -> pd.DataFrame:
    raw = _frame(_load(path)["rows"])
    raw["taker_buy_base"] = pd.to_numeric(raw.taker_buy_base)
    hourly = raw.resample("1h", label="left", closed="left").agg(
        close=("close", "last"), volume=("volume", "sum"),
        taker_buy_base=("taker_buy_base", "sum"),
    ).dropna()
    hourly[f"{prefix}_imb"] = (2 * hourly.taker_buy_base - hourly.volume) / hourly.volume
    hourly[f"{prefix}_imb_3"] = hourly[f"{prefix}_imb"].rolling(3).mean()
    hourly[f"{prefix}_imb_12"] = hourly[f"{prefix}_imb"].rolling(12).mean()
    hourly[f"{prefix}_volume_ratio"] = hourly.volume / hourly.volume.shift(1).rolling(24).mean()
    return hourly.rename(columns={"close": f"{prefix}_close"})[[
        f"{prefix}_close", f"{prefix}_imb", f"{prefix}_imb_3",
        f"{prefix}_imb_12", f"{prefix}_volume_ratio",
    ]]


def build_dataset(primary: Path, spot: Path, futures: Path, derivatives: Path) -> pd.DataFrame:
    frame = _market(primary)
    previous_close = frame.close.shift(1)
    true_range = pd.concat([
        frame.high - frame.low,
        (frame.high - previous_close).abs(),
        (frame.low - previous_close).abs(),
    ], axis=1).max(axis=1)
    frame["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    ema20 = frame.close.ewm(span=20, adjust=False).mean()
    ema50 = frame.close.ewm(span=50, adjust=False).mean()
    ema200 = frame.close.ewm(span=200, adjust=False).mean()
    macd = frame.close.ewm(span=12, adjust=False).mean() - frame.close.ewm(span=26, adjust=False).mean()
    for bars in (1, 3, 6, 24, 72):
        frame[f"return_{bars}"] = frame.close.pct_change(bars, fill_method=None)
    frame["atr_pct"] = frame.atr / frame.close
    frame["atr_ratio_24"] = frame.atr / frame.atr.shift(1).rolling(24).mean()
    frame["volume_ratio"] = frame.volume / frame.volume.shift(1).rolling(24).mean()
    frame["ema20_50_atr"] = (ema20 - ema50) / frame.atr
    frame["ema50_200_atr"] = (ema50 - ema200) / frame.atr
    frame["close_ema20_atr"] = (frame.close - ema20) / frame.atr
    frame["rsi"] = _rsi(frame.close)
    frame["adx"] = _adx(frame, frame.atr)
    frame["macd_atr"] = macd / frame.atr
    frame["body_atr"] = (frame.close - frame.open) / frame.atr
    frame["lower_wick_atr"] = (frame[["open", "close"]].min(axis=1) - frame.low) / frame.atr
    frame["upper_wick_atr"] = (frame.high - frame[["open", "close"]].max(axis=1)) / frame.atr
    frame["range_atr"] = (frame.high - frame.low) / frame.atr

    spot_frame, futures_frame = _flow(spot, "spot"), _flow(futures, "fut")
    frame = frame.join(spot_frame).join(futures_frame)
    frame["basis_pct"] = frame.fut_close / frame.spot_close - 1

    context = _load(derivatives)
    metrics = _frame(context["metrics"]).resample("1h").last()
    for bars in (1, 4, 24):
        metrics[f"oi_change_{bars}"] = metrics.open_interest.pct_change(bars, fill_method=None)
    frame = frame.join(metrics[[
        "oi_change_1", "oi_change_4", "oi_change_24",
        "top_accounts_long_short", "top_positions_long_short",
        "global_long_short", "taker_long_short",
    ]])
    funding = _frame(context["funding"])[["funding_rate"]]
    frame = pd.merge_asof(
        frame.reset_index().sort_values("ts"), funding.reset_index().sort_values("ts"),
        on="ts", direction="backward",
    ).set_index("ts")
    hour = frame.index.hour
    frame["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    frame["day_sin"] = np.sin(2 * np.pi * frame.index.dayofweek / 7)
    frame["day_cos"] = np.cos(2 * np.pi * frame.index.dayofweek / 7)
    frame["next_open"] = frame.open.shift(-1)
    return frame.replace([np.inf, -np.inf], np.nan)


@dataclass(frozen=True)
class Outcomes:
    net: pd.Series
    exit_at: pd.Series


def outcomes(frame: pd.DataFrame, side: str, tp_atr: float, sl_atr: float,
             hold_hours: int, cost_pct: float) -> Outcomes:
    high, low, close = frame.high.to_numpy(), frame.low.to_numpy(), frame.close.to_numpy()
    entry = frame.open.shift(-1).to_numpy()
    atr = frame.atr.to_numpy()
    net = np.full(len(frame), np.nan)
    exit_at = np.full(len(frame), np.datetime64("NaT"), dtype="datetime64[ns]")
    times = frame.index.to_numpy(dtype="datetime64[ns]")
    for i in range(len(frame) - hold_hours - 1):
        price, unit = entry[i], atr[i]
        start, end = i + 1, i + hold_hours + 1
        if side == "LONG":
            tp_price, sl_price = price + tp_atr * unit, price - sl_atr * unit
            take = np.flatnonzero(high[start:end] >= tp_price)
            stop = np.flatnonzero(low[start:end] <= sl_price)
        else:
            tp_price, sl_price = price - tp_atr * unit, price + sl_atr * unit
            take = np.flatnonzero(low[start:end] <= tp_price)
            stop = np.flatnonzero(high[start:end] >= sl_price)
        take_i = int(take[0]) if len(take) else hold_hours + 1
        stop_i = int(stop[0]) if len(stop) else hold_hours + 1
        # Adverse-first if both barriers occur inside the same 1h candle.
        if take_i < stop_i:
            gross = abs(tp_price / price - 1) * 100
            offset = take_i
        elif stop_i <= hold_hours:
            gross = -abs(sl_price / price - 1) * 100
            offset = stop_i
        else:
            exit_price = close[end - 1]
            gross = (exit_price / price - 1) * 100 if side == "LONG" else (price / exit_price - 1) * 100
            offset = hold_hours - 1
        net[i] = gross - cost_pct
        exit_at[i] = times[start + offset] + np.timedelta64(1, "h")
    return Outcomes(pd.Series(net, index=frame.index), pd.Series(exit_at, index=frame.index))


def simulate(frame: pd.DataFrame, long_prediction: pd.Series, short_prediction: pd.Series,
             threshold: float | pd.Series, long_outcome: Outcomes, short_outcome: Outcomes,
             sl_atr: float, direction_mode: str = "predicted") -> dict:
    ranked = frame.assign(long_prediction=long_prediction, short_prediction=short_prediction)
    ranked["entry_price"] = frame.next_open
    if isinstance(threshold, pd.Series):
        ranked["threshold"] = threshold.reindex(ranked.index)
    else:
        ranked["threshold"] = threshold
    ranked["prediction"] = ranked[["long_prediction", "short_prediction"]].max(axis=1)
    ranked = ranked[ranked.prediction >= ranked.threshold]
    trades, available_at = [], None
    for ts, row in ranked.iterrows():
        entry_at = ts + pd.Timedelta(hours=1)
        if available_at is not None and entry_at < available_at:
            continue
        side = "LONG" if row.long_prediction >= row.short_prediction else "SHORT"
        if direction_mode.startswith("contrarian"):
            side = "SHORT" if side == "LONG" else "LONG"
        if direction_mode == "contrarian_trend":
            aligned = (side == "LONG" and row.ema50_200_atr >= 0) or (side == "SHORT" and row.ema50_200_atr < 0)
            if not aligned:
                continue
        elif direction_mode == "contrarian_countertrend":
            aligned = (side == "LONG" and row.ema50_200_atr < 0) or (side == "SHORT" and row.ema50_200_atr >= 0)
            if not aligned:
                continue
        selected = long_outcome if side == "LONG" else short_outcome
        value, trade_exit = selected.net.loc[ts], selected.exit_at.loc[ts]
        if pd.isna(value) or pd.isna(trade_exit):
            continue
        stop_pct = sl_atr * float(row.atr) / float(row.entry_price) * 100
        trades.append((float(value), side, stop_pct))
        available_at = trade_exit
    weeks = max((frame.index[-1] - frame.index[0]).total_seconds() / (7 * 86400), 1 / 7)
    if not trades:
        return {"n": 0, "excursions_per_week": 0.0, "net_portfolio_pct": 0.0,
                "profit_factor": None, "max_drawdown_pct": 0.0}
    values = np.array([item[0] for item in trades])
    gains, losses = values[values > 0].sum(), abs(values[values < 0].sum())
    # 0.5% equity risk at the initial stop; capital fraction is naturally < 1 here.
    risk_fraction = 0.005
    portfolio_returns = np.array([value / stop_pct * risk_fraction for value, _, stop_pct in trades])
    equity = np.cumprod(1 + portfolio_returns)
    peaks = np.maximum.accumulate(np.r_[1.0, equity])
    drawdown = (np.r_[1.0, equity] / peaks - 1) * 100
    return {
        "n": len(trades), "excursions_per_week": round(len(trades) / weeks, 4),
        "win_rate_pct": round(float((values > 0).mean() * 100), 4),
        "mean_net_return_pct": round(float(values.mean()), 6),
        "sum_net_return_pct": round(float(values.sum()), 6),
        "net_portfolio_pct": round(float((equity[-1] - 1) * 100), 6),
        "profit_factor": round(float(gains / losses), 6) if losses else None,
        "max_drawdown_pct": round(float(abs(drawdown.min())), 6),
        "long_trades": sum(side == "LONG" for _, side, _ in trades),
        "short_trades": sum(side == "SHORT" for _, side, _ in trades),
    }


def model() -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(
        objective="huber", n_estimators=350, learning_rate=0.025,
        num_leaves=15, max_depth=4, min_child_samples=150,
        subsample=0.8, colsample_bytree=0.75, reg_lambda=4.0,
        random_state=42, verbosity=-1,
    )


def walk_forward_predictions(usable: pd.DataFrame, outcome: Outcomes, side: str,
                             prediction_start: pd.Timestamp, hold_hours: int,
                             sl_atr: float, lookback_days: int = 365) -> pd.Series:
    """Retrain monthly using only labels whose maximum horizon has elapsed."""
    result = pd.Series(np.nan, index=usable.index, dtype=float)
    stop_pct = sl_atr * usable.atr / usable.open.shift(-1) * 100
    risk_normalized_target = outcome.net / stop_pct
    boundaries = pd.date_range(prediction_start.normalize(), usable.index[-1] + pd.offsets.MonthBegin(1), freq="MS")
    if not len(boundaries) or boundaries[0] > prediction_start:
        boundaries = boundaries.insert(0, prediction_start)
    elif boundaries[0] < prediction_start:
        boundaries = boundaries[boundaries >= prediction_start].insert(0, prediction_start)
    for period_start, period_end in zip(boundaries[:-1], boundaries[1:]):
        purge_before = period_start - pd.Timedelta(hours=hold_hours + 1)
        fit_start = purge_before - pd.Timedelta(days=lookback_days)
        fit_mask = (
            (usable.index >= fit_start) & (usable.index < purge_before)
            & risk_normalized_target.notna()
        )
        predict_mask = (usable.index >= period_start) & (usable.index < period_end)
        if fit_mask.sum() < 2000 or not predict_mask.any():
            continue
        estimator = model()
        estimator.fit(usable.loc[fit_mask, FEATURES], risk_normalized_target.loc[fit_mask])
        result.loc[predict_mask] = estimator.predict(usable.loc[predict_mask, FEATURES])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", required=True)
    parser.add_argument("--spot-flow", required=True)
    parser.add_argument("--futures-flow", required=True)
    parser.add_argument("--derivatives", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-cost-pct", type=float, default=0.14)
    parser.add_argument("--stress-cost-pct", type=float, default=0.30)
    args = parser.parse_args()

    data = build_dataset(Path(args.primary), Path(args.spot_flow), Path(args.futures_flow), Path(args.derivatives))
    usable = data.dropna(subset=FEATURES).copy()
    start, end = usable.index[0], usable.index[-1]
    fit_end = start + pd.Timedelta(days=365)
    train_end = fit_end + pd.Timedelta(days=120)
    validation_end = train_end + pd.Timedelta(days=90)
    periods = {
        "train": usable[(usable.index >= fit_end) & (usable.index < train_end)],
        "validation": usable[(usable.index >= train_end) & (usable.index < validation_end)],
        "test": usable[(usable.index >= validation_end) & (usable.index <= end)],
    }
    candidates = []
    for tp_atr, sl_atr, hold_hours in BARRIERS:
        base = {
            side: outcomes(usable, side, tp_atr, sl_atr, hold_hours, args.base_cost_pct)
            for side in ("LONG", "SHORT")
        }
        stress = {
            side: outcomes(usable, side, tp_atr, sl_atr, hold_hours, args.stress_cost_pct)
            for side in ("LONG", "SHORT")
        }
        predictions = {}
        for side in ("LONG", "SHORT"):
            predictions[side] = walk_forward_predictions(
                usable, base[side], side, fit_end, hold_hours,
                sl_atr,
            )
        train_best = pd.concat([predictions["LONG"].reindex(periods["train"].index), predictions["SHORT"].reindex(periods["train"].index)], axis=1).max(axis=1)
        for quantile in QUANTILES:
            # Prediction distributions drift after each monthly refit. A causal
            # rolling percentile is stable across retrains; q alone is selected on train.
            best_score = pd.concat([predictions["LONG"], predictions["SHORT"]], axis=1).max(axis=1)
            threshold = best_score.rolling(24 * 30, min_periods=24 * 14).quantile(quantile).shift(1)
            for direction_mode in (
                "predicted", "contrarian", "contrarian_trend",
                "contrarian_countertrend",
            ):
                metrics = {
                    name: simulate(frame, predictions["LONG"].reindex(frame.index), predictions["SHORT"].reindex(frame.index), threshold, base["LONG"], base["SHORT"], sl_atr, direction_mode)
                    for name, frame in periods.items()
                }
                stress_metrics = {
                    name: simulate(frame, predictions["LONG"].reindex(frame.index), predictions["SHORT"].reindex(frame.index), threshold, stress["LONG"], stress["SHORT"], sl_atr, direction_mode)
                    for name, frame in periods.items()
                }
                candidates.append({
                    "tp_atr": tp_atr, "sl_atr": sl_atr, "hold_hours": hold_hours,
                    "threshold_quantile": quantile, "threshold": "causal_30d_rolling_quantile",
                    "direction_mode": direction_mode,
                    "metrics": metrics, "stress_metrics": stress_metrics,
                })

    eligible = [candidate for candidate in candidates if candidate["metrics"]["train"]["excursions_per_week"] >= 2 and candidate["metrics"]["train"]["net_portfolio_pct"] > 0]
    selected = max(eligible, key=lambda item: (item["metrics"]["train"]["net_portfolio_pct"], item["metrics"]["train"]["profit_factor"] or 0)) if eligible else None
    passed = False
    if selected:
        passed = all(
            selected[group][period]["excursions_per_week"] >= 2
            and selected[group][period]["net_portfolio_pct"] > 0
            and (selected[group][period]["profit_factor"] or 0) > 1
            for group in ("metrics", "stress_metrics") for period in periods
        )
    output = {
        "contract": {
            "selection": "barrier and threshold on train only; validation/test are gates",
            "signal_timeframe": "1h completed candle", "entry": "next 1h open",
            "same_bar_ambiguity": "stop first", "single_concurrent_position": True,
            "risk_per_excursion_pct": 0.5, "minimum_excursions_per_week": 2,
            "base_cost_pct": args.base_cost_pct, "stress_cost_pct": args.stress_cost_pct,
        },
        "dataset": {
            "start": str(start), "fit_end": str(fit_end), "train_end": str(train_end),
            "validation_end": str(validation_end), "end": str(end),
            "rows": len(usable), "period_rows": {key: len(value) for key, value in periods.items()},
        },
        "selected": selected, "passed": passed, "candidates": candidates,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": output["dataset"], "selected": selected, "passed": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
