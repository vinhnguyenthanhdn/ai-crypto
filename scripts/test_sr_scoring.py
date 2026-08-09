"""Deterministic regression cho S/R scoring; không gọi network/DB thật."""
from datetime import datetime, timedelta, timezone
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, run as runtime, state_store  # noqa: E402
from src.backtest import paper_engine  # noqa: E402
from src.engine import decision, risk, support_resistance as sr  # noqa: E402


_failures = []
_count = 0


def check(name, fn):
    global _count
    _count += 1
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        _failures.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")


def frame(low_points=(), high_points=(), n=70, atr=4.0):
    lows = [105.0 + (i % 2) * 0.1 for i in range(n)]
    highs = [108.0 + (i % 2) * 0.1 for i in range(n)]
    for idx, value in low_points:
        lows[idx] = value
    for idx, value in high_points:
        highs[idx] = value
    closes = [(lo + hi) / 2 for lo, hi in zip(lows, highs)]
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="5min"),
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [100.0] * n, "atr": [atr] * n,
    })


def test_causal_confirmation():
    df = frame(low_points=[(20, 100.0)])
    assert not sr.confirmed_swings(df, kind="low", decision_idx=22, window=3)
    swings = sr.confirmed_swings(df, kind="low", decision_idx=23, window=3)
    assert [s.idx for s in swings] == [20]


def test_zone_spread_boundary():
    same = frame(low_points=[(10, 100.0), (20, 101.0)], atr=4.0)
    zone = sr.find_active_zone(same, kind="low", decision_idx=30, required_swings=2)
    assert zone and zone.low == 100.0 and zone.high == 101.0
    apart = frame(low_points=[(10, 100.0), (20, 101.01)], atr=4.0)
    assert sr.find_active_zone(apart, kind="low", decision_idx=30, required_swings=2) is None


def test_touch_quality_and_proximity():
    df = frame(low_points=[(10, 100.0), (20, 100.5)], atr=2.0)
    inside = sr.score(df, 100.25, decision_idx=30, required_swings=2)
    assert inside["buy_score"] == 100.0
    boundary = sr.score(df, 100.5 + 0.09 * 2.0, decision_idx=30, required_swings=2)
    assert abs(boundary["buy_score"] - 70.0) < 1e-6, boundary
    below = sr.score(df, 99.99, decision_idx=30, required_swings=2)
    assert below["buy_score"] > 0.0
    assert not below["buy_eligible"] and below["buy_ineligible_reason"] == "WAIT_RECLAIM"
    one = frame(low_points=[(20, 100.0)], atr=2.0)
    assert sr.score(one, 100.0, decision_idx=30, required_swings=1)["buy_score"] == 50.0


def test_single_swing_partial_score_is_hard_gated():
    df = frame(low_points=[(20, 100.0)], atr=2.0)
    result = sr.score(df, 100.0, decision_idx=30, required_swings=2)
    assert result["support_status"] == "SINGLE_SWING_CANDIDATE"
    assert 0.0 < result["buy_score"] <= 50.0
    assert not result["buy_eligible"]
    assert result["buy_ineligible_reason"] == "NEED_MORE_SWINGS"
    action, _ = decision.decide_support_resistance_entry(
        result["buy_score"], result["sell_score"], threshold=40,
        buy_eligible=result["buy_eligible"],
        ineligible_reason=result["buy_ineligible_reason"],
    )
    assert action == "IGNORE"


def test_far_single_swing_score_reacts_to_price():
    df = frame(low_points=[(20, 100.0)], atr=2.0)
    far = sr.score(df, 103.6, decision_idx=30, required_swings=2)
    closer = sr.score(df, 103.0, decision_idx=30, required_swings=2)
    assert far["buy_score"] > 4.0, far
    assert closer["buy_score"] - far["buy_score"] > 0.5, (far, closer)
    assert not far["buy_eligible"] and not closer["buy_eligible"]


def test_score_floor_without_support():
    result = sr.score(frame(), 500.0, decision_idx=30, required_swings=2)
    assert result["buy_score"] == config.SR_SCORE_FLOOR
    assert result["support_status"] == "NO_SUPPORT"
    assert not result["buy_eligible"]


