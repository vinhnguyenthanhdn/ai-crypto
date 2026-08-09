"""Causal event study cho confirmed support touch và touch→reclaim.

Không đặt lệnh, không dùng TP/SL. Mục tiêu là đo xem location/trigger có lift
so với control cùng trend, ATR regime và giờ giao dịch trước khi tối ưu exit.
"""
import argparse
import bisect
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.backtest.engine import WARMUP_BARS  # noqa: E402
from src.data import market  # noqa: E402
from src.engine import risk, support_resistance  # noqa: E402
from src.indicators import technical  # noqa: E402

HORIZONS_MINUTES = (5, 15, 60, 240, 1440)
RECLAIM_WINDOW_MINUTES = 15
RECLAIM_BUFFER_ATR = 0.10


def _load_or_fetch(path: Path, days: int, symbol: str, timeframe: str, tick_timeframe: str):
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    exchange = market.get_exchange()
    raw_primary = market.fetch_historical_ohlcv(exchange, symbol, timeframe, days)
    raw_tick = market.fetch_historical_ohlcv(exchange, symbol, tick_timeframe, days)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "metadata": {
            "symbol": symbol, "market_type": config.MARKET_TYPE,
            "timeframe": timeframe, "tick_timeframe": tick_timeframe,
        },
        "primary": raw_primary, "tick": raw_tick, "funding": None,
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(result, fh, separators=(",", ":"))
    return result


