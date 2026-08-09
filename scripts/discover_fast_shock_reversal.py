"""Discover causal 1h liquidation-shock reversal entries."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.discover_fast_champion import _aggregate, _load


SHOCK_ATR = (1.0, 1.5, 2.0, 2.5)
VOLUME_RATIO = (1.0, 1.5, 2.0)
CONFIRMATION = (False, True)
TP_ATR = (1.0, 1.5, 2.0)
SL_ATR = (1.0, 1.5, 2.0)
HOLD_HOURS = (6, 12, 24)


def features(bars):
    out = bars.copy()
    previous = out.close.shift(1)
    tr = pd.concat([
        out.high - out.low, (out.high - previous).abs(),
        (out.low - previous).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["shock_atr"] = (out.close - out.open) / out.atr
    out["volume_ratio"] = out.volume / out.volume.shift(1).rolling(24).mean()
    return out


def replay(frame, start, end, shock_atr, volume_ratio, confirmation,
           tp_atr, sl_atr, hold_hours, cost):
    rows = list(frame[(frame.index >= start) & (frame.index < end)].itertuples())
    trades, position, pending = [], None, None

    def close(ts, price, reason):
        nonlocal position
        side = position["side"]
        gross = ((price / position["entry_price"] - 1) if side == "LONG"
                 else (position["entry_price"] / price - 1)) * 100
        trades.append({**position, "exit_ts": ts, "exit_price": float(price),
                       "exit_reason": reason, "net_return_pct": gross - cost})
        position = None

    for i, row in enumerate(rows):
        if pending and len(pending) == 5 and pending[4] == "FILL_NEXT" and position is None:
            side, signal_atr = pending[0], pending[1]
            entry = float(row.open)
            position = {
                "side": side, "entry_ts": row.Index, "entry_price": entry,
                "stop": entry - sl_atr * signal_atr if side == "LONG" else entry + sl_atr * signal_atr,
                "take": entry + tp_atr * signal_atr if side == "LONG" else entry - tp_atr * signal_atr,
            }
            pending = None
        elif pending and position is None:
            side, signal_atr, needs_confirmation, shock_close = pending
            confirmed = not needs_confirmation or (
                (side == "LONG" and row.close > row.open and row.close > shock_close)
                or (side == "SHORT" and row.close < row.open and row.close < shock_close)
            )
            if confirmed:
                # Confirmation uses this completed candle; fill at the next open.
                if needs_confirmation and i + 1 < len(rows):
                    pending = (side, signal_atr, False, None, "FILL_NEXT")
                else:
                    entry = float(row.open)
                    position = {
                        "side": side, "entry_ts": row.Index, "entry_price": entry,
                        "stop": entry - sl_atr * signal_atr if side == "LONG" else entry + sl_atr * signal_atr,
                        "take": entry + tp_atr * signal_atr if side == "LONG" else entry - tp_atr * signal_atr,
                    }
                    pending = None
            else:
                pending = None
        if position:
            stop_hit = row.low <= position["stop"] if position["side"] == "LONG" else row.high >= position["stop"]
            take_hit = row.high >= position["take"] if position["side"] == "LONG" else row.low <= position["take"]
            if stop_hit:  # adverse-first within an ambiguous 1h candle
                close(row.Index, position["stop"], "STOP")
            elif take_hit:
                close(row.Index, position["take"], "TAKE_PROFIT")
            elif (row.Index - position["entry_ts"]) / pd.Timedelta(hours=1) >= hold_hours:
                close(row.Index, row.close, "TIMEOUT")
        if i + 1 >= len(rows) or position or pending or pd.isna(row.atr) or pd.isna(row.volume_ratio):
            continue
        if row.volume_ratio >= volume_ratio and abs(row.shock_atr) >= shock_atr:
            side = "LONG" if row.shock_atr < 0 else "SHORT"
            pending = (side, float(row.atr), confirmation, float(row.close))
    if position and rows:
        close(rows[-1].Index, rows[-1].close, "SEGMENT_END")
    return trades


def metrics(trades, start, end, robust=False):
    values = np.array([trade["net_return_pct"] for trade in trades])
    weeks = (end - start) / pd.Timedelta(days=7)
    result = {"n": len(values), "excursions_per_week": round(len(values) / weeks, 6)}
    if not len(values):
        return {**result, "sum_net_return_pct": 0.0, "profit_factor": None,
                "mean_net_return_pct": None, "win_rate_pct": None}
    gain, loss = values[values > 0].sum(), abs(values[values < 0].sum())
    result.update({
        "sum_net_return_pct": round(float(values.sum()), 6),
        "mean_net_return_pct": round(float(values.mean()), 6),
        "profit_factor": round(float(gain / loss), 6) if loss else None,
        "win_rate_pct": round(float((values > 0).mean() * 100), 4),
    })
    if robust:
        quarters = []
        cursor = start
        while cursor < end:
            finish = min(cursor + pd.DateOffset(months=3), end)
            quarters.append(sum(t["net_return_pct"] for t in trades if cursor <= t["exit_ts"] < finish))
            cursor = finish
        result["positive_quarters"] = sum(value > 0 for value in quarters)
        result["quarter_count"] = len(quarters)
    return result


def gate(metric, train=False):
    return (metric["excursions_per_week"] >= 2 and metric["sum_net_return_pct"] > 0
            and (metric["profit_factor"] or 0) > (1.1 if train else 1)
            and (not train or metric["positive_quarters"] >= np.ceil(metric["quarter_count"] * .6)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-cost-pct", type=float, default=.14)
    parser.add_argument("--stress-cost-pct", type=float, default=.30)
    args = parser.parse_args()
    bars = features(_aggregate(_load(args.flow_cache), "1h"))
    splits = {
        "train": (pd.Timestamp("2023-08-07"), pd.Timestamp("2025-08-07")),
        "validation": (pd.Timestamp("2025-08-07"), pd.Timestamp("2026-02-07")),
        "test": (pd.Timestamp("2026-02-07"), pd.Timestamp("2026-08-07")),
    }
    grid = []
    for shock in SHOCK_ATR:
        for volume in VOLUME_RATIO:
            for confirmation in CONFIRMATION:
                for tp in TP_ATR:
                    for sl in SL_ATR:
                        for hold in HOLD_HOURS:
                            params = (shock, volume, confirmation, tp, sl, hold)
                            train_trades = replay(bars, *splits["train"], *params, args.base_cost_pct)
                            grid.append({
                                "shock_atr": shock, "volume_ratio": volume,
                                "confirmation": confirmation, "tp_atr": tp,
                                "sl_atr": sl, "hold_hours": hold,
                                "metrics": {"train": metrics(train_trades, *splits["train"], True)},
                            })
    eligible = [candidate for candidate in grid if gate(candidate["metrics"]["train"], True)]
    selected = max(eligible, key=lambda x: (x["metrics"]["train"]["sum_net_return_pct"], x["metrics"]["train"]["profit_factor"])) if eligible else None
    stress = {}
    if selected:
        params = tuple(selected[key] for key in ("shock_atr", "volume_ratio", "confirmation", "tp_atr", "sl_atr", "hold_hours"))
        for name in ("validation", "test"):
            selected["metrics"][name] = metrics(replay(bars, *splits[name], *params, args.base_cost_pct), *splits[name])
        for name, bounds in splits.items():
            stress[name] = metrics(replay(bars, *bounds, *params, args.stress_cost_pct), *bounds)
    passed = bool(selected and all(gate(selected["metrics"][name]) for name in ("validation", "test")) and all(gate(stress[name]) for name in splits))
    output = {"passed": passed, "contract": {"selection": "train only", "same_bar": "stop first", "base_cost_pct": args.base_cost_pct, "stress_cost_pct": args.stress_cost_pct}, "grid_size": len(grid), "eligible_count": len(eligible), "selected": selected, "cost_stress": stress, "grid": grid}
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "grid_size", "eligible_count", "selected", "cost_stress")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
