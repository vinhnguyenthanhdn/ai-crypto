"""Deterministic regression cho production staggered-pullback core."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, run as runtime  # noqa: E402
from src.engine import staggered_pullback as strategy  # noqa: E402


failures = []
count = 0


def check(name, fn):
    global count
    count += 1
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        failures.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")


def test_flag_off_by_default():
    assert config.STAGGERED_PULLBACK_ENABLED is False


def test_execution_flag_fails_closed_before_live_parity():
    original = config.STAGGERED_PULLBACK_ENABLED
    config.STAGGERED_PULLBACK_ENABLED = True
    try:
        try:
            runtime.main()
        except RuntimeError as exc:
            assert "two-sided lifecycle parity" in str(exc)
        else:
            raise AssertionError("runtime phải fail-closed khi bật flag chưa được authorize")
    finally:
        config.STAGGERED_PULLBACK_ENABLED = original


def test_frozen_contract():
    assert strategy.FROZEN_CONTRACT.manifest() == {
        "timeframe": "4h", "source_timeframe": "5m", "z_lookback_bars": 60,
        "trend_ema_bars": 180, "entry_z": 2.0, "exit_z": 0.5,
        "stop_atr": 5.0, "atr_bars": 14, "max_tranches": 5,
        "round_trip_cost_pct": 0.30,
    }


def test_aggregate_drops_incomplete_bucket():
    ts = pd.date_range("2026-01-01", periods=49, freq="5min")
    source = pd.DataFrame({
        "ts": ts, "open": range(49), "high": range(1, 50), "low": range(49),
        "close": range(1, 50),
    })
    bars = strategy.aggregate_closed_4h(source)
    assert len(bars) == 1
    assert bars.iloc[0]["open"] == 0 and bars.iloc[0]["close"] == 48


def test_entry_and_exit_are_side_symmetric():
    long = pd.Series({"z": -2.1, "atr": 1.0, "close": 101.0, "trend_ema": 100.0})
    short = pd.Series({"z": 2.1, "atr": 1.0, "close": 99.0, "trend_ema": 100.0})
    assert strategy.entry_signal(long) == "LONG"
    assert strategy.entry_signal(short) == "SHORT"
    assert strategy.exit_signal("LONG", 0.5)
    assert strategy.exit_signal("SHORT", -0.5)


def test_tranche_plan_caps_total_risk_and_capital():
    plan = strategy.compute_tranche_plan(100, 2, 500, side="LONG")
    assert plan["stop_price"] == 90
    assert plan["risk_usd"] == 1.0  # 1% của $500 / 5 tranche
    assert plan["size_usd"] == 10
    assert plan["capital_cap_usd"] == 100
    near = strategy.compute_tranche_plan(100, 0.2, 500, side="SHORT")
    assert near["stop_price"] == 101
    assert near["size_usd"] == 100  # cap 20% vốn/tranche
    assert near["risk_usd"] == 1.0
    exhausted = strategy.compute_tranche_plan(
        100, 2, 500, side="LONG", committed_excursion_risk_usd=5,
    )
    assert exhausted["size_usd"] == 0 and exhausted["risk_usd"] == 0


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        check(name, fn)

print(f"\n{count - len(failures)}/{count} passed")
if failures:
    raise SystemExit(1)
