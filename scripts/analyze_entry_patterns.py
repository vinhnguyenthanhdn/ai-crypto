"""Causal price-action event study để khám phá candidate entry Long.

Script chỉ đo location/trigger; không đặt lệnh hoặc tối ưu TP/SL. Mọi signal
dùng nến 5m đã đóng và giả định fill ở open nến kế tiếp.
"""
import argparse
import bisect
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import WARMUP_BARS  # noqa: E402
from src.engine import risk, support_resistance  # noqa: E402
from src.indicators import technical  # noqa: E402


HORIZONS_MINUTES = (15, 60, 240, 1440)
BREAKOUT_BUFFER_ATR = 0.10
RETEST_BUFFER_ATR = 0.15
RETEST_CONFIRM_ATR = 0.05
RETEST_WINDOW_BARS = 48  # 4 giờ
EVENT_COOLDOWN_BARS = 12  # 1 giờ
CONTROL_EXCLUSION_HOURS = 6
BOOTSTRAP_SAMPLES = 2000


def _load_dataset(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _path_arrays(tick: pd.DataFrame):
    times, prices = [], []
    step = pd.Timedelta(seconds=15)
    for row in tick.itertuples():
        for offset, field in enumerate(("open", "low", "high", "close")):
            times.append(row.ts + step * offset)
            prices.append(float(getattr(row, field)))
    return np.asarray(times, dtype="datetime64[ns]"), np.asarray(prices, dtype=float)


def _forward_metrics(path_ts, path_price, entry_time, entry_price):
    start = int(np.searchsorted(path_ts, np.datetime64(entry_time), side="left"))
    result = {}
    for minutes in HORIZONS_MINUTES:
        end_time = np.datetime64(entry_time + pd.Timedelta(minutes=minutes))
        if not len(path_ts) or path_ts[-1] < end_time:
            result[str(minutes)] = None
            continue
        end = int(np.searchsorted(path_ts, end_time, side="left"))
        window = path_price[start:end]
        if not len(window):
            result[str(minutes)] = None
            continue
        result[str(minutes)] = {
            "forward_return_pct": float((window[-1] / entry_price - 1) * 100),
            "mfe_pct": float((window.max() / entry_price - 1) * 100),
            "mae_pct": float((window.min() / entry_price - 1) * 100),
        }
    return result


def _event(enriched, signal_idx, name, level, path_ts, path_price, detail=None):
    entry_idx = signal_idx + 1
    signal = enriched.iloc[signal_idx]
    entry = enriched.iloc[entry_idx]
    entry_time = pd.Timestamp(entry["ts"])
    atr_pct = float(signal["atr"] / signal["close"] * 100)
    return {
        "pattern": name,
        "signal_idx": int(signal_idx),
        "signal_ts": str(pd.Timestamp(signal["ts"]) + pd.Timedelta(minutes=5)),
        "entry_ts": str(entry_time),
        "entry_price": float(entry["open"]),
        "level": float(level),
        "atr": float(signal["atr"]),
        "atr_pct": atr_pct,
        "trend": "UP" if signal["ema20"] > signal["ema50"] else "DOWN",
        "hour_bucket": int(entry_time.hour // 4),
        "metrics": _forward_metrics(path_ts, path_price, entry_time, float(entry["open"])),
        "detail": detail or {},
    }


def _dedup(events):
    result, last_idx = [], -10_000
    for event in sorted(events, key=lambda row: row["signal_idx"]):
        if event["signal_idx"] - last_idx < EVENT_COOLDOWN_BARS:
            continue
        result.append(event)
        last_idx = event["signal_idx"]
    return result


def _discover_events(enriched, path_ts, path_price):
    events = defaultdict(list)
    resistance_breakouts = []
    rolling_breakouts = []
    seen_resistance = set()
    last_rolling_idx = -10_000

    for i in range(WARMUP_BARS, len(enriched) - 1):
        row, prev = enriched.iloc[i], enriched.iloc[i - 1]
        if pd.isna(row["atr"]) or row["atr"] <= 0:
            continue
        bullish = row["close"] > row["open"]
        uptrend = row["ema20"] > row["ema50"]
        volume_ok = row["volume"] >= row["vol_sma20"]

        resistance = support_resistance.find_active_zone(
            enriched, kind="high", decision_idx=i - 1, required_swings=2,
        )
        if resistance:
            level = resistance.high
            threshold = level + BREAKOUT_BUFFER_ATR * row["atr"]
            zone_key = tuple(swing.idx for swing in resistance.swings)
            crossed = prev["close"] <= threshold and row["close"] > threshold
            if crossed and bullish and uptrend and volume_ok and zone_key not in seen_resistance:
                event = _event(
                    enriched, i, "confirmed_resistance_breakout", level,
                    path_ts, path_price,
                    {"zone": resistance.to_dict(), "volume_ratio": float(row["volume"] / row["vol_sma20"])},
                )
                events[event["pattern"]].append(event)
                resistance_breakouts.append((i, resistance, event))
                seen_resistance.add(zone_key)

        rolling_level = float(enriched["high"].iloc[i - 48:i].max())
        rolling_threshold = rolling_level + BREAKOUT_BUFFER_ATR * row["atr"]
        rolling_crossed = prev["close"] <= rolling_threshold and row["close"] > rolling_threshold
        if (
            rolling_crossed and bullish and uptrend
            and row["volume"] >= 1.2 * row["vol_sma20"]
            and i - last_rolling_idx >= EVENT_COOLDOWN_BARS
        ):
            event = _event(
                enriched, i, "rolling_4h_high_breakout", rolling_level,
                path_ts, path_price,
                {"lookback_bars": 48, "volume_ratio": float(row["volume"] / row["vol_sma20"])},
            )
            events[event["pattern"]].append(event)
            rolling_breakouts.append((i, rolling_level, event))
            last_rolling_idx = i

        ema_stack = row["ema20"] > row["ema50"] > row["ema200"]
        touched = row["low"] <= row["ema20"] + RETEST_BUFFER_ATR * row["atr"]
        reclaimed = row["close"] >= row["ema20"] + RETEST_CONFIRM_ATR * row["atr"]
        confirmation = bullish and row["close"] > prev["close"]
        if ema_stack and touched and reclaimed and confirmation:
            events["ema20_pullback_reclaim"].append(_event(
                enriched, i, "ema20_pullback_reclaim", float(row["ema20"]),
                path_ts, path_price,
                {"distance_low_to_ema_atr": float((row["low"] - row["ema20"]) / row["atr"])},
            ))

    for breakout_idx, zone, parent in resistance_breakouts:
        level = zone.high
        for j in range(breakout_idx + 1, min(breakout_idx + RETEST_WINDOW_BARS + 1, len(enriched) - 1)):
            row = enriched.iloc[j]
            if row["close"] < zone.low - RETEST_BUFFER_ATR * row["atr"]:
                break
            visited = row["low"] <= level + RETEST_BUFFER_ATR * row["atr"]
            confirmed = row["close"] >= level + RETEST_CONFIRM_ATR * row["atr"] and row["close"] > row["open"]
            if visited and confirmed:
                events["confirmed_resistance_breakout_retest"].append(_event(
                    enriched, j, "confirmed_resistance_breakout_retest", level,
                    path_ts, path_price,
                    {"parent_signal_ts": parent["signal_ts"], "bars_after_breakout": j - breakout_idx},
                ))
                break

    for breakout_idx, level, parent in rolling_breakouts:
        for j in range(breakout_idx + 1, min(breakout_idx + RETEST_WINDOW_BARS + 1, len(enriched) - 1)):
            row = enriched.iloc[j]
            if row["close"] < level - RETEST_BUFFER_ATR * row["atr"]:
                break
            visited = row["low"] <= level + RETEST_BUFFER_ATR * row["atr"]
            confirmed = row["close"] >= level + RETEST_CONFIRM_ATR * row["atr"] and row["close"] > row["open"]
            if visited and confirmed:
                events["rolling_4h_high_breakout_retest"].append(_event(
                    enriched, j, "rolling_4h_high_breakout_retest", level,
                    path_ts, path_price,
                    {"parent_signal_ts": parent["signal_ts"], "bars_after_breakout": j - breakout_idx},
                ))
                break

    return {name: _dedup(rows) for name, rows in events.items()}


def _build_control_pool(enriched, path_ts, path_price, atr_edges):
    pool = []
    for i in range(WARMUP_BARS, len(enriched) - 1):
        row, entry = enriched.iloc[i], enriched.iloc[i + 1]
        if pd.isna(row["atr"]) or row["atr"] <= 0:
            continue
        atr_pct = float(row["atr"] / row["close"] * 100)
        atr_bin = int(np.clip(np.searchsorted(atr_edges, atr_pct, side="right") - 1, 0, 4))
        entry_time = pd.Timestamp(entry["ts"])
        pool.append({
            "signal_idx": i,
            "entry_ts": str(entry_time),
            "entry_price": float(entry["open"]),
            "trend": "UP" if row["ema20"] > row["ema50"] else "DOWN",
            "atr_bin": atr_bin,
            "hour_bucket": int(entry_time.hour // 4),
            "metrics": _forward_metrics(path_ts, path_price, entry_time, float(entry["open"])),
        })
    return pool


def _match_controls(events, pool, atr_edges):
    event_times = [pd.Timestamp(row["entry_ts"]) for row in events]
    buckets = defaultdict(list)
    for row in pool:
        buckets[(row["trend"], row["atr_bin"], row["hour_bucket"])].append(row)
    used, controls = set(), []
    for event in events:
        atr_bin = int(np.clip(np.searchsorted(atr_edges, event["atr_pct"], side="right") - 1, 0, 4))
        candidates = buckets[(event["trend"], atr_bin, event["hour_bucket"])]
        times = [row["entry_ts"] for row in candidates]
        pos = bisect.bisect_left(times, event["entry_ts"])
        ranked = sorted(
            (row for row in candidates[max(0, pos - 80):pos + 81] if row["signal_idx"] not in used),
            key=lambda row: abs(pd.Timestamp(row["entry_ts"]) - pd.Timestamp(event["entry_ts"])),
        )
        chosen = None
        for row in ranked:
            ts = pd.Timestamp(row["entry_ts"])
            if all(abs(ts - event_ts) >= pd.Timedelta(hours=CONTROL_EXCLUSION_HOURS) for event_ts in event_times):
                chosen = row
                break
        if chosen:
            used.add(chosen["signal_idx"])
            controls.append({**chosen, "matched_signal_idx": event["signal_idx"]})
    return controls


def _paired_summary(events, controls, split_ts, cost_pct, hurdle_pct):
    controls_by_idx = {row["matched_signal_idx"]: row for row in controls}
    pairs = [(event, controls_by_idx[event["signal_idx"]]) for event in events if event["signal_idx"] in controls_by_idx]
    output = {}
    for split in ("all", "discovery", "validation"):
        if split == "discovery":
            selected = [pair for pair in pairs if pd.Timestamp(pair[0]["entry_ts"]) < split_ts]
        elif split == "validation":
            selected = [pair for pair in pairs if pd.Timestamp(pair[0]["entry_ts"]) >= split_ts]
        else:
            selected = pairs
        horizons = {}
        for minutes in HORIZONS_MINUTES:
            key = str(minutes)
            values = [
                (event["metrics"][key], control["metrics"][key])
                for event, control in selected
                if event["metrics"].get(key) and control["metrics"].get(key)
            ]
            if not values:
                horizons[key] = None
                continue
            event_fwd = np.asarray([pair[0]["forward_return_pct"] for pair in values])
            control_fwd = np.asarray([pair[1]["forward_return_pct"] for pair in values])
            event_mfe = np.asarray([pair[0]["mfe_pct"] for pair in values])
            event_mae = np.asarray([pair[0]["mae_pct"] for pair in values])
            lift = event_fwd - control_fwd
            rng = np.random.default_rng(20260807 + minutes + len(values))
            means = np.asarray([
                lift[rng.integers(0, len(lift), len(lift))].mean()
                for _ in range(BOOTSTRAP_SAMPLES)
            ])
            horizons[key] = {
                "n": len(values),
                "forward_mean_pct": round(float(event_fwd.mean()), 6),
                "control_mean_pct": round(float(control_fwd.mean()), 6),
                "paired_lift_mean_pct": round(float(lift.mean()), 6),
                "paired_lift_ci95_pct": [round(float(np.quantile(means, 0.025)), 6), round(float(np.quantile(means, 0.975)), 6)],
                "mfe_median_pct": round(float(np.median(event_mfe)), 6),
                "mae_median_pct": round(float(np.median(event_mae)), 6),
                "p_close_above_cost_pct": round(float((event_fwd >= cost_pct).mean() * 100), 4),
                "p_mfe_above_hurdle_pct": round(float((event_mfe >= hurdle_pct).mean() * 100), 4),
                "p_mfe_gt_abs_mae_pct": round(float((event_mfe > np.abs(event_mae)).mean() * 100), 4),
            }
        output[split] = {"n_pairs": len(selected), "horizons": horizons}
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--since", help="ISO timestamp inclusive")
    parser.add_argument("--until", help="ISO timestamp exclusive")
    args = parser.parse_args()

    raw = _load_dataset(Path(args.dataset_cache))
    primary = technical.to_dataframe(raw["primary"])
    tick = technical.to_dataframe(raw["tick"])
    if args.since:
        since = pd.Timestamp(args.since)
        primary = primary[primary["ts"] >= since].reset_index(drop=True)
        tick = tick[tick["ts"] >= since].reset_index(drop=True)
    if args.until:
        until = pd.Timestamp(args.until)
        primary = primary[primary["ts"] < until].reset_index(drop=True)
        tick = tick[tick["ts"] < until].reset_index(drop=True)
    if len(primary) <= WARMUP_BARS + 2 or tick.empty:
        raise ValueError("Dataset sau khi lọc không đủ warmup hoặc tick path")
    enriched = technical.add_indicators(primary)
    path_ts, path_price = _path_arrays(tick)
    split_ts = pd.Timestamp(primary["ts"].iloc[0]) + (pd.Timestamp(primary["ts"].iloc[-1]) - pd.Timestamp(primary["ts"].iloc[0])) / 2
    atr_pct = (enriched["atr"] / enriched["close"] * 100).iloc[WARMUP_BARS:].dropna()
    atr_edges = np.quantile(atr_pct, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    events = _discover_events(enriched, path_ts, path_price)
    pool = _build_control_pool(enriched, path_ts, path_price, atr_edges)
    cost_pct = risk.round_trip_cost_pct()
    hurdle_pct = cost_pct * 2.5
    summaries, controls = {}, {}
    for name, rows in events.items():
        matched = _match_controls(rows, pool, atr_edges)
        controls[name] = matched
        summaries[name] = _paired_summary(rows, matched, split_ts, cost_pct, hurdle_pct)

    output = {
        "contract": {
            "side": "long_spot",
            "signal_timeframe": "5m closed candle",
            "tick_proxy": "1m OHLC adverse-first path",
            "fill": "next 5m open",
            "patterns": sorted(events),
            "horizons_minutes": HORIZONS_MINUTES,
            "breakout_buffer_atr": BREAKOUT_BUFFER_ATR,
            "retest_window_minutes": RETEST_WINDOW_BARS * 5,
            "retest_buffer_atr": RETEST_BUFFER_ATR,
            "retest_confirm_atr": RETEST_CONFIRM_ATR,
            "event_cooldown_minutes": EVENT_COOLDOWN_BARS * 5,
            "control": "nearest unused bar with same EMA trend, ATR quintile and 4h UTC bucket; >=6h from every same-pattern event",
            "round_trip_cost_pct": cost_pct,
            "trade_hurdle_pct": hurdle_pct,
            "split_ts": str(split_ts),
        },
        "dataset": {
            "primary_bars": len(primary), "tick_bars": len(tick),
            "start": str(primary["ts"].iloc[0]), "end": str(primary["ts"].iloc[-1]),
        },
        "summaries": summaries,
        "events": events,
        "controls": controls,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": output["dataset"], "contract": output["contract"], "summaries": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
