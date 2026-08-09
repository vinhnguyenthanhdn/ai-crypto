"""Train-only discovery Fast Champion trên 15m/30m/1h."""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine import staggered_pullback as strategy  # noqa: E402


TIMEFRAMES = ("15min", "30min", "1h")
LOOKBACKS = (48, 96, 192)
TREND_EMAS = (200, 400)
ENTRY_Z = (1.0, 1.5, 2.0, 2.5)
EXIT_Z = (0.0, 0.5, 1.0)
STOP_ATR = (2.0, 3.0, 5.0)
MAX_TRANCHES = (1, 3)
RISK_PER_EXCURSION_PCT = 0.5
BASE_COST_PCT = 0.30
STRESS_COST_PCT = 0.60
MIN_EXCURSIONS_PER_WEEK = 2.0


def _load(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    return frame.set_index("ts")


def _aggregate(source, timeframe):
    aggregation = {
        "open": ("open", "first"), "high": ("high", "max"),
        "low": ("low", "min"), "close": ("close", "last"),
        "count": ("close", "count"),
    }
    if "volume" in source:
        aggregation.update({"volume": ("volume", "sum"), "taker_buy_base": ("taker_buy_base", "sum")})
    bars = source.resample(timeframe, origin="epoch", label="left", closed="left").agg(**aggregation)
    if "volume" in bars:
        bars["taker_imbalance"] = (2 * bars.taker_buy_base / bars.volume.replace(0, np.nan) - 1).fillna(0)
    expected = int(pd.Timedelta(timeframe) / pd.Timedelta(minutes=5))
    return bars[bars["count"] == expected].drop(columns="count").dropna()


def _contributions(trades, contract):
    values = []
    for trade in trades:
        stop_pct = abs(trade["stop"] / trade["entry_price"] - 1) * 100
        tranche_risk_pct = RISK_PER_EXCURSION_PCT / contract.max_tranches
        capital_fraction = min(1 / contract.max_tranches, tranche_risk_pct / stop_pct)
        values.append((pd.Timestamp(trade["exit_ts"]), trade["net_return_pct"] * capital_fraction))
    return values


def _compound(values):
    values = np.asarray(values, dtype=float)
    return float((np.prod(1 + values / 100) - 1) * 100) if len(values) else 0.0


def _metrics(trades, start, end, contract, robustness=False):
    contributions = _contributions(trades, contract)
    values = np.asarray([value for _, value in contributions])
    gains = values[values > 0].sum() if len(values) else 0.0
    losses = abs(values[values < 0].sum()) if len(values) else 0.0
    equity = np.r_[1.0, np.cumprod(1 + values / 100)]
    drawdown = equity / np.maximum.accumulate(equity) - 1
    excursions = len({trade["excursion_id"] for trade in trades})
    weeks = (pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta(days=7)
    result = {
        "tickets": len(trades), "excursions": excursions,
        "excursions_per_week": round(float(excursions / weeks), 6),
        "net_return_pct": round(_compound(values), 6),
        "profit_factor": round(float(gains / losses), 6) if losses else None,
        "max_drawdown_pct": round(float(abs(drawdown.min()) * 100), 6),
        "win_rate_pct": round(float((values > 0).mean() * 100), 4) if len(values) else None,
    }
    if robustness:
        quarter_returns = []
        cursor = pd.Timestamp(start)
        while cursor < pd.Timestamp(end):
            window_end = min(cursor + pd.DateOffset(months=3), pd.Timestamp(end))
            quarter_returns.append(_compound([
                value for timestamp, value in contributions if cursor <= timestamp < window_end
            ]))
            cursor = window_end
        result["quarter_returns_pct"] = [round(value, 6) for value in quarter_returns]
        result["positive_quarters"] = sum(value > 0 for value in quarter_returns)
        result["quarter_count"] = len(quarter_returns)
    return result


def _train_gate(metric, minimum_excursions_per_week=MIN_EXCURSIONS_PER_WEEK):
    return (
        metric["net_return_pct"] > 0
        and (metric["profit_factor"] or 0) > 1.10
        and metric["max_drawdown_pct"] <= 10
        and metric["excursions_per_week"] >= minimum_excursions_per_week
        and metric["positive_quarters"] >= np.ceil(metric["quarter_count"] * 0.60)
    )


def _oos_gate(metric, minimum_excursions_per_week=MIN_EXCURSIONS_PER_WEEK):
    return (
        metric["net_return_pct"] > 0
        and (metric["profit_factor"] or 0) > 1
        and metric["max_drawdown_pct"] <= 10
        and metric["excursions_per_week"] >= minimum_excursions_per_week
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-cost-pct", type=float, default=BASE_COST_PCT)
    parser.add_argument("--stress-cost-pct", type=float, default=STRESS_COST_PCT)
    parser.add_argument("--min-excursions-per-week", type=float, default=MIN_EXCURSIONS_PER_WEEK)
    args = parser.parse_args()
    source_path = Path(args.flow_cache)
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = _load(source_path)
    end = pd.Timestamp("2026-08-07")
    start = end - pd.DateOffset(years=3)
    train_end = start + pd.DateOffset(years=2)
    validation_end = train_end + pd.DateOffset(months=6)
    splits = {
        "train": (start, train_end),
        "validation": (train_end, validation_end),
        "test": (validation_end, end),
    }

    bars_by_tf = {timeframe: _aggregate(source, timeframe) for timeframe in TIMEFRAMES}
    featured = {}
    for timeframe, bars in bars_by_tf.items():
        for lookback in LOOKBACKS:
            for trend_ema in TREND_EMAS:
                contract = strategy.Contract(
                    timeframe=timeframe, z_lookback_bars=lookback,
                    trend_ema_bars=trend_ema,
                )
                featured[(timeframe, lookback, trend_ema)] = strategy.add_features(bars, contract)

    grid = []
    for timeframe in TIMEFRAMES:
        for lookback in LOOKBACKS:
            for trend_ema in TREND_EMAS:
                frame = featured[(timeframe, lookback, trend_ema)]
                for entry_z in ENTRY_Z:
                    for exit_z in EXIT_Z:
                        for stop_atr in STOP_ATR:
                            for max_tranches in MAX_TRANCHES:
                                contract = strategy.Contract(
                                    timeframe=timeframe, z_lookback_bars=lookback,
                                    trend_ema_bars=trend_ema, entry_z=entry_z,
                                    exit_z=exit_z, stop_atr=stop_atr,
                                    max_tranches=max_tranches,
                                    round_trip_cost_pct=args.base_cost_pct,
                                )
                                trades = strategy.replay(frame, *splits["train"], contract)
                                metric = _metrics(
                                    trades, *splits["train"], contract, robustness=True,
                                )
                                grid.append({
                                    "timeframe": timeframe, "z_lookback_bars": lookback,
                                    "trend_ema_bars": trend_ema, "entry_z": entry_z,
                                    "exit_z": exit_z, "stop_atr": stop_atr,
                                    "max_tranches": max_tranches,
                                    "metrics": {"train": metric},
                                })
    eligible = [item for item in grid if _train_gate(item["metrics"]["train"], args.min_excursions_per_week)]
    selected = max(
        eligible,
        key=lambda item: (
            item["metrics"]["train"]["net_return_pct"],
            item["metrics"]["train"]["profit_factor"],
        ),
    ) if eligible else None

    stress = {}
    if selected:
        params = {key: selected[key] for key in (
            "timeframe", "z_lookback_bars", "trend_ema_bars", "entry_z",
            "exit_z", "stop_atr", "max_tranches",
        )}
        frame = featured[(params["timeframe"], params["z_lookback_bars"], params["trend_ema_bars"])]
        base_contract = strategy.Contract(**params, round_trip_cost_pct=args.base_cost_pct)
        stress_contract = strategy.Contract(**params, round_trip_cost_pct=args.stress_cost_pct)
        for name in ("validation", "test"):
            trades = strategy.replay(frame, *splits[name], base_contract)
            selected["metrics"][name] = _metrics(trades, *splits[name], base_contract)
        for name, bounds in splits.items():
            trades = strategy.replay(frame, *bounds, stress_contract)
            stress[name] = _metrics(trades, *bounds, stress_contract)

    passed = bool(
        selected
        and all(_oos_gate(selected["metrics"][name], args.min_excursions_per_week) for name in ("validation", "test"))
        and all(_oos_gate(stress[name], args.min_excursions_per_week) for name in splits)
    )
    output = {
        "passed": passed,
        "contract": {
            "selection": "train_only", "primary_objective": "portfolio_net_return",
            "risk_per_excursion_pct": RISK_PER_EXCURSION_PCT,
            "min_excursions_per_week": args.min_excursions_per_week,
            "base_cost_pct": args.base_cost_pct, "stress_cost_pct": args.stress_cost_pct,
        },
        "dataset": {
            "source": str(source_path), "sha256": digest, "start": str(start),
            "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end),
        },
        "grid_size": len(grid), "train_eligible_count": len(eligible),
        "selected": selected, "cost_stress": stress, "grid": grid,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": passed, "grid_size": len(grid),
        "train_eligible_count": len(eligible), "selected": selected,
        "cost_stress": stress,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
