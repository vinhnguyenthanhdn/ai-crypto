"""Discovery Fast Champion family Donchian breakout hai phía."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.discover_fast_champion import (  # noqa: E402
    BASE_COST_PCT, MIN_EXCURSIONS_PER_WEEK, RISK_PER_EXCURSION_PCT,
    STRESS_COST_PCT, _aggregate, _load,
)


TIMEFRAMES = ("15min", "30min", "1h")
ENTRY_WINDOWS = (20, 40, 80)
EXIT_WINDOWS = (10, 20)
STOP_ATR = (2.0, 3.0)
MAX_HOLD_HOURS = (12, 24, 72)
CONTEXT_FILTERS = ("none", "volatility", "taker_confirm", "taker_strong", "volume_taker_confirm")


def _features(bars, entry_window, exit_window):
    out = bars.copy()
    prior = out.close.shift(1)
    tr = pd.concat([
        out.high - out.low, (out.high - prior).abs(), (out.low - prior).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["ema"] = out.close.ewm(span=200, adjust=False).mean()
    out["entry_high"] = out.high.shift(1).rolling(entry_window).max()
    out["entry_low"] = out.low.shift(1).rolling(entry_window).min()
    out["exit_high"] = out.high.shift(1).rolling(exit_window).max()
    out["exit_low"] = out.low.shift(1).rolling(exit_window).min()
    out["atr_pct"] = out.atr / out.close
    out["atr_median"] = out.atr_pct.shift(1).rolling(200).median()
    out["volume_median"] = out.volume.shift(1).rolling(200).median()
    return out


def _run(frame, start, end, stop_atr, max_hold_hours, context_filter, cost):
    rows = list(frame[(frame.index >= start) & (frame.index < end)].itertuples())
    trades, position, pending_entry, pending_exit = [], None, None, None

    def close(timestamp, price, reason):
        nonlocal position
        gross = (
            (price / position["entry_price"] - 1) * 100
            if position["side"] == "LONG"
            else (position["entry_price"] / price - 1) * 100
        )
        trades.append({
            **position, "exit_ts": timestamp, "exit_price": float(price),
            "exit_reason": reason, "net_return_pct": gross - cost,
        })
        position = None

    for i, row in enumerate(rows):
        if pending_exit and position is not None:
            close(row.Index, row.open, pending_exit)
        pending_exit = None
        if pending_entry and position is None:
            side, signal_atr = pending_entry
            stop = row.open - stop_atr * signal_atr if side == "LONG" else row.open + stop_atr * signal_atr
            position = {
                "side": side, "entry_ts": row.Index, "entry_price": float(row.open),
                "stop": float(stop), "excursion_id": len(trades) + 1,
            }
        pending_entry = None

        if position is not None:
            stopped = row.low <= position["stop"] if position["side"] == "LONG" else row.high >= position["stop"]
            if stopped:
                close(row.Index, position["stop"], "INITIAL_STOP")
            else:
                held_hours = (row.Index - position["entry_ts"]) / pd.Timedelta(hours=1)
                channel_exit = (
                    row.close <= row.exit_low if position["side"] == "LONG"
                    else row.close >= row.exit_high
                )
                if channel_exit or held_hours >= max_hold_hours:
                    pending_exit = "CHANNEL_EXIT" if channel_exit else "TIMEOUT_EXIT"

        if i + 1 >= len(rows) or position is not None or pending_exit:
            continue
        if any(pd.isna(v) for v in (row.atr, row.entry_high, row.entry_low, row.atr_median)):
            continue
        if callable(context_filter):
            long_context = bool(context_filter(row, "LONG"))
            short_context = bool(context_filter(row, "SHORT"))
        else:
            long_context = (
            context_filter == "none"
            or (context_filter == "volatility" and row.atr_pct >= row.atr_median)
            or (context_filter == "taker_confirm" and row.taker_imbalance > 0)
            or (context_filter == "taker_strong" and row.taker_imbalance > 0.10)
            or (context_filter == "volume_taker_confirm" and row.taker_imbalance > 0 and row.volume >= row.volume_median)
            )
            short_context = (
            context_filter == "none"
            or (context_filter == "volatility" and row.atr_pct >= row.atr_median)
            or (context_filter == "taker_confirm" and row.taker_imbalance < 0)
            or (context_filter == "taker_strong" and row.taker_imbalance < -0.10)
            or (context_filter == "volume_taker_confirm" and row.taker_imbalance < 0 and row.volume >= row.volume_median)
            )
        if long_context and row.close >= row.entry_high and row.close >= row.ema:
            pending_entry = ("LONG", float(row.atr))
        elif short_context and row.close <= row.entry_low and row.close < row.ema:
            pending_entry = ("SHORT", float(row.atr))
    if position is not None and rows:
        close(rows[-1].Index, rows[-1].close, "SEGMENT_END")
    return trades


def _metrics(trades, start, end, robustness=False):
    values = []
    for trade in trades:
        stop_pct = abs(trade["stop"] / trade["entry_price"] - 1) * 100
        fraction = min(1.0, RISK_PER_EXCURSION_PCT / stop_pct)
        values.append((pd.Timestamp(trade["exit_ts"]), trade["net_return_pct"] * fraction))
    raw = np.asarray([value for _, value in values])
    gains = raw[raw > 0].sum() if len(raw) else 0
    losses = abs(raw[raw < 0].sum()) if len(raw) else 0
    equity = np.r_[1.0, np.cumprod(1 + raw / 100)]
    dd = equity / np.maximum.accumulate(equity) - 1
    weeks = (pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta(days=7)
    result = {
        "tickets": len(trades), "excursions": len(trades),
        "excursions_per_week": round(len(trades) / weeks, 6),
        "net_return_pct": round(float((equity[-1] - 1) * 100), 6),
        "profit_factor": round(float(gains / losses), 6) if losses else None,
        "max_drawdown_pct": round(float(abs(dd.min()) * 100), 6),
        "win_rate_pct": round(float((raw > 0).mean() * 100), 4) if len(raw) else None,
    }
    if robustness:
        quarters, cursor = [], pd.Timestamp(start)
        while cursor < pd.Timestamp(end):
            window_end = min(cursor + pd.DateOffset(months=3), pd.Timestamp(end))
            chunk = [v for ts, v in values if cursor <= ts < window_end]
            quarters.append(float((np.prod(1 + np.asarray(chunk) / 100) - 1) * 100) if chunk else 0)
            cursor = window_end
        result["quarter_returns_pct"] = [round(v, 6) for v in quarters]
        result["positive_quarters"] = sum(v > 0 for v in quarters)
        result["quarter_count"] = len(quarters)
    return result


def _gate(metric, train=False):
    return (
        metric["net_return_pct"] > 0 and (metric["profit_factor"] or 0) > (1.10 if train else 1)
        and metric["max_drawdown_pct"] <= 10
        and metric["excursions_per_week"] >= MIN_EXCURSIONS_PER_WEEK
        and (not train or metric["positive_quarters"] >= np.ceil(metric["quarter_count"] * .6))
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-cost-pct", type=float, default=BASE_COST_PCT)
    parser.add_argument("--stress-cost-pct", type=float, default=STRESS_COST_PCT)
    args = parser.parse_args()
    source_path = Path(args.flow_cache); source = _load(source_path)
    end = pd.Timestamp("2026-08-07"); start = end - pd.DateOffset(years=3)
    train_end = start + pd.DateOffset(years=2); validation_end = train_end + pd.DateOffset(months=6)
    splits = {"train":(start,train_end),"validation":(train_end,validation_end),"test":(validation_end,end)}
    bars = {tf:_aggregate(source,tf) for tf in TIMEFRAMES}
    grid=[]
    for tf in TIMEFRAMES:
      for ew in ENTRY_WINDOWS:
       for xw in EXIT_WINDOWS:
        frame=_features(bars[tf],ew,xw)
        for stop in STOP_ATR:
         for hold in MAX_HOLD_HOURS:
          for context_filter in CONTEXT_FILTERS:
           trades=_run(frame,*splits['train'],stop,hold,context_filter,args.base_cost_pct)
           grid.append({"timeframe":tf,"entry_window":ew,"exit_window":xw,"stop_atr":stop,"max_hold_hours":hold,"context_filter":context_filter,"metrics":{"train":_metrics(trades,*splits['train'],True)}})
    eligible=[x for x in grid if _gate(x['metrics']['train'],True)]
    selected=max(eligible,key=lambda x:x['metrics']['train']['net_return_pct']) if eligible else None
    stress={}
    if selected:
      frame=_features(bars[selected['timeframe']],selected['entry_window'],selected['exit_window'])
      args2=(selected['stop_atr'],selected['max_hold_hours'],selected['context_filter'])
      for name in ('validation','test'):
       selected['metrics'][name]=_metrics(_run(frame,*splits[name],*args2,args.base_cost_pct),*splits[name])
      for name,bounds in splits.items(): stress[name]=_metrics(_run(frame,*bounds,*args2,args.stress_cost_pct),*bounds)
    passed=bool(selected and all(_gate(selected['metrics'][x]) for x in ('validation','test')) and all(_gate(stress[x]) for x in splits))
    output={"passed":passed,"contract":{"selection":"train_only","risk_per_excursion_pct":RISK_PER_EXCURSION_PCT,"min_excursions_per_week":MIN_EXCURSIONS_PER_WEEK,"base_cost_pct":args.base_cost_pct,"stress_cost_pct":args.stress_cost_pct},"dataset":{"source":str(source_path),"sha256":hashlib.sha256(source_path.read_bytes()).hexdigest(),"start":str(start),"train_end":str(train_end),"validation_end":str(validation_end),"end":str(end)},"grid_size":len(grid),"train_eligible_count":len(eligible),"selected":selected,"cost_stress":stress,"grid":grid}
    Path(args.out).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:output[k] for k in ('passed','grid_size','train_eligible_count','selected','cost_stress')},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
