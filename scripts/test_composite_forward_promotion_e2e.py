"""Synthetic E2E proving every composite forward promotion gate can pass."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


SCHEMA = """
CREATE TABLE event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, trade_id TEXT, type TEXT, payload TEXT
);
CREATE TABLE equity_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, trade_id TEXT, accounting TEXT
);
CREATE TABLE position_state (trade_id TEXT, status TEXT);
CREATE TABLE feature_snapshot (id INTEGER PRIMARY KEY AUTOINCREMENT, lineage TEXT);
"""


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


REQUIRED_ARTIFACTS = (
    "composite_btc_trend_funding_crowding_5y.json",
    "funding_crowding_runtime_parity_5y.json",
    "funding_crowding_paper_5y.json",
    "btc_spot_trend_paper_9y.json",
    "composite_btc_trend_funding_crowding_v1.json",
)

PROJECT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = PROJECT / "tests" / "fixtures" / "composite_forward"

#: base filename -> the gitignored real-data subdirectory it normally lives in.
ARTIFACT_SUBDIR = {
    "composite_btc_trend_funding_crowding_5y.json": "data/backtests",
    "funding_crowding_runtime_parity_5y.json": "data/backtests",
    "funding_crowding_paper_5y.json": "data/backtests",
    "btc_spot_trend_paper_9y.json": "data/backtests",
    "composite_btc_trend_funding_crowding_v1.json": "data/strategy_packages",
}


def artifact_path(filename: str) -> Path:
    """Prefer the real data artifact when present; otherwise the committed fixture.

    The real result files live under ``data/backtests/`` and
    ``data/strategy_packages/``, which are gitignored and rejected by the
    ``guard-secrets`` CI job, so they are never present on a clean checkout. The
    committed fixtures under ``tests/fixtures/composite_forward/`` are small
    stand-ins that let this test run on a clean clone. Anyone who has the real
    artifacts keeps using them unchanged — they take precedence.
    """
    real = PROJECT / ARTIFACT_SUBDIR[filename] / filename
    if real.exists():
        return real
    return FIXTURE_DIR / filename


def missing_artifacts() -> list[str]:
    """Artifacts absent from BOTH the real data location and the fixture set.

    With the fixture files committed this should be empty on any checkout; it only
    reports names if the fixtures themselves are deleted.
    """
    return [name for name in REQUIRED_ARTIFACTS if not artifact_path(name).exists()]


def main() -> None:
    now = pd.Timestamp.now(tz="UTC").floor("h")
    signal_times = pd.date_range(end=now, periods=28 * 24, freq="1h")
    entry_indices = np.linspace(0, len(signal_times) - 2, 30, dtype=int)
    base_returns = [.004 if index % 3 else -.001 for index in range(30)]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        backtests = root / "data/backtests"
        package_dir = root / "data/strategy_packages"
        package_dir.mkdir(parents=True)
        for name in (
            "composite_btc_trend_funding_crowding_5y.json",
            "funding_crowding_runtime_parity_5y.json",
            "funding_crowding_paper_5y.json", "btc_spot_trend_paper_9y.json",
        ):
            backtests.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifact_path(name), backtests / name)
        shutil.copy2(
            artifact_path("composite_btc_trend_funding_crowding_v1.json"),
            package_dir / "composite_btc_trend_funding_crowding_v1.json",
        )

        fast_db = root / "data/state_funding_crowding_forward.db"
        fast_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(fast_db) as conn:
            conn.executescript(SCHEMA)
            for timestamp in signal_times:
                conn.execute(
                    "INSERT INTO event_log(ts,type,payload) VALUES (?,?,?)",
                    (timestamp.isoformat(), "PAPER_OBSERVATION",
                     json.dumps({"signal_ts": timestamp.isoformat()})),
                )
                conn.execute(
                    "INSERT INTO event_log(ts,type,payload) VALUES (?,?,?)",
                    (timestamp.isoformat(), "PAPER_INPUT_SNAPSHOT", "{}"),
                )
                conn.executemany(
                    "INSERT INTO feature_snapshot(lineage) VALUES (?)",
                    [(json.dumps({"snapshot_type": "hourly_input"}),)] * 15,
                )
            for sequence, (offset, net_return) in enumerate(zip(entry_indices, base_returns)):
                timestamp = signal_times[offset] + pd.Timedelta(hours=1)
                trade_id = f"fast-{sequence}"
                conn.execute(
                    "INSERT INTO event_log(ts,trade_id,type,payload) VALUES (?,?,?,?)",
                    (timestamp.isoformat(), trade_id, "ENTRY",
                     json.dumps({"capital_fraction": .25})),
                )
                conn.execute(
                    "INSERT INTO equity_ledger(ts,trade_id,accounting) VALUES (?,?,?)",
                    ((timestamp + pd.Timedelta(hours=1)).isoformat(), trade_id,
                     json.dumps({"net_equity_return": net_return})),
                )
        fast_equity = 250 * float(np.prod(1 + np.asarray(base_returns)))
        write_json(backtests / "funding_crowding_forward_status.json", {
            "mode": "FRESH_FORWARD_PAPER_NO_ORDER", "live_execution": False,
            "observed_at": now.isoformat(), "fast_sleeve_equity_usd": fast_equity,
            "forward_progress": {"observed_hours": len(signal_times),
                                 "closed_trades": 30,
                                 "independent_risk_episodes": 30,
                                 "input_snapshots": len(signal_times),
                                 "hourly_input_features": len(signal_times) * 15},
            "universe": [f"ASSET{index}" for index in range(15)],
            "open_positions": [],
        })

        btc_db = root / "data/state_btc_spot_trend_forward.db"
        btc_returns, btc_stress = [.0005] * 28, [.0004] * 28
        with sqlite3.connect(btc_db) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO event_log(ts,type,payload) VALUES (?,?,?)",
                (signal_times[0].isoformat(), "PAPER_OBSERVATION",
                 json.dumps({"bootstrap": True})),
            )
            for day, (base_return, stress_return) in enumerate(zip(btc_returns, btc_stress)):
                signal = signal_times[0].normalize() + pd.Timedelta(days=day)
                conn.execute(
                    "INSERT INTO event_log(ts,type,payload) VALUES (?,?,?)",
                    (signal.isoformat(), "PAPER_OBSERVATION", json.dumps({
                        "bootstrap": False, "signal_ts": signal.isoformat(),
                        "base_return": base_return, "stress_return": stress_return,
                    })),
                )
        write_json(backtests / "btc_spot_trend_forward_status.json", {
            "mode": "FRESH_FORWARD_PAPER_NO_ORDER", "live_execution": False,
            "observed_at": now.isoformat(),
            "base_equity_usd": 250 * float(np.prod(1 + np.asarray(btc_returns))),
            "stress_equity_usd": 250 * float(np.prod(1 + np.asarray(btc_stress))),
            "progress": {"observed_days": 29, "closed_trades": 0, "open_positions": 0},
        })

        script = Path(__file__).with_name("verify_composite_forward_promotion.py")
        output = backtests / "composite_forward_status.json"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        subprocess.run([
            sys.executable, str(script), "--root", str(root), "--output", str(output),
            "--require-ready",
        ], check=True, env=env, capture_output=True, text=True)
        status = json.loads(output.read_text())
        assert status["collection_healthy"] is True
        assert status["promotion_ready"] is True
        assert all(status["promotion_checks"].values())
    print("PASS synthetic composite forward promotion E2E")


if __name__ == "__main__":
    absent = missing_artifacts()
    if absent:
        print("SKIP synthetic composite forward promotion E2E — artifacts absent:")
        for name in absent:
            print(f"  {name}")
    else:
        main()