def test_single_swing_high_fallback_is_target_only():
    df = frame(
        low_points=[(10, 100.0), (20, 100.2)],
        high_points=[(15, 110.0)], atr=2.0,
    )
    result = sr.score(df, 100.2, decision_idx=30, required_swings=2)
    assert result["support_status"] == "CONFIRMED_ZONE"
    assert result["resistance_status"] == "SINGLE_SWING_TARGET"
    assert result["resistance_zone"]["touch_count"] == 1
    plan = sr.compute_position_plan(
        100.2, 2.0, result["support_zone"], result["resistance_zone"],
        fee_pct=0.0, slippage_pct=0.0,
    )
    assert plan["edge_viable"] and plan["take_profit_price"] > 100.2
    assert plan["tp_reason"] == "TAKE_PROFIT_FIB"


def test_missing_resistance_reject_has_safe_gate():
    support, _, _ = zones()
    plan = sr.compute_position_plan(101.0, 2.0, support, None)
    assert not plan["edge_viable"]
    assert plan["reject_gate"] == "missing_resistance"
    assert "tp_distance_pct" not in plan


def test_scheduler_is_start_to_start():
    start = 1000.0
    interval = 3600.0
    next_start = runtime._advance_scheduled_start(start, start + 3000, interval)
    assert next_start == start + interval
    assert next_start - (start + 3000) == 600


def test_sell_only_on_support_breakdown():
    support = {"low": 100.0, "high": 100.5, "touch_count": 2, "swings": []}
    assert sr.breakdown_score(100.0, support, 10.0) == 0.0  # trùng đáy
    assert sr.breakdown_score(98.5, support, 10.0) == 0.0   # fake-break 0.15 ATR
    assert abs(sr.breakdown_score(98.15, support, 10.0) - 70.0) < 1e-6
    assert sr.breakdown_score(98.0, support, 10.0) == 100.0


def zones():
    support = {"low": 100.0, "high": 100.5, "touch_count": 2, "swings": []}
    resistance_near = {"low": 105.0, "high": 105.5, "touch_count": 2, "swings": []}
    resistance_far = {"low": 120.0, "high": 120.5, "touch_count": 2, "swings": []}
    return support, resistance_near, resistance_far


def test_sl_and_direct_tp():
    support, resistance, _ = zones()
    plan = sr.compute_position_plan(
        101.0, 2.0, support, resistance, fee_pct=0.0, slippage_pct=0.0,
        account_equity_usd=500,
    )
    assert plan["edge_viable"]
    assert plan["stop_price"] == 99.6
    assert plan["take_profit_price"] == 105.0
    assert plan["tp_reason"] == "TAKE_PROFIT_DIRECT_HIGH"


def test_fibonacci_tp():
    support, _, resistance = zones()
    plan = sr.compute_position_plan(
        101.0, 2.0, support, resistance, fee_pct=0.0, slippage_pct=0.0,
        account_equity_usd=500,
    )
    assert plan["edge_viable"]
    assert plan["tp_reason"] == "TAKE_PROFIT_FIB"
    assert plan["fib_level"] == 0.382
    assert abs(plan["take_profit_price"] - 107.64) < 1e-6


def test_no_valid_fib_rejects():
    support, _, resistance = zones()
    old = config.SR_MIN_RISK_REWARD
    try:
        config.SR_MIN_RISK_REWARD = 100.0
        plan = sr.compute_position_plan(101.0, 2.0, support, resistance, fee_pct=0, slippage_pct=0)
        assert not plan["edge_viable"]
    finally:
        config.SR_MIN_RISK_REWARD = old


def test_farther_swing_high_unlocks_fibonacci_tp():
    support = {"low": 100.0, "high": 100.2, "touch_count": 2, "swings": []}
    near = {"low": 100.4, "high": 100.4, "touch_count": 2, "swings": []}
    far = {"low": 102.0, "high": 102.0, "touch_count": 1, "swings": []}
    plan = sr.compute_position_plan(
        100.1, 0.5, support, near, resistance_targets=[far],
        fee_pct=0.001, slippage_pct=0.0005,
    )
    assert plan["edge_viable"], plan
    assert plan["tp_reason"] == "TAKE_PROFIT_FIB"
    assert plan["fib_level"] == 0.5
    assert abs(plan["take_profit_price"] - 101.0) < 1e-9
    assert plan["selected_resistance_rank"] == 2


