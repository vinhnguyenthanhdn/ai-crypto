"""Validate a BTC Spot trend + frequent funding-crowding composite portfolio."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discover_btc_spot_trend_9y import load_daily, replay  # noqa: E402


ROOT = Path("data/backtests")
WEIGHTS = (.25, .50, .75, .90)  # BTC Spot sleeve; remainder is frequent sleeve.
BTC_BASE_COST = .12
BTC_STRESS_COST = .24
FAST_BASE_COST = .07
FAST_STRESS_COST = .14


def portfolio_metrics(returns: pd.Series) -> dict:
    values = returns.to_numpy(dtype=float)
    equity = np.cumprod(1 + values) if len(values) else np.asarray([1.0])
    path = np.r_[1.0, equity]
    drawdown = path / np.maximum.accumulate(path) - 1
    gain, loss = values[values > 0].sum(), -values[values < 0].sum()
    return {
        "net_return_pct": float((equity[-1] - 1) * 100) if len(values) else 0.0,
        "profit_factor": float(gain / loss) if loss else None,
        "max_drawdown_pct": float(-drawdown.min() * 100),
        "positive_30d_ratio": float((returns.resample("30D").apply(
            lambda part: np.prod(1 + part) - 1
        ) > 0).mean()),
    }


def fast_daily(rows: list[dict], start: pd.Timestamp, end: pd.Timestamp,
               cost_delta_pct: float) -> pd.Series:
    dates = pd.date_range(start, end - pd.Timedelta(days=1), freq="1D")
    if not rows:
        return pd.Series(0.0, index=dates)
    frame = pd.DataFrame(rows)
    frame["exit_time"] = pd.to_datetime(frame.exit_time).dt.tz_localize(None)
    frame["return"] = frame.net_equity_return - frame.capital_fraction * cost_delta_pct / 100
    daily = frame.set_index("exit_time")["return"].resample("1D").apply(
        lambda values: np.prod(1 + values) - 1
    )
    return daily.reindex(dates, fill_value=0.0)


def composite_returns(btc: pd.Series, fast: pd.Series, btc_weight: float) -> pd.Series:
    """Combine independently compounded sleeves without implicit rebalancing."""
    btc_equity = (1 + btc).cumprod()
    fast_equity = (1 + fast).cumprod()
    total = btc_weight * btc_equity + (1 - btc_weight) * fast_equity
    return total.pct_change().fillna(total.iloc[0] - 1)


def episode_metrics(rows: list[dict], start: pd.Timestamp, end: pd.Timestamp) -> dict:
    timestamps = pd.DatetimeIndex(pd.to_datetime(
        [row["signal_time"] for row in rows], utc=True
    ).tz_convert(None).unique()).sort_values()
    episodes = int(timestamps.nunique())
    weeks = (end - start).total_seconds() / (7 * 86400)
    edges = pd.date_range(start, end, freq="7D")
    if not len(edges) or edges[-1] < end:
        edges = edges.append(pd.DatetimeIndex([end]))
    nonoverlap = np.asarray([
        int(((timestamps >= left) & (timestamps < right)).sum())
        * (pd.Timedelta(days=7) / (right - left))
        for left, right in zip(edges[:-1], edges[1:])
    ], dtype=float)
    days = pd.date_range(start.normalize(), end.normalize(), freq="1D", inclusive="left")
    rolling = np.asarray([
        int(((timestamps > day - pd.Timedelta(days=7)) & (timestamps <= day)).sum())
        for day in days
    ], dtype=float)

    def distribution(values: np.ndarray) -> dict:
        quantiles = np.quantile(values, [.05, .25, .50, .75, .95])
        return {
            "windows": int(len(values)),
            "p05": float(quantiles[0]), "p25": float(quantiles[1]),
            "median": float(quantiles[2]), "p75": float(quantiles[3]),
            "p95": float(quantiles[4]), "maximum": float(values.max()),
            "target_window_ratio": float(((values >= 5) & (values <= 10)).mean()),
            "zero_window_ratio": float((values == 0).mean()),
        }

    return {
        "positions": len(rows), "independent_risk_episodes": episodes,
        "episodes_per_week": episodes / weeks,
        "nonoverlapping_7d_distribution": distribution(nonoverlap),
        "daily_trailing_7d_distribution": distribution(rolling),
    }


def main() -> None:
    fast_artifact = json.loads((ROOT / "multiasset_funding_crowding_5y.json").read_text())
    if not fast_artifact["selected"]:
        raise ValueError("funding-crowding artifact has no train-selected candidate")
    daily = load_daily(ROOT / "binance_btcusdt_spot_5m_flow_9y.json.gz")
    moving_average = daily.close.rolling(50, min_periods=50).mean()
    volatility = daily.close.pct_change(fill_method=None).rolling(
        30, min_periods=30
    ).std() * np.sqrt(365)
    btc_signal = (daily.close > moving_average * 1.01).astype(float) * (
        .30 / volatility
    ).clip(upper=1)

    ledgers = {}
    for split, raw_bounds in fast_artifact["bounds"].items():
        start, end = (pd.Timestamp(value).tz_localize(None) for value in raw_bounds)
        rows = fast_artifact["trades"][split]
        ledgers[split] = {"episodes": episode_metrics(rows, start, end)}
        for scenario, btc_cost, fast_cost in (
            ("base", BTC_BASE_COST, FAST_BASE_COST),
            ("stress", BTC_STRESS_COST, FAST_STRESS_COST),
        ):
            btc_values, btc_dates, _ = replay(daily, btc_signal, start, end, btc_cost)
            btc_returns = pd.Series(btc_values, index=btc_dates)
            fast_returns = fast_daily(rows, start, end, fast_cost - FAST_BASE_COST)
            ledgers[split][scenario] = {
                "btc": portfolio_metrics(btc_returns),
                "fast": portfolio_metrics(fast_returns),
                "weights": {
                    str(weight): portfolio_metrics(composite_returns(
                        btc_returns, fast_returns, weight
                    )) for weight in WEIGHTS
                },
            }

    train = ledgers["train"]
    eligible = [weight for weight in WEIGHTS if (
        5 <= train["episodes"]["episodes_per_week"] <= 10
        and train["base"]["weights"][str(weight)]["net_return_pct"] > 0
        and (train["base"]["weights"][str(weight)]["profit_factor"] or 0) > 1.05
        and train["stress"]["weights"][str(weight)]["net_return_pct"] > 0
        and (train["stress"]["weights"][str(weight)]["profit_factor"] or 0) > 1
        and train["stress"]["weights"][str(weight)]["max_drawdown_pct"] <= 20
    )]
    # Preserve as much of the already validated incumbent as train gates allow.
    selected_weight = max(eligible) if eligible else None
    selected = {}
    if selected_weight is not None:
        for split, values in ledgers.items():
            selected[split] = {
                "episodes": values["episodes"],
                "base": values["base"]["weights"][str(selected_weight)],
                "stress": values["stress"]["weights"][str(selected_weight)],
                "sleeves": {"base": {"btc": values["base"]["btc"],
                                      "fast": values["base"]["fast"]},
                            "stress": {"btc": values["stress"]["btc"],
                                       "fast": values["stress"]["fast"]}},
            }
    historical_pass = bool(selected_weight is not None and all(
        5 <= selected[name]["episodes"]["episodes_per_week"] <= 10
        and selected[name]["base"]["net_return_pct"] > 0
        and (selected[name]["base"]["profit_factor"] or 0) > 1
        and selected[name]["stress"]["net_return_pct"] > 0
        and (selected[name]["stress"]["profit_factor"] or 0) > 1
        for name in ("validation", "test")
    ))
    output = {
        "historical_pass": historical_pass,
        "status": "HISTORICAL_PASS_REQUIRES_FRESH_FORWARD" if historical_pass else "REJECTED",
        "package_id": "composite_btc_trend_funding_crowding_v1",
        "selection": {
            "weight_grid": WEIGHTS,
            "rule": "maximum incumbent BTC weight satisfying train base/stress gates",
            "selected_btc_weight": selected_weight,
            "selected_fast_weight": 1 - selected_weight if selected_weight is not None else None,
        },
        "contract": {
            "accounting": "independent sleeve equity; no implicit daily rebalancing",
            "episode": "unique fast-sleeve signal timestamp; simultaneous assets are one episode",
            "btc_source_package": "btc_spot_vol_scaled_trend_v1",
            "fast_source_package": fast_artifact["package_id"],
            "live_execution": False,
        },
        "selected": selected, "all_weights": ledgers,
    }
    path = ROOT / "composite_btc_trend_funding_crowding_5y.json"
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "historical_pass", "status", "selection", "selected"
    )}, indent=2))


if __name__ == "__main__":
    main()
