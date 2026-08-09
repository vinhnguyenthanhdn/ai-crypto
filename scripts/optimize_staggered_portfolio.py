"""Tối ưu staggered pullback theo net portfolio return, không theo ticket count."""
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


LOOKBACKS = (30, 45, 60, 90, 120)
TREND_EMA_VALUES = (120, 180, 240)
ENTRY_Z_VALUES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5)
EXIT_Z_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0)
STOP_ATR_VALUES = (3.0, 4.0, 5.0, 6.0, 8.0)
MAX_TRANCHES_VALUES = (1, 3, 5, 7, 10)
RISK_PER_EXCURSION_PCT = 1.0
MAX_DRAWDOWN_PCT = 15.0
MIN_EXCURSIONS_PER_YEAR = 5.0
MIN_POSITIVE_TRAIN_WINDOWS = 4
REQUIRED_STRESS_COST_PCT = 0.60


def _load_source(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    return frame


def _portfolio_contributions(trades, contract):
    contributions = []
    for trade in trades:
        stop_distance_pct = abs(trade["stop"] / trade["entry_price"] - 1) * 100
        risk_pct = RISK_PER_EXCURSION_PCT / contract.max_tranches
        capital_fraction = min(1 / contract.max_tranches, risk_pct / stop_distance_pct)
        contribution = trade["net_return_pct"] * capital_fraction
        contributions.append((pd.Timestamp(trade["exit_ts"]), contribution))
    return sorted(contributions, key=lambda item: item[0])


def _compounded_return_pct(values):
    values = np.asarray(values, dtype=float)
    return float((np.prod(1 + values / 100) - 1) * 100) if len(values) else 0.0


def _anchored_year_returns(contributions, start, end):
    """Các cửa sổ 365 ngày neo tại đầu train; chỉ dùng để chống regime overfit."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    returns = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + pd.Timedelta(days=365), end)
        values = [value for timestamp, value in contributions if cursor <= timestamp < window_end]
        returns.append(round(_compounded_return_pct(values), 6))
        cursor = window_end
    return returns


def _portfolio_metrics(trades, start, end, contract, *, include_robustness=False):
    if not trades:
        return {
            "tickets": 0, "excursions": 0, "portfolio_net_return_pct": 0.0,
            "portfolio_profit_factor": None, "max_drawdown_pct": 0.0,
            "average_tickets_per_30d": 0.0, "rolling_median_tickets_30d": 0.0,
        }
    contributions = _portfolio_contributions(trades, contract)
    values = np.asarray([value for _, value in contributions])
    gains, losses = values[values > 0].sum(), abs(values[values < 0].sum())
    ordered = np.asarray([value for _, value in contributions])
    equity = np.cumprod(1 + ordered / 100)
    with_origin = np.r_[1.0, equity]
    drawdown = with_origin / np.maximum.accumulate(with_origin) - 1
    entries = pd.DatetimeIndex([pd.Timestamp(trade["entry_ts"]) for trade in trades])
    checkpoints = pd.date_range(pd.Timestamp(start).normalize() + pd.Timedelta(days=30), pd.Timestamp(end).normalize(), freq="1D")
    rolling_counts = np.asarray([
        sum((entries > point - pd.Timedelta(days=30)) & (entries <= point))
        for point in checkpoints
    ])
    duration_30d = (pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta(days=30)
    excursion_count = len({trade["excursion_id"] for trade in trades})
    result = {
        "tickets": len(trades),
        "excursions": excursion_count,
        "portfolio_net_return_pct": round(_compounded_return_pct(values), 6),
        "portfolio_profit_factor": round(float(gains / losses), 6) if losses else None,
        "max_drawdown_pct": round(float(abs(drawdown.min()) * 100), 6),
        "mean_contribution_per_ticket_pct": round(float(values.mean()), 8),
        "positive_ticket_pct": round(float((values > 0).mean() * 100), 4),
        "average_tickets_per_30d": round(float(len(trades) / duration_30d), 6),
        "average_excursions_per_30d": round(float(excursion_count / duration_30d), 6),
        "rolling_p10_tickets_30d": float(np.quantile(rolling_counts, 0.10)),
        "rolling_median_tickets_30d": float(np.median(rolling_counts)),
        "rolling_p90_tickets_30d": float(np.quantile(rolling_counts, 0.90)),
        "pct_zero_ticket_windows": round(float((rolling_counts == 0).mean() * 100), 4),
    }
    if include_robustness:
        annual = _anchored_year_returns(contributions, start, end)
        result.update({
            "anchored_365d_returns_pct": annual,
            "positive_365d_windows": sum(value > 0 for value in annual),
            "median_365d_return_pct": round(float(np.median(annual)), 6),
            "worst_365d_return_pct": round(float(min(annual)), 6),
        })
    return result


def _gate(metric, start, end, *, require_train_robustness=False):
    years = (pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta(days=365)
    return (
        metric["portfolio_net_return_pct"] > 0
        and metric["portfolio_profit_factor"] is not None
        and metric["portfolio_profit_factor"] > 1
        and metric["max_drawdown_pct"] <= MAX_DRAWDOWN_PCT
        and metric["excursions"] >= MIN_EXCURSIONS_PER_YEAR * years
        and (
            not require_train_robustness
            or metric["positive_365d_windows"] >= MIN_POSITIVE_TRAIN_WINDOWS
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--split-artifact", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source = Path(args.flow_cache)
    split_artifact = json.loads(Path(args.split_artifact).read_text(encoding="utf-8"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != split_artifact["dataset"]["sha256"]:
        raise AssertionError("dataset hash không khớp split artifact")
    source_5m = _load_source(source)
    bars = strategy.aggregate_closed_4h(source_5m)
    splits = {
        "train": (pd.Timestamp(split_artifact["dataset"]["start"]), pd.Timestamp(split_artifact["dataset"]["train_end"])),
        "validation": (pd.Timestamp(split_artifact["dataset"]["train_end"]), pd.Timestamp(split_artifact["dataset"]["validation_end"])),
        "test": (pd.Timestamp(split_artifact["dataset"]["validation_end"]), pd.Timestamp(split_artifact["dataset"]["end"])),
    }
    featured = {}
    for lookback in LOOKBACKS:
        for trend_ema in TREND_EMA_VALUES:
            featured[(lookback, trend_ema)] = strategy.add_features(
                bars, strategy.Contract(
                    z_lookback_bars=lookback, trend_ema_bars=trend_ema,
                ),
            )

    grid = []
    for lookback in LOOKBACKS:
        for trend_ema in TREND_EMA_VALUES:
            for entry_z in ENTRY_Z_VALUES:
                for exit_z in EXIT_Z_VALUES:
                    for stop_atr in STOP_ATR_VALUES:
                        for max_tranches in MAX_TRANCHES_VALUES:
                            contract = strategy.Contract(
                                z_lookback_bars=lookback, trend_ema_bars=trend_ema,
                                entry_z=entry_z, exit_z=exit_z,
                                stop_atr=stop_atr, max_tranches=max_tranches,
                            )
                            bounds = splits["train"]
                            trades = strategy.replay(featured[(lookback, trend_ema)], *bounds, contract)
                            metrics = {
                                "train": _portfolio_metrics(
                                    trades, *bounds, contract, include_robustness=True,
                                ),
                            }
                            grid.append({
                                "z_lookback_bars": lookback,
                                "trend_ema_bars": trend_ema,
                                "entry_z": entry_z, "exit_z": exit_z,
                                "stop_atr": stop_atr, "max_tranches": max_tranches,
                                "metrics": metrics,
                            })

    eligible = [
        item for item in grid
        if _gate(item["metrics"]["train"], *splits["train"], require_train_robustness=True)
    ]
    selected = max(
        eligible,
        key=lambda item: (
            item["metrics"]["train"]["portfolio_net_return_pct"],
            item["metrics"]["train"]["portfolio_profit_factor"],
            -item["metrics"]["train"]["max_drawdown_pct"],
        ),
    ) if eligible else None
    if selected:
        contract = strategy.Contract(**{
            key: selected[key] for key in (
                "z_lookback_bars", "trend_ema_bars", "entry_z", "exit_z",
                "stop_atr", "max_tranches",
            )
        })
        for name in ("validation", "test"):
            bounds = splits[name]
            trades = strategy.replay(
                featured[(contract.z_lookback_bars, contract.trend_ema_bars)],
                *bounds, contract,
            )
            selected["metrics"][name] = _portfolio_metrics(trades, *bounds, contract)
    base_cost_gate_passed = bool(selected and all(
        _gate(selected["metrics"][name], *splits[name], require_train_robustness=name == "train")
        for name in splits
    ))
    required_cost_stress = {}
    if selected:
        stress_contract = strategy.Contract(**{
            **contract.manifest(), "round_trip_cost_pct": REQUIRED_STRESS_COST_PCT,
        })
        for name, bounds in splits.items():
            trades = strategy.replay(
                featured[(stress_contract.z_lookback_bars, stress_contract.trend_ema_bars)],
                *bounds, stress_contract,
            )
            metric = _portfolio_metrics(trades, *bounds, stress_contract)
            required_cost_stress[name] = {
                key: metric[key] for key in (
                    "portfolio_net_return_pct", "portfolio_profit_factor", "max_drawdown_pct",
                )
            }
    cost_stress_passed = bool(required_cost_stress) and all(
        metric["portfolio_net_return_pct"] > 0
        and metric["portfolio_profit_factor"] is not None
        and metric["portfolio_profit_factor"] > 1
        for metric in required_cost_stress.values()
    )
    passed = base_cost_gate_passed and cost_stress_passed
    output = {
        "contract": {
            "primary_objective": "maximize train portfolio net return",
            "selection": "train only",
            "risk_per_excursion_pct": RISK_PER_EXCURSION_PCT,
            "capital_cap_per_tranche": "1/max_tranches of equity",
            "round_trip_cost_pct": strategy.FROZEN_CONTRACT.round_trip_cost_pct,
            "gate": {
                "portfolio_pf_gt": 1,
                "max_drawdown_pct_lte": MAX_DRAWDOWN_PCT,
                "min_excursions_per_year": MIN_EXCURSIONS_PER_YEAR,
                "min_positive_train_365d_windows": MIN_POSITIVE_TRAIN_WINDOWS,
            },
            "frequency_role": "diagnostic only",
        },
        "dataset": {**split_artifact["dataset"], "sha256": digest},
        "grid_size": len(grid), "train_eligible_count": len(eligible),
        "base_cost_gate_passed": base_cost_gate_passed,
        "required_cost_stress_pct": REQUIRED_STRESS_COST_PCT,
        "required_cost_stress": required_cost_stress,
        "cost_stress_passed": cost_stress_passed,
        "passed": passed, "selected": selected, "grid": grid,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": passed, "base_cost_gate_passed": base_cost_gate_passed,
        "cost_stress_passed": cost_stress_passed, "grid_size": len(grid),
        "train_eligible_count": len(eligible), "selected": selected,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
