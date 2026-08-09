"""Cross-sectional momentum discovery on a fixed, liquid perpetual universe."""
import argparse
import gzip
import itertools
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


LOOKBACK_DAYS = (3, 7, 14, 30)
REBALANCE_DAYS = (1, 3, 7)
BUCKET_SIZE = (1, 2, 3)
LIQUID_UNIVERSE_SIZE = (8, 12, 15)
SCORE_MODE = ("raw_momentum", "vol_adjusted_momentum")
PORTFOLIO_MODE = ("long_short",)
BASE_ONE_WAY_COST_PCT = .07
STRESS_ONE_WAY_COST_PCT = .15


def load_panel(directory: Path):
    closes, opens, liquidity = {}, {}, {}
    for path in sorted(directory.glob("*_1h_*.json.gz")):
        symbol = path.name.split("_", 1)[0].upper()
        with gzip.open(path, "rt", encoding="utf-8") as source:
            rows = json.load(source)["rows"]
        frame = pd.DataFrame(rows)
        frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
        frame = frame.set_index("ts").sort_index()
        closes[symbol] = frame.close
        opens[symbol] = frame.open
        liquidity[symbol] = frame.close * frame.volume
    close = pd.DataFrame(closes).dropna()
    open_price = pd.DataFrame(opens).reindex(close.index).dropna()
    dollar_volume = pd.DataFrame(liquidity).reindex(close.index)
    common = close.index.intersection(open_price.index)
    return close.reindex(common), open_price.reindex(common), dollar_volume.reindex(common)


def load_funding(directory: Path, index, symbols):
    panel = pd.DataFrame(0.0, index=index, columns=symbols)
    for path in sorted(directory.glob("*_funding_*.json.gz")):
        symbol = path.name.split("_", 1)[0].upper()
        if symbol not in panel:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as source:
            rows = json.load(source)["rows"]
        frame = pd.DataFrame(rows)
        # Binance occasionally reports the settlement a few milliseconds after
        # the nominal hour. Normalize it to that hour before joining candles.
        frame["ts"] = pd.to_datetime(frame.ts, unit="ms").dt.floor("h")
        values = frame.groupby("ts").funding_rate.last()
        common = panel.index.intersection(values.index)
        panel.loc[common, symbol] = values.reindex(common)
    return panel


def weights_at(score_panel, liquidity_panel, signal_at, bucket_size,
               liquid_size, portfolio_mode):
    signal_idx = score_panel.index.get_indexer([signal_at], method="pad")[0]
    if signal_idx < 30 * 24:
        return None
    liq = liquidity_panel.iloc[signal_idx]
    universe = liq.nlargest(liquid_size).index
    score = score_panel.iloc[signal_idx].reindex(universe).dropna().sort_values()
    if len(score) < max(2 * bucket_size, bucket_size):
        return None
    result = pd.Series(0.0, index=score_panel.columns)
    longs = score.nlargest(bucket_size).index
    if portfolio_mode == "long_only":
        result.loc[longs] = 1 / bucket_size
    else:
        shorts = score.nsmallest(bucket_size).index
        result.loc[longs] = .5 / bucket_size
        result.loc[shorts] = -.5 / bucket_size
    return result


