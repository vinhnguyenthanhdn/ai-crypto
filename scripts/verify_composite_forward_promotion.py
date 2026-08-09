"""Build composite forward status and fail closed on every promotion gate."""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


PACKAGE_ID = "composite_btc_trend_funding_crowding_v1"
INITIAL_SLEEVE_EQUITY = 250.0
FAST_BASE_COST_PCT = 0.07
FAST_STRESS_COST_PCT = 0.14


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def profit_metrics(returns: list[float], initial_equity: float = 500.0) -> dict:
    values = np.asarray(returns, dtype=float)
    path = np.r_[initial_equity, initial_equity * np.cumprod(1 + values)]
    peaks = np.maximum.accumulate(path)
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return {
        "events": int(len(values)),
        "net_return_pct": float((path[-1] / initial_equity - 1) * 100),
        "profit_factor": gains / losses if losses else None,
        "has_profit_without_losses": bool(gains > 0 and losses == 0),
        "max_drawdown_pct": float(-(path / peaks - 1).min() * 100),
    }


def pf_pass(metrics: dict) -> bool:
    return bool(metrics["has_profit_without_losses"] or
                (metrics["profit_factor"] is not None and metrics["profit_factor"] > 1))


def historical_metrics_pass(artifact: dict) -> bool:
    if artifact.get("historical_pass") is not True:
        return False
    selection = artifact.get("selection", {})
    if (selection.get("selected_btc_weight") != .5
            or selection.get("selected_fast_weight") != .5):
        return False
    selected = artifact.get("selected", {})
    for split in ("train", "validation", "test"):
        values = selected.get(split, {})
        episodes = values.get("episodes", {})
        base, stress = values.get("base", {}), values.get("stress", {})
        rolling = episodes.get("daily_trailing_7d_distribution", {})
        base_pf_floor = 1.05 if split == "train" else 1.0
        checks = (
            5 <= episodes.get("episodes_per_week", 0) <= 10,
            base.get("net_return_pct", 0) > 0,
            (base.get("profit_factor") or 0) > base_pf_floor,
            stress.get("net_return_pct", 0) > 0,
            (stress.get("profit_factor") or 0) > 1,
            base.get("max_drawdown_pct", 100) <= 20,
            stress.get("max_drawdown_pct", 100) <= 20,
            5 <= rolling.get("median", 0) <= 10,
            rolling.get("target_window_ratio", 0) >= .5,
            rolling.get("zero_window_ratio", 1) <= .1,
        )
        if not all(checks):
            return False
    return artifact.get("contract", {}).get("live_execution") is False


def parity_artifact_pass(artifact: dict) -> bool:
    splits = artifact.get("splits", {})
    return bool(artifact.get("passed") is True and
                all(splits.get(name, {}).get("passed") is True
                    for name in ("train", "validation", "test")))


def paper_artifact_pass(artifact: dict) -> bool:
    return bool(artifact.get("passed") is True
                and artifact.get("parity", {}).get("passed") is True
                and artifact.get("lifecycle", {}).get("passed") is True)


def fast_events(db_path: Path) -> tuple[list[dict], dict]:
    events = []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT l.ts, l.trade_id, l.accounting, e.payload "
            "FROM equity_ledger l JOIN event_log e ON e.trade_id=l.trade_id "
            "AND e.type='ENTRY' ORDER BY l.id"
        ).fetchall()
        for ts, trade_id, accounting_raw, entry_raw in rows:
            accounting, entry = json.loads(accounting_raw), json.loads(entry_raw)
            capital_fraction = float(entry["capital_fraction"])
            base_return = float(accounting["net_equity_return"])
            stress_return = base_return - capital_fraction * (
                FAST_STRESS_COST_PCT - FAST_BASE_COST_PCT
            ) / 100
            events.append({"ts": pd.Timestamp(ts), "sleeve": "fast",
                           "base_return": base_return, "stress_return": stress_return,
                           "trade_id": trade_id})
        observations = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload FROM event_log WHERE type='PAPER_OBSERVATION' ORDER BY id"
        )]
        counts = {
            "observed_hours": len(observations),
            "closed_trades": len(rows),
            "independent_risk_episodes": conn.execute(
                "SELECT COUNT(DISTINCT ts) FROM event_log WHERE type='ENTRY'"
            ).fetchone()[0],
            "open_positions": conn.execute(
                "SELECT COUNT(*) FROM position_state WHERE status='IN_POSITION'"
            ).fetchone()[0],
            "input_snapshots": conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE type='PAPER_INPUT_SNAPSHOT'"
            ).fetchone()[0],
            "hourly_input_features": sum(
                1 for (lineage,) in conn.execute("SELECT lineage FROM feature_snapshot")
                if json.loads(lineage).get("snapshot_type") == "hourly_input"
            ),
        }
        entry_times = [pd.Timestamp(row[0]) for row in conn.execute(
            "SELECT DISTINCT ts FROM event_log WHERE type='ENTRY' ORDER BY ts"
        )]
    signal_times = sorted({pd.Timestamp(item["signal_ts"]) for item in observations})
    counts["signal_times"] = signal_times
    counts["entry_times"] = entry_times
    return events, counts