def test_horizon_no_min_and_timeout():
    entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame([
        {"open": 100, "close": 100, "volume": 100, "vol_sma20": 100,
         "macd": 0, "macd_signal": 0, "rsi": 50, "ema20": 101, "ema50": 100},
        {"open": 100, "close": 100, "volume": 100, "vol_sma20": 100,
         "macd": -1, "macd_signal": 0, "rsi": 50, "ema20": 101, "ema50": 100},
    ])
    position = {"entry_time": entry, "stop_price": 90, "take_profit_price": 110}
    exited, reason = decision.decide_exit(position, 100, df, current_time=entry + timedelta(minutes=1))
    assert exited and "MACD" in reason  # không còn minimum hold
    flat = df.copy()
    flat.loc[:, "macd"] = 0
    exited, reason = decision.decide_exit(
        position, 100, flat, current_time=entry + timedelta(minutes=1440),
    )
    assert exited and reason.startswith("TIMEOUT_EXIT")


def test_accounting_reconciles():
    a = risk.compute_trade_accounting(
        100, 102, 400, fee_pct=0.001, slippage_pct=0.0005,
        equity_before_usd=500,
    )
    assert abs(a["equity_after_usd"] - (500 + a["net_pnl_usd"])) < 1e-8
    assert abs(a["return_on_equity_pct"] - a["net_pnl_usd"] / 500 * 100) < 1e-6


def test_live_primitives_match_paper_replay():
    n = 230
    timestamps = pd.date_range("2026-01-01", periods=n, freq="5min")
    lows, highs = [105.0] * n, [110.0] * n
    for idx, value in ((170, 100.0), (180, 100.2)):
        lows[idx] = value
    for idx, value in ((175, 120.0), (185, 120.2)):
        highs[idx] = value
    primary = pd.DataFrame({
        "ts": timestamps, "open": [107.5] * n, "high": highs, "low": lows,
        "close": [107.5] * n, "volume": [100.0] * n,
    })
    tick = pd.DataFrame([
        {"ts": timestamps[210], "open": 100.3, "high": 101.0, "low": 100.3, "close": 100.5, "volume": 10},
        {"ts": timestamps[211], "open": 100.4, "high": 100.4, "low": 95.0, "close": 96.0, "volume": 10},
    ])
    result = paper_engine.run_paper_backtest(
        primary, tick, timeframe="5m", tick_timeframe="1m",
        scoring_profile="support_resistance_only", sr_required_swings=2,
        fee_pct=0.0, slippage_pct=0.0,
    )
    assert result["n_trades"] == 1
    trade = result["trades"][0]
    feature = trade["entry_feature"]
    action, _ = decision.decide_support_resistance_entry(
        feature["buy_score"], feature["sell_score"],
    )
    assert action == "BUY"
    plan = sr.compute_position_plan(
        trade["entry_price"], feature["atr_current"], feature["support_zone"],
        feature["resistance_zone"], fee_pct=0.0, slippage_pct=0.0,
        account_equity_usd=config.ACCOUNT_EQUITY_USD,
    )
    assert plan["stop_price"] == trade["stop_price"]
    assert plan["take_profit_price"] == trade["take_profit_price"]
    expected = risk.compute_trade_accounting(
        trade["entry_price"], trade["exit_price"], trade["size_usd"],
        fee_pct=0.0, slippage_pct=0.0,
        equity_before_usd=config.ACCOUNT_EQUITY_USD,
    )
    assert expected == trade["accounting"]
    assert trade["reason"].startswith("STOP_LOSS")