def replay(close, open_price, funding_panel, score_panel, liquidity_panel, start, end,
           rebalance_days, bucket_size, liquid_size, portfolio_mode,
           one_way_cost_pct):
    schedule = pd.date_range(start.ceil("D"), end.floor("D"), freq=f"{rebalance_days}D")
    schedule = schedule[schedule.isin(open_price.index)]
    prior_weights = pd.Series(0.0, index=close.columns)
    prior_entry = None
    prior_timestamp = None
    returns, timestamps, assets = [], [], Counter()
    for timestamp in schedule:
        signal_at = timestamp - pd.Timedelta(hours=1)
        new_weights = weights_at(
            score_panel, liquidity_panel, signal_at, bucket_size,
            liquid_size, portfolio_mode,
        )
        if new_weights is None:
            continue
        current_open = open_price.loc[timestamp]
        gross = 0.0
        if prior_entry is not None:
            asset_returns = current_open / prior_entry - 1
            gross = float((prior_weights * asset_returns).sum())
            funding = funding_panel[(funding_panel.index > prior_timestamp) & (funding_panel.index <= timestamp)].sum()
            gross += float((-prior_weights * funding).sum())
        turnover = float((new_weights - prior_weights).abs().sum())
        net = gross - turnover * one_way_cost_pct / 100
        returns.append(net)
        timestamps.append(timestamp)
        for symbol, weight in new_weights[new_weights != 0].items():
            assets[("LONG" if weight > 0 else "SHORT", symbol)] += 1
        prior_weights, prior_entry, prior_timestamp = new_weights, current_open, timestamp
    if prior_entry is not None:
        final_ts = close.index[close.index < end][-1]
        final_price = close.loc[final_ts]
        gross = float((prior_weights * (final_price / prior_entry - 1)).sum())
        funding = funding_panel[(funding_panel.index > prior_timestamp) & (funding_panel.index <= final_ts)].sum()
        gross += float((-prior_weights * funding).sum())
        net = gross - float(prior_weights.abs().sum()) * one_way_cost_pct / 100
        returns.append(net); timestamps.append(final_ts)
    return np.asarray(returns), pd.DatetimeIndex(timestamps), assets