def btc_events(db_path: Path) -> tuple[list[dict], dict]:
    events = []
    with sqlite3.connect(db_path) as conn:
        rows = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload FROM event_log WHERE type='PAPER_OBSERVATION' ORDER BY id"
        )]
        for payload in rows:
            if payload.get("bootstrap") or "base_return" not in payload:
                continue
            events.append({
                "ts": pd.Timestamp(payload["signal_ts"]) + pd.Timedelta(days=1),
                "sleeve": "btc", "base_return": float(payload["base_return"]),
                "stress_return": float(payload["stress_return"]),
            })
        counts = {
            "observed_days": len(rows),
            "closed_trades": conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0],
            "open_positions": conn.execute(
                "SELECT COUNT(*) FROM position_state WHERE status='IN_POSITION'"
            ).fetchone()[0],
        }
    return events, counts


def composite_event_metrics(fast: list[dict], btc: list[dict]) -> tuple[dict, dict, dict]:
    grouped = defaultdict(list)
    for event in fast + btc:
        grouped[pd.Timestamp(event["ts"])].append(event)
    sleeves = {
        "base": {"fast": INITIAL_SLEEVE_EQUITY, "btc": INITIAL_SLEEVE_EQUITY},
        "stress": {"fast": INITIAL_SLEEVE_EQUITY, "btc": INITIAL_SLEEVE_EQUITY},
    }
    returns = {"base": [], "stress": []}
    for timestamp in sorted(grouped):
        for scenario in ("base", "stress"):
            before = sum(sleeves[scenario].values())
            for event in grouped[timestamp]:
                sleeve = event["sleeve"]
                sleeves[scenario][sleeve] *= 1 + event[f"{scenario}_return"]
            after = sum(sleeves[scenario].values())
            returns[scenario].append(after / before - 1)
    return profit_metrics(returns["base"]), profit_metrics(returns["stress"]), sleeves