def test_replay_processes_ticks_after_entry_in_same_subbar():
    n = 230
    timestamps = pd.date_range("2026-01-01", periods=n, freq="5min")
    lows, highs = [105.0] * n, [110.0] * n
    for idx, value in ((170, 100.0), (180, 100.2)):
        lows[idx] = value
    for idx, value in ((175, 120.0), (185, 120.2)):
        highs[idx] = value
    primary = pd.DataFrame({
        "ts": timestamps, "open": [107.5] * n, "high": highs, "low": lows,
        "close": [107.5] * n, "volume": [100.0] * n,
    })
    tick = pd.DataFrame([{
        "ts": timestamps[210], "open": 100.3, "high": 130.0,
        "low": 100.3, "close": 110.0, "volume": 10,
    }])
    result = paper_engine.run_paper_backtest(
        primary, tick, timeframe="5m", tick_timeframe="1m",
        scoring_profile="support_resistance_only", sr_required_swings=2,
        fee_pct=0.0, slippage_pct=0.0,
    )
    assert result["n_trades"] == 1, result
    trade = result["trades"][0]
    assert trade["entry_idx"] == trade["exit_idx"] == 210
    assert pd.Timestamp(trade["exit_time"]) - pd.Timestamp(trade["entry_time"]) < pd.Timedelta(minutes=1)
    assert trade["reason"].startswith("TAKE_PROFIT"), trade


def test_feature_lineage_schema_round_trip():
    original_db = config.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            config.DB_PATH = Path(temp_dir) / "lineage.db"
            lineage = {
                "strategy_package_id": config.STRATEGY_PACKAGE_ID,
                "transformation_version": config.FEATURE_VERSION,
                "engine_version": config.RUNTIME_ENGINE_VERSION,
                "scoring_profile": "support_resistance_only",
                "experiment_id": config.SR_EXPERIMENT_ID,
                "support_resistance_params": config.support_resistance_manifest(),
            }
            state_store.log_feature_snapshot("BTC/USDT", 100.0, {"support_resistance": {}}, lineage)
            row = state_store.get_feature_snapshots("BTC/USDT", limit=1)[0]
            assert row["lineage"] == lineage
    finally:
        config.DB_PATH = original_db


def test_atomic_owner_run_lock():
    original_db = config.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            config.DB_PATH = Path(temp_dir) / "lock.db"
            with state_store.run_lock(stale_after_seconds=60) as owner:
                assert state_store.refresh_run_lock(owner)
                try:
                    with state_store.run_lock(stale_after_seconds=60):
                        raise AssertionError("lock thứ hai không được acquire")
                except state_store.RunAlreadyInProgress:
                    pass
                assert state_store.get_run_lock_status()["owner_token"] == owner
            assert not state_store.get_run_lock_status()["active"]

            # Lease fresh nhưng PID owner đã chết phải được reclaim ngay sau
            # restart cưỡng bức, không chờ hết stale timeout.
            with state_store.get_conn() as conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO run_lock (id,pid,started_at,owner_token,heartbeat_at) "
                    "VALUES (1,99999999,?,'dead-owner',?)",
                    (now, now),
                )
            with state_store.run_lock(stale_after_seconds=3600) as replacement:
                assert replacement != "dead-owner"
    finally:
        config.DB_PATH = original_db


def test_market_freshness_ws_then_rest():
    original_db = config.DB_PATH
    original_fetch = runtime.market.fetch_ticker_snapshot
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            config.DB_PATH = Path(temp_dir) / "freshness.db"
            now = datetime.now(timezone.utc)
            state_store.set_kv(f"last_tick_price_{config.SYMBOL}", 101.0)
            state_store.set_kv(f"last_tick_price_{config.SYMBOL}_at", now.isoformat())
            runtime.market.fetch_ticker_snapshot = lambda *_: (_ for _ in ()).throw(AssertionError("không được gọi REST"))
            assert runtime._current_price(object())["source"] == "collector_ws"

            state_store.set_kv(
                f"last_tick_price_{config.SYMBOL}_at",
                (now - timedelta(minutes=2)).isoformat(),
            )
            runtime.market.fetch_ticker_snapshot = lambda *_: {
                "price": 102.0, "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                "source": "rest_ticker",
            }
            snapshot = runtime._current_price(object())
            assert snapshot["price"] == 102.0 and snapshot["source"] == "rest_ticker"
    finally:
        runtime.market.fetch_ticker_snapshot = original_fetch
        config.DB_PATH = original_db


