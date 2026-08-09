"""Discover a conservative post-only maker strategy from 1-second BBO data."""
import argparse
import gzip
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd


IMBALANCE = (.80, .90)
SMOOTH_SECONDS = (15, 60)
LIMIT_OFFSET_BPS = (10, 20)
ORDER_TTL_SECONDS = (300, 900)
TP_BPS = (25, 50)
SL_BPS = (30, 50)
MAX_HOLD_SECONDS = (900, 3600)
DIRECTION_MODE = ("momentum",)
MAX_TRADES_PER_DAY = (2, 4)
MAX_LEVERAGE = 3.0
RISK_PER_TRADE_PCT = .5


def load_day(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"], columns=raw["columns"])
    frame["second"] = (frame.ts // 1000).astype(np.int64)
    frame["imbalance"] = (frame.bid_qty - frame.ask_qty) / (frame.bid_qty + frame.ask_qty)
    return frame


def replay(frame, imbalance, smooth_seconds, offset_bps, ttl, tp_bps,
           sl_bps, max_hold, direction_mode, max_trades_per_day,
           target_cost_pct, stop_cost_pct):
    bid = frame.bid.to_numpy(); ask = frame.ask.to_numpy(); seconds = frame.second.to_numpy()
    smooth = frame.imbalance.rolling(smooth_seconds, min_periods=smooth_seconds).mean().to_numpy()
    trades = []
    order = position = None
    previous_second = None
    for i in range(len(frame)):
        now = int(seconds[i])
        if previous_second is not None and now - previous_second > 5:
            order = None
            if position:
                # Missing BBO makes fill ordering unknowable: force adverse exit.
                price = bid[i] if position["side"] == "LONG" else ask[i]
                gross = ((price / position["entry"] - 1) if position["side"] == "LONG" else (position["entry"] / price - 1)) * 100
                trades.append({"net": gross - stop_cost_pct, "reason": "DATA_GAP", "side": position["side"]})
                position = None
        previous_second = now
        if order:
            filled = ask[i] <= order["limit"] if order["side"] == "LONG" else bid[i] >= order["limit"]
            if filled:
                position = {"side": order["side"], "entry": order["limit"], "filled_at": now}
                order = None
            elif now - order["created_at"] >= ttl:
                order = None
        if position:
            side, entry = position["side"], position["entry"]
            take = entry * (1 + tp_bps / 10000) if side == "LONG" else entry * (1 - tp_bps / 10000)
            stop = entry * (1 - sl_bps / 10000) if side == "LONG" else entry * (1 + sl_bps / 10000)
            stop_hit = bid[i] <= stop if side == "LONG" else ask[i] >= stop
            take_hit = bid[i] >= take if side == "LONG" else ask[i] <= take
            timed_out = now - position["filled_at"] >= max_hold
            exit_limit = position.get("exit_limit")
            maker_exit_hit = exit_limit is not None and (
                bid[i] >= exit_limit if side == "LONG" else ask[i] <= exit_limit
            )
            exit_expired = exit_limit is not None and now - position["exit_created_at"] >= 300
            if stop_hit or exit_expired:
                price = bid[i] if side == "LONG" else ask[i]
                gross = ((price / entry - 1) if side == "LONG" else (entry / price - 1)) * 100
                trades.append({"net": gross - stop_cost_pct, "reason": "STOP" if stop_hit else "TIMEOUT_TAKER", "side": side})
                position = None
            elif maker_exit_hit:
                gross = ((exit_limit / entry - 1) if side == "LONG" else (entry / exit_limit - 1)) * 100
                trades.append({"net": gross - target_cost_pct, "reason": "TIMEOUT_MAKER", "side": side})
                position = None
            elif take_hit:
                gross = abs(take / entry - 1) * 100
                trades.append({"net": gross - target_cost_pct, "reason": "TAKE_PROFIT", "side": side})
                position = None
            elif timed_out and exit_limit is None:
                position["exit_limit"] = ask[i] if side == "LONG" else bid[i]
                position["exit_created_at"] = now
        if (len(trades) < max_trades_per_day and order is None and position is None
                and np.isfinite(smooth[i]) and abs(smooth[i]) >= imbalance):
            side = "LONG" if smooth[i] > 0 else "SHORT"
            if direction_mode == "contrarian":
                side = "SHORT" if side == "LONG" else "LONG"
            limit = bid[i] * (1 - offset_bps / 10000) if side == "LONG" else ask[i] * (1 + offset_bps / 10000)
            order = {"side": side, "limit": limit, "created_at": now}
    if position:
        price = bid[-1] if position["side"] == "LONG" else ask[-1]
        gross = ((price / position["entry"] - 1) if position["side"] == "LONG" else (position["entry"] / price - 1)) * 100
        trades.append({"net": gross - stop_cost_pct, "reason": "DAY_END", "side": position["side"]})
    return trades


def metrics(day_trades, sl_bps):
    flattened = [trade for trades in day_trades for trade in trades]
    values = np.array([trade["net"] for trade in flattened])
    leverage = min(MAX_LEVERAGE, RISK_PER_TRADE_PCT / (sl_bps / 100))
    portfolio = values * leverage
    equity = np.cumprod(1 + portfolio / 100) if len(portfolio) else np.array([1.])
    gain = portfolio[portfolio > 0].sum() if len(portfolio) else 0
    loss = abs(portfolio[portfolio < 0].sum()) if len(portfolio) else 0
    day_returns = []
    for trades in day_trades:
        returns = np.array([trade["net"] * leverage for trade in trades])
        day_returns.append(float((np.prod(1 + returns / 100) - 1) * 100) if len(returns) else 0.)
    return {
        "n": len(flattened), "trades_per_active_day": round(len(flattened) / len(day_trades), 4),
        "net_portfolio_pct": round(float((equity[-1] - 1) * 100), 6),
        "profit_factor": round(float(gain / loss), 6) if loss else None,
        "win_rate_pct": round(float((portfolio > 0).mean() * 100), 4) if len(portfolio) else None,
        "day_returns_pct": [round(value, 6) for value in day_returns],
        "positive_days": sum(value > 0 for value in day_returns),
        "take_profit": sum(trade["reason"] == "TAKE_PROFIT" for trade in flattened),
        "stop": sum(trade["reason"] == "STOP" for trade in flattened),
        "timeout": sum(trade["reason"] in ("TIMEOUT_MAKER", "TIMEOUT_TAKER", "DAY_END", "DATA_GAP") for trade in flattened),
        "leverage": leverage,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/backtests/bookticker")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    paths = sorted(Path(args.data_dir).glob("btcusdt_bookticker_1s_*.json.gz"))
    days = {path.name[-18:-8]: load_day(path) for path in paths}
    split_dates = {
        "train": ("2023-05-16", "2023-07-15", "2023-09-15"),
        "validation": ("2023-11-15", "2023-12-15"),
        "test": ("2024-01-15", "2024-03-15"),
    }
    missing = [date for dates in split_dates.values() for date in dates if date not in days]
    if missing:
        raise ValueError(f"Missing compact BBO days: {missing}")
    grid = []
    combinations = itertools.product(IMBALANCE, SMOOTH_SECONDS, LIMIT_OFFSET_BPS,
                                     ORDER_TTL_SECONDS, TP_BPS, SL_BPS,
                                     MAX_HOLD_SECONDS, DIRECTION_MODE,
                                     MAX_TRADES_PER_DAY)
    for params in combinations:
        imbalance, smooth, offset, ttl, tp, sl, hold, mode, max_trades = params
        base = [replay(days[date], *params, .04, .09) for date in split_dates["train"]]
        stress = [replay(days[date], *params, .08, .14) for date in split_dates["train"]]
        base_metric, stress_metric = metrics(base, sl), metrics(stress, sl)
        grid.append({
            "imbalance": imbalance, "smooth_seconds": smooth, "offset_bps": offset,
            "ttl_seconds": ttl, "tp_bps": tp, "sl_bps": sl,
            "max_hold_seconds": hold, "direction_mode": mode,
            "max_trades_per_day": max_trades,
            "metrics": {"train": base_metric}, "stress_metrics": {"train": stress_metric},
        })
    eligible = [item for item in grid if (
        item["metrics"]["train"]["trades_per_active_day"] >= 2 / 7
        and item["metrics"]["train"]["net_portfolio_pct"] > 0
        and (item["metrics"]["train"]["profit_factor"] or 0) > 1.1
        and min(item["metrics"]["train"]["day_returns_pct"]) >= 0
        and item["metrics"]["train"]["positive_days"] >= 2
        and item["stress_metrics"]["train"]["net_portfolio_pct"] > 0
        and min(item["stress_metrics"]["train"]["day_returns_pct"]) >= 0
    )]
    selected = max(eligible, key=lambda item: item["metrics"]["train"]["net_portfolio_pct"]) if eligible else None
    if selected:
        params = tuple(selected[key] for key in ("imbalance", "smooth_seconds", "offset_bps", "ttl_seconds", "tp_bps", "sl_bps", "max_hold_seconds", "direction_mode", "max_trades_per_day"))
        for split in ("validation", "test"):
            selected["metrics"][split] = metrics([replay(days[date], *params, .04, .09) for date in split_dates[split]], selected["sl_bps"])
            selected["stress_metrics"][split] = metrics([replay(days[date], *params, .08, .14) for date in split_dates[split]], selected["sl_bps"])
    passed = bool(selected and all(
        selected[group][split]["trades_per_active_day"] >= 2 / 7
        and selected[group][split]["net_portfolio_pct"] > 0
        and (selected[group][split]["profit_factor"] or 0) > 1
        and min(selected[group][split]["day_returns_pct"]) >= 0
        and selected[group][split]["positive_days"] >= 1
        for group in ("metrics", "stress_metrics") for split in split_dates
    ))
    output = {
        "passed": passed,
        "contract": {
            "selection": "train days only", "fill": "strict BBO trade-through",
            "data_gap": "force adverse exit", "same_second": "stop first",
            "target_cost_pct": {"base": .04, "stress": .08},
            "stop_or_timeout_cost_pct": {"base": .09, "stress": .14},
            "risk_per_trade_pct": RISK_PER_TRADE_PCT, "max_leverage": MAX_LEVERAGE,
        },
        "splits": split_dates, "grid_size": len(grid), "eligible_count": len(eligible),
        "selected": selected, "grid": grid,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "grid_size", "eligible_count", "selected")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