def metrics(returns, timestamps, assets, start, end, robustness=False):
    if not len(returns):
        return {"periods": 0, "net_return_pct": 0.0, "profit_factor": None}
    equity = np.cumprod(1 + returns)
    gain, loss = returns[returns > 0].sum(), abs(returns[returns < 0].sum())
    peaks = np.maximum.accumulate(np.r_[1.0, equity])
    drawdown = np.r_[1.0, equity] / peaks - 1
    months = max((end - start) / pd.Timedelta(days=30), 1 / 30)
    result = {
        "periods": len(returns), "periods_per_30d": round(len(returns) / months, 4),
        "net_return_pct": round(float((equity[-1] - 1) * 100), 6),
        "profit_factor": round(float(gain / loss), 6) if loss else None,
        "win_rate_pct": round(float((returns > 0).mean() * 100), 4),
        "max_drawdown_pct": round(float(abs(drawdown.min()) * 100), 6),
        "top_allocations": [
            {"side": side, "symbol": symbol, "count": count}
            for (side, symbol), count in assets.most_common(12)
        ],
    }
    if robustness:
        quarters = []
        cursor = start
        series = pd.Series(returns, index=timestamps)
        while cursor < end:
            finish = min(cursor + pd.DateOffset(months=3), end)
            values = series[(series.index >= cursor) & (series.index < finish)].to_numpy()
            quarters.append(float((np.prod(1 + values) - 1) * 100) if len(values) else 0.0)
            cursor = finish
        result["quarter_returns_pct"] = [round(value, 6) for value in quarters]
        result["positive_quarters"] = sum(value > 0 for value in quarters)
        result["quarter_count"] = len(quarters)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/backtests/cross_sectional")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    close, open_price, dollar_volume = load_panel(Path(args.data_dir))
    funding_panel = load_funding(Path(args.data_dir), close.index, close.columns)
    hourly_returns = close.pct_change(fill_method=None)
    liquidity_panel = dollar_volume.rolling(30 * 24, min_periods=30 * 24).mean()
    score_panels = {}
    for lookback in LOOKBACK_DAYS:
        bars = lookback * 24
        momentum = close / close.shift(bars) - 1
        volatility = hourly_returns.rolling(bars, min_periods=bars).std().replace(0, np.nan)
        score_panels[(lookback, "raw_momentum")] = momentum
        score_panels[(lookback, "vol_adjusted_momentum")] = momentum / volatility
    start, end = close.index[0], close.index[-1] + pd.Timedelta(hours=1)
    train_end = start + pd.DateOffset(months=18)
    validation_end = train_end + pd.DateOffset(months=6)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    grid = []
    for params in itertools.product(LOOKBACK_DAYS, REBALANCE_DAYS, BUCKET_SIZE,
                                    LIQUID_UNIVERSE_SIZE, SCORE_MODE, PORTFOLIO_MODE):
        lookback, rebalance, bucket, liquid_size, score_mode, portfolio_mode = params
        if 2 * bucket > liquid_size and portfolio_mode == "long_short":
            continue
        score_panel = score_panels[(lookback, score_mode)]
        values, timestamps, assets = replay(
            close, open_price, funding_panel, score_panel, liquidity_panel, *splits["train"],
            rebalance, bucket, liquid_size, portfolio_mode, BASE_ONE_WAY_COST_PCT,
        )
        metric = metrics(values, timestamps, assets, *splits["train"], True)
        grid.append({
            "lookback_days": lookback, "rebalance_days": rebalance,
            "bucket_size": bucket, "liquid_universe_size": liquid_size,
            "score_mode": score_mode, "portfolio_mode": portfolio_mode,
            "metrics": {"train": metric},
        })
    eligible = [item for item in grid if (
        item["metrics"]["train"]["net_return_pct"] > 0
        and (item["metrics"]["train"]["profit_factor"] or 0) > 1.1
        and item["metrics"]["train"]["max_drawdown_pct"] <= 15
        and item["metrics"]["train"]["positive_quarters"] >= np.ceil(item["metrics"]["train"]["quarter_count"] * .6)
    )]
    selected = max(eligible, key=lambda item: (
        item["metrics"]["train"]["net_return_pct"] / max(item["metrics"]["train"]["max_drawdown_pct"], .01),
        item["metrics"]["train"]["profit_factor"],
    )) if eligible else None
    stress = {}
    if selected:
        lookback, rebalance, bucket, liquid_size, score_mode, portfolio_mode = tuple(selected[key] for key in (
            "lookback_days", "rebalance_days", "bucket_size", "liquid_universe_size", "score_mode", "portfolio_mode",
        ))
        score_panel = score_panels[(lookback, score_mode)]
        for name in ("validation", "test"):
            values, timestamps, assets = replay(close, open_price, funding_panel, score_panel, liquidity_panel, *splits[name], rebalance, bucket, liquid_size, portfolio_mode, BASE_ONE_WAY_COST_PCT)
            selected["metrics"][name] = metrics(values, timestamps, assets, *splits[name])
        for name, bounds in splits.items():
            values, timestamps, assets = replay(close, open_price, funding_panel, score_panel, liquidity_panel, *bounds, rebalance, bucket, liquid_size, portfolio_mode, STRESS_ONE_WAY_COST_PCT)
            stress[name] = metrics(values, timestamps, assets, *bounds)
    passed = bool(selected and all(
        selected["metrics"][name]["net_return_pct"] > 0
        and (selected["metrics"][name]["profit_factor"] or 0) > 1
        for name in ("validation", "test")
    ) and all(
        stress[name]["net_return_pct"] > 0 and (stress[name]["profit_factor"] or 0) > 1
        for name in splits
    ))
    output = {
        "passed": passed,
        "contract": {
            "selection": "train only", "execution": "signal at prior 1h close; fill next scheduled 1h open",
            "universe": list(close.columns), "liquidity_filter": "causal trailing 30d dollar volume",
            "base_one_way_cost_pct": BASE_ONE_WAY_COST_PCT,
            "stress_one_way_cost_pct": STRESS_ONE_WAY_COST_PCT,
            "funding_included": True,
        },
        "dataset": {"start": str(start), "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end), "symbols": len(close.columns), "rows": len(close)},
        "grid_size": len(grid), "eligible_count": len(eligible), "selected": selected,
        "cost_stress": stress, "grid": grid,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "dataset", "grid_size", "eligible_count", "selected", "cost_stress")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