def _closed_only(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    return df[df["ts"] + pd.Timedelta(minutes=minutes) <= now].reset_index(drop=True)


def _path_arrays(tick: pd.DataFrame):
    times, prices = [], []
    step = pd.Timedelta(seconds=15)
    for row in tick.itertuples():
        for j, field in enumerate(("open", "low", "high", "close")):
            times.append(row.ts + step * j)
            prices.append(float(getattr(row, field)))
    return np.asarray(times, dtype="datetime64[ns]"), np.asarray(prices, dtype="float64")


def _forward_metrics(path_ts, path_price, entry_time, entry_price):
    start = int(np.searchsorted(path_ts, np.datetime64(entry_time), side="right"))
    result = {}
    for minutes in HORIZONS_MINUTES:
        end_time = np.datetime64(entry_time + pd.Timedelta(minutes=minutes))
        end = int(np.searchsorted(path_ts, end_time, side="right"))
        window = path_price[start:end]
        if len(window) == 0:
            result[str(minutes)] = None
            continue
        result[str(minutes)] = {
            "forward_return_pct": round((float(window[-1]) / entry_price - 1) * 100, 6),
            "mfe_pct": round((float(window.max()) / entry_price - 1) * 100, 6),
            "mae_pct": round((float(window.min()) / entry_price - 1) * 100, 6),
        }
    return result


def _summarize(rows, cost_pct, hurdle_pct):
    summary = {"n": len(rows), "horizons": {}}
    for minutes in HORIZONS_MINUTES:
        metrics = [row["metrics"].get(str(minutes)) for row in rows]
        metrics = [metric for metric in metrics if metric is not None]
        if not metrics:
            summary["horizons"][str(minutes)] = None
            continue
        fwd = pd.Series([metric["forward_return_pct"] for metric in metrics])
        mfe = pd.Series([metric["mfe_pct"] for metric in metrics])
        mae = pd.Series([metric["mae_pct"] for metric in metrics])
        summary["horizons"][str(minutes)] = {
            "n": len(metrics),
            "forward_mean_pct": round(float(fwd.mean()), 6),
            "forward_median_pct": round(float(fwd.median()), 6),
            "mfe_median_pct": round(float(mfe.median()), 6),
            "mae_median_pct": round(float(mae.median()), 6),
            "p_close_positive_pct": round(float((fwd > 0).mean() * 100), 4),
            "p_close_above_cost_pct": round(float((fwd >= cost_pct).mean() * 100), 4),
            "p_mfe_above_cost_pct": round(float((mfe >= cost_pct).mean() * 100), 4),
            "p_mfe_above_hurdle_pct": round(float((mfe >= hurdle_pct).mean() * 100), 4),
        }
    return summary


def _matched_controls(events, pool):
    buckets = {}
    for row in pool:
        buckets.setdefault(tuple(row["match_key"]), []).append(row)
    for rows in buckets.values():
        rows.sort(key=lambda row: row["ts"])
    used, matched = set(), []
    for event in events:
        event_time = pd.Timestamp(event["ts"])
        rows = buckets.get(tuple(event["match_key"]), [])
        timestamps = [row["ts"] for row in rows]
        pos = bisect.bisect_left(timestamps, event["ts"])
        candidates = []
        for distance in range(len(rows)):
            for idx in (pos - distance - 1, pos + distance):
                if 0 <= idx < len(rows) and id(rows[idx]) not in used:
                    candidate_time = pd.Timestamp(rows[idx]["ts"])
                    if abs(candidate_time - event_time) >= pd.Timedelta(hours=24):
                        candidates.append((abs(candidate_time - event_time), rows[idx]))
            if candidates:
                break
        if candidates:
            chosen = min(candidates, key=lambda item: item[0])[1]
            used.add(id(chosen))
            matched.append({**chosen, "matched_event_id": event["event_id"]})
    return matched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--tick-timeframe", default="1m")
    args = parser.parse_args()

    cache = _load_or_fetch(
        Path(args.dataset_cache), args.days, args.symbol,
        args.timeframe, args.tick_timeframe,
    )
    primary = _closed_only(technical.to_dataframe(cache["primary"]), 5)
    tick = _closed_only(technical.to_dataframe(cache["tick"]), 1)
    enriched = technical.add_indicators(primary)
    path_ts, path_price = _path_arrays(tick)
    atr_pct = enriched["atr"] / enriched["close"] * 100
    enriched["atr_bin"] = pd.qcut(atr_pct, 5, labels=False, duplicates="drop")

    touch_events, reclaim_events, control_pool = [], [], []
    seen_zones = set()
    max_horizon = pd.Timedelta(minutes=max(HORIZONS_MINUTES))
    last_ts = pd.Timestamp(primary["ts"].iloc[-1])
    cost_pct = risk.round_trip_cost_pct()
    hurdle_pct = cost_pct * config.MIN_TP_COST_RATIO

    for i in range(WARMUP_BARS, len(enriched) - 1):
        bar = enriched.iloc[i]
        bar_time = pd.Timestamp(bar["ts"])
        if bar_time + max_horizon > last_ts:
            break
        trend = "UP" if bar.get("ema20", 0) > bar.get("ema50", 0) else "DOWN"
        atr_bin = int(bar["atr_bin"]) if not pd.isna(bar["atr_bin"]) else -1
        match_key = [trend, atr_bin, int(bar_time.hour // 4)]
        zone = support_resistance.find_active_zone(
            enriched, kind="low", decision_idx=i - 1,
            required_swings=config.SR_REQUIRED_SWINGS,
        )
        touched = bool(
            zone and float(bar["open"]) >= zone.low
            and float(bar["low"]) <= zone.high and float(bar["high"]) >= zone.low
        )
        if not touched:
            entry_price = float(bar["open"])
            control_pool.append({
                "ts": str(bar_time), "entry_price": entry_price,
                "match_key": match_key,
                "metrics": _forward_metrics(path_ts, path_price, bar_time, entry_price),
            })
            continue

        zone_key = tuple(swing.idx for swing in zone.swings)
        if zone_key in seen_zones:
            continue
        seen_zones.add(zone_key)
        entry_price = min(float(bar["open"]), zone.high)
        bar_end = bar_time + pd.Timedelta(minutes=5)
        lo = int(np.searchsorted(path_ts, np.datetime64(bar_time), side="left"))
        hi = int(np.searchsorted(path_ts, np.datetime64(bar_end), side="left"))
        touch_candidates = np.flatnonzero(path_price[lo:hi] <= zone.high)
        if len(touch_candidates) == 0:
            continue
        touch_time = pd.Timestamp(path_ts[lo + int(touch_candidates[0])])
        event_id = len(touch_events)
        event = {
            "event_id": event_id,
            "ts": str(touch_time), "entry_price": entry_price,
            "zone": zone.to_dict(), "atr": round(float(bar["atr"]), 8),
            "match_key": match_key,
            "metrics": _forward_metrics(path_ts, path_price, touch_time, entry_price),
        }
        touch_events.append(event)

        reclaim_end = touch_time + pd.Timedelta(minutes=RECLAIM_WINDOW_MINUTES)
        rows = tick[(tick["ts"] >= touch_time.floor("min")) & (tick["ts"] <= reclaim_end)]
        reclaim_level = zone.high + RECLAIM_BUFFER_ATR * float(bar["atr"])
        reclaimed = rows[(rows["close"] >= reclaim_level) & (rows["close"] > rows["open"])]
        if not reclaimed.empty:
            reclaim = reclaimed.iloc[0]
            reclaim_time = pd.Timestamp(reclaim["ts"]) + pd.Timedelta(seconds=45)
            reclaim_price = float(reclaim["close"])
            reclaim_events.append({
                "parent_event_id": event_id,
                "ts": str(reclaim_time), "touch_ts": str(touch_time),
                "entry_price": reclaim_price, "reclaim_level": round(reclaim_level, 8),
                "zone": zone.to_dict(), "atr": round(float(bar["atr"]), 8),
                "match_key": match_key,
                "metrics": _forward_metrics(path_ts, path_price, reclaim_time, reclaim_price),
            })

    controls = _matched_controls(touch_events, control_pool)
    reclaim_ids = {event["parent_event_id"] for event in reclaim_events}
    reclaim_controls = [
        control for control in controls if control["matched_event_id"] in reclaim_ids
    ]
    summaries = {
        "touch": _summarize(touch_events, cost_pct, hurdle_pct),
        "reclaim": _summarize(reclaim_events, cost_pct, hurdle_pct),
        "touch_control": _summarize(controls, cost_pct, hurdle_pct),
        "reclaim_control": _summarize(reclaim_controls, cost_pct, hurdle_pct),
    }
    for kind in ("touch", "reclaim"):
        control_kind = f"{kind}_control"
        for minutes in HORIZONS_MINUTES:
            candidate = summaries[kind]["horizons"].get(str(minutes))
            control = summaries[control_kind]["horizons"].get(str(minutes))
            if candidate and control:
                candidate["lift_vs_control"] = {
                    "forward_mean_pct": round(candidate["forward_mean_pct"] - control["forward_mean_pct"], 6),
                    "p_mfe_above_hurdle_pct": round(candidate["p_mfe_above_hurdle_pct"] - control["p_mfe_above_hurdle_pct"], 4),
                }

    output = {
        "contract": {
            "symbol": args.symbol, "timeframe": args.timeframe,
            "tick_timeframe": args.tick_timeframe,
            "required_swings": config.SR_REQUIRED_SWINGS,
            "touch": "first touch per causal confirmed zone",
            "reclaim": f"bullish 1m close >= zone high + {RECLAIM_BUFFER_ATR} ATR within {RECLAIM_WINDOW_MINUTES}m",
            "control": "nearest unused non-touch bar with same EMA trend, ATR quintile and 4h UTC bucket; >=24h from every touch",
            "round_trip_cost_pct": cost_pct,
            "trade_hurdle_pct": hurdle_pct,
            "horizons_minutes": HORIZONS_MINUTES,
        },
        "dataset": {
            "primary_bars": len(primary), "tick_bars": len(tick),
            "start": str(primary["ts"].iloc[0]), "end": str(primary["ts"].iloc[-1]),
        },
        "summaries": summaries,
        "events": {
            "touch": touch_events, "reclaim": reclaim_events,
            "touch_control": controls, "reclaim_control": reclaim_controls,
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": output["dataset"], "summaries": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