def test_sr_only_scoring_view_zeros_other_layers():
    layers, weights = runtime._sr_scoring_view(73.25)
    assert layers["support_resistance"] == 73.25
    assert weights["support_resistance"] == 100
    assert all(layers[name] == 0 for name in runtime.LEGACY_SCORE_LAYERS)
    assert all(weights[name] == 0 for name in runtime.LEGACY_SCORE_LAYERS)


def test_sr_monitor_snapshot_zone_distance():
    zone = {
        "kind": "low", "low": 100.0, "high": 101.0, "atr_form": 4.0,
        "touch_count": 2,
        "swings": [{"price": 100.0, "ts": "a"}, {"price": 101.0, "ts": "b"}],
    }
    monitor = runtime._sr_monitor_snapshot(
        103.0,
        {"atr_current": 4.0, "decision_idx": 20, "threshold": 70,
         "buy_score": 25.0, "sell_score": 0.0, "support_zone": zone,
         "resistance_zone": None},
        "BUY_SUPPORT", 25.0, [],
    )
    assert monitor["current_price"] == 103.0
    assert monitor["support"]["relation"] == "ABOVE"
    assert monitor["support"]["distance_usd"] == 2.0
    assert monitor["support"]["distance_atr"] == 0.5
    assert monitor["support"]["swing_prices"] == [100.0, 101.0]
    assert not monitor["resistance"]["available"]


def test_zone_diagnostics_explains_broken_pair():
    df = frame(low_points=[(10, 100.0), (20, 100.2)], atr=4.0)
    df.loc[15, "close"] = 99.0
    diagnostics = sr.zone_diagnostics(
        df, kind="low", decision_idx=30, required_swings=2,
    )
    assert diagnostics["confirmed_swing_count"] == 2
    assert diagnostics["summary"] == "CLOSEST_SAME_ZONE_WAS_BROKEN"
    pair = diagnostics["pairs_shown"][0]
    assert pair["same_zone"] and not pair["unbroken"]
    assert "BROKEN" in pair["reasons"]


def main():
    print(f"=== {config.SR_EXPERIMENT_ID} deterministic regression ===")
    for name, fn in [
        ("causal swing confirmation", test_causal_confirmation),
        ("same-zone ATR boundary", test_zone_spread_boundary),
        ("touch quality + proximity", test_touch_quality_and_proximity),
        ("single swing partial score is hard-gated", test_single_swing_partial_score_is_hard_gated),
        ("far single swing score reacts to price", test_far_single_swing_score_reacts_to_price),
        ("nonzero score floor without support", test_score_floor_without_support),
        ("single swing high fallback is target-only", test_single_swing_high_fallback_is_target_only),
        ("missing resistance rejection has safe gate", test_missing_resistance_reject_has_safe_gate),
        ("scheduler cadence is start-to-start", test_scheduler_is_start_to_start),
        ("SELL only after support breakdown", test_sell_only_on_support_breakdown),
        ("SL lower low + direct TP", test_sl_and_direct_tp),
        ("Fibonacci TP", test_fibonacci_tp),
        ("reject when no Fibonacci target valid", test_no_valid_fib_rejects),
        ("farther swing high unlocks Fibonacci TP", test_farther_swing_high_unlocks_fibonacci_tp),
        ("no minimum hold + 24h timeout", test_horizon_no_min_and_timeout),
        ("USD accounting reconcile", test_accounting_reconciles),
        ("live primitives == paper replay", test_live_primitives_match_paper_replay),
        ("replay keeps post-entry ticks in same sub-bar", test_replay_processes_ticks_after_entry_in_same_subbar),
        ("feature lineage schema round-trip", test_feature_lineage_schema_round_trip),
        ("atomic owner run lock", test_atomic_owner_run_lock),
        ("market freshness WS then REST", test_market_freshness_ws_then_rest),
        ("S/R-only view zeros all other scores", test_sr_only_scoring_view_zeros_other_layers),
        ("S/R monitor zone and distance", test_sr_monitor_snapshot_zone_distance),
        ("zone diagnostics explains rejected pair", test_zone_diagnostics_explains_broken_pair),
    ]:
        check(name, fn)
    print(f"\n=== {_count - len(_failures)}/{_count} PASS ===")
    if _failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