def frequency_metrics(signal_times: list[pd.Timestamp],
                      entry_times: list[pd.Timestamp]) -> dict:
    if not signal_times:
        return {"observed_span_days": 0.0, "coverage_ratio": 0.0,
                "episodes_per_week": 0.0, "daily_trailing_7d": None}
    start, end = signal_times[0], signal_times[-1] + pd.Timedelta(hours=1)
    hours = max(1.0, (end - start).total_seconds() / 3600)
    days = pd.date_range(
        start.normalize() + pd.Timedelta(days=7), end.normalize(), freq="1D",
        inclusive="left",
    )
    episodes = pd.DatetimeIndex(entry_times)
    rolling = np.asarray([
        int(((episodes > day - pd.Timedelta(days=7)) & (episodes <= day)).sum())
        for day in days
    ], dtype=float)
    distribution = None
    if len(rolling):
        quantiles = np.quantile(rolling, [.05, .50, .95])
        distribution = {
            "complete_windows": int(len(rolling)),
            "p05": float(quantiles[0]), "median": float(quantiles[1]),
            "p95": float(quantiles[2]), "maximum": float(rolling.max()),
            "target_window_ratio": float(((rolling >= 5) & (rolling <= 10)).mean()),
            "zero_window_ratio": float((rolling == 0).mean()),
        }
    return {
        "observed_span_days": hours / 24,
        "coverage_ratio": len(signal_times) / hours,
        "episodes_per_week": len(entry_times) / (hours / (24 * 7)),
        "observed_hours": len(signal_times),
        "daily_trailing_7d": distribution,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path,
                        default=Path("data/backtests/composite_forward_status.json"))
    parser.add_argument("--maximum-age-hours", type=float, default=2.5)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    backtests = root / "data/backtests"
    fast_status = read_json(backtests / "funding_crowding_forward_status.json")
    btc_status = read_json(backtests / "btc_spot_trend_forward_status.json")
    package = read_json(root / "data/strategy_packages/composite_btc_trend_funding_crowding_v1.json")
    historical = read_json(backtests / "composite_btc_trend_funding_crowding_5y.json")
    parity = read_json(backtests / "funding_crowding_runtime_parity_5y.json")
    fast_paper = read_json(backtests / "funding_crowding_paper_5y.json")
    btc_paper = read_json(backtests / "btc_spot_trend_paper_9y.json")

    fast, fast_counts = fast_events(root / "data/state_funding_crowding_forward.db")
    btc, btc_counts = btc_events(root / "data/state_btc_spot_trend_forward.db")
    base, stress, sleeves = composite_event_metrics(fast, btc)
    signal_times = fast_counts.pop("signal_times")
    entry_times = fast_counts.pop("entry_times")
    frequency = frequency_metrics(signal_times, entry_times)
    now = pd.Timestamp.now(tz="UTC")
    ages = {
        "fast_hours": (now - pd.Timestamp(fast_status["observed_at"])).total_seconds() / 3600,
        "btc_hours": (now - pd.Timestamp(btc_status["observed_at"])).total_seconds() / 3600,
    }

    static_checks = {
        "package_contract": package.get("package_id") == PACKAGE_ID
                            and package.get("status") == "PAPER_CHALLENGER"
                            and package.get("signal_policy", {}).get("news") == "disabled"
                            and package.get("signal_policy", {}).get("sentiment_feed") == "disabled",
        "live_execution_off": package.get("live_execution") is False
                              and fast_status.get("live_execution") is False
                              and btc_status.get("live_execution") is False,
        "historical_validation_test_cost_stress": historical_metrics_pass(historical),
        "funding_runtime_parity": parity_artifact_pass(parity),
        "funding_paper_lifecycle": paper_artifact_pass(fast_paper),
        "btc_paper_lifecycle": paper_artifact_pass(btc_paper),
    }
    integrity_checks = {
        "fresh_status_age": all(0 <= age <= args.maximum_age_hours for age in ages.values()),
        "paper_modes": fast_status.get("mode") == "FRESH_FORWARD_PAPER_NO_ORDER"
                       and btc_status.get("mode") == "FRESH_FORWARD_PAPER_NO_ORDER",
        "fast_status_matches_db": fast_status.get("forward_progress") == {
            key: fast_counts[key] for key in (
                "observed_hours", "closed_trades", "independent_risk_episodes",
                "input_snapshots", "hourly_input_features",
            )
        } and len(fast_status.get("open_positions", [])) == fast_counts["open_positions"],
        "complete_hourly_input_lineage": (
            fast_counts["input_snapshots"] == fast_counts["observed_hours"]
            and fast_counts["hourly_input_features"]
            == fast_counts["observed_hours"] * len(fast_status.get("universe", []))
        ),
        "btc_status_matches_db": btc_status.get("progress") == btc_counts,
        "base_equity_reconciles": abs(
            float(fast_status["fast_sleeve_equity_usd"]) - sleeves["base"]["fast"]
        ) < 1e-6 and abs(float(btc_status["base_equity_usd"]) - sleeves["base"]["btc"]) < 1e-6,
        "stress_equity_reconciles": abs(
            float(btc_status["stress_equity_usd"]) - sleeves["stress"]["btc"]
        ) < 1e-6,
    }
    promotion_checks = {
        "minimum_28_observed_days": frequency["observed_span_days"] >= 28,
        "minimum_90pct_hourly_coverage": frequency["coverage_ratio"] >= .90,
        "minimum_30_closed_fast_trades": fast_counts["closed_trades"] >= 30,
        "minimum_30_independent_episodes": fast_counts["independent_risk_episodes"] >= 30,
        "frequency_5_to_10_per_week": 5 <= frequency["episodes_per_week"] <= 10,
        "rolling_median_5_to_10": frequency["daily_trailing_7d"] is not None
                                  and 5 <= frequency["daily_trailing_7d"]["median"] <= 10,
        "rolling_target_windows_at_least_50pct": frequency["daily_trailing_7d"] is not None
                                                 and frequency["daily_trailing_7d"]["target_window_ratio"] >= .50,
        "rolling_zero_windows_at_most_10pct": frequency["daily_trailing_7d"] is not None
                                              and frequency["daily_trailing_7d"]["zero_window_ratio"] <= .10,
        "base_net_positive": base["net_return_pct"] > 0,
        "base_profit_factor_above_one": pf_pass(base),
        "stress_net_positive": stress["net_return_pct"] > 0,
        "stress_profit_factor_above_one": pf_pass(stress),
        "base_drawdown_at_most_20pct": base["max_drawdown_pct"] <= 20,
        "stress_drawdown_at_most_20pct": stress["max_drawdown_pct"] <= 20,
        "fast_positions_flat_for_snapshot": fast_counts["open_positions"] == 0,
    }
    collection_healthy = all(static_checks.values()) and all(integrity_checks.values())
    promotion_ready = collection_healthy and all(promotion_checks.values())
    output = {
        "package_id": PACKAGE_ID, "mode": "COMPOSITE_FRESH_FORWARD_PAPER_NO_ORDER",
        "live_execution": False, "observed_at": now.isoformat(),
        "collection_healthy": collection_healthy, "promotion_ready": promotion_ready,
        "static_checks": static_checks, "integrity_checks": integrity_checks,
        "promotion_checks": promotion_checks, "status_age": ages,
        "progress": {"fast": fast_counts, "btc": btc_counts, "frequency": frequency},
        "performance": {"base": base, "stress": stress, "sleeve_equity": sleeves},
    }
    output_path = args.output if args.output.is_absolute() else root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    if not collection_healthy or (args.require_ready and not promotion_ready):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
