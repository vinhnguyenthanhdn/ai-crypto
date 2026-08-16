"""Generate minimal, readable fixtures for the composite forward promotion E2E test.

The real result files live under ``data/backtests/`` and ``data/strategy_packages/``,
which ``guard-secrets`` CI deliberately forbids committing. These fixtures live
outside ``data/`` (under ``tests/fixtures/composite_forward/``) and are small,
hand-derivable JSON documents that satisfy every STATIC gate in
``scripts/verify_composite_forward_promotion.py``.

The E2E test synthesizes its own SQLite state DBs + fresh status files (that part
is unchanged), so only these five static artifact shapes need to be present.

Every value below is written out explicitly so a reviewer can read the diff and
see where it comes from; there are no binary blobs.

Run:  python tests/fixtures/composite_forward/generate_fixtures.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent


def dump(name: str, value: dict) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {name}")


def split_metrics() -> dict:
    """One train/validation/test block that clears historical_metrics_pass().

    Gates per split (verify script, historical_metrics_pass):
      5 <= episodes_per_week <= 10        -> 7
      base.net_return_pct > 0             -> 12.0
      base.profit_factor > 1.05 (train)   -> 1.40
                  or > 1.0 (val/test)     -> 1.40
      stress.net_return_pct > 0           -> 8.0
      stress.profit_factor > 1            -> 1.20
      base/stress.max_drawdown_pct <= 20  -> 5.0
      rolling median in 5..10             -> 7
      rolling target_window_ratio >= .5   -> 1.0
      rolling zero_window_ratio <= .1     -> 0.0
    Same numbers are valid for all three splits (train just needs PF>1.05).
    """
    return {
        "episodes": {
            "episodes_per_week": 7,
            "daily_trailing_7d_distribution": {
                "complete_windows": 30,
                "p05": 5.0,
                "median": 7.0,
                "p95": 9.0,
                "maximum": 10.0,
                "target_window_ratio": 1.0,
                "zero_window_ratio": 0.0,
            },
        },
        "base": {
            "net_return_pct": 12.0,
            "profit_factor": 1.40,
            "max_drawdown_pct": 5.0,
        },
        "stress": {
            "net_return_pct": 8.0,
            "profit_factor": 1.20,
            "max_drawdown_pct": 5.0,
        },
    }


def main() -> None:
    # 1. Strategy package — satisfies package_contract + live_execution_off.
    dump("composite_btc_trend_funding_crowding_v1.json", {
        "package_id": "composite_btc_trend_funding_crowding_v1",
        "status": "PAPER_CHALLENGER",
        "live_execution": False,
        "signal_policy": {
            "news": "disabled",
            "sentiment_feed": "disabled",
        },
    })

    # 2. Historical result — satisfies historical_metrics_pass.
    dump("composite_btc_trend_funding_crowding_5y.json", {
        "historical_pass": True,
        "live_execution": False,
        "contract": {"live_execution": False},
        "selection": {
            "selected_btc_weight": 0.5,
            "selected_fast_weight": 0.5,
        },
        "selected": {
            "train": split_metrics(),
            "validation": split_metrics(),
            "test": split_metrics(),
        },
    })

    # 3. Runtime parity — satisfies parity_artifact_pass.
    dump("funding_crowding_runtime_parity_5y.json", {
        "passed": True,
        "splits": {
            "train": {"passed": True},
            "validation": {"passed": True},
            "test": {"passed": True},
        },
    })

    # 4 + 5. Paper lifecycle (fast sleeve and BTC sleeve) — satisfy paper_artifact_pass.
    paper = {
        "passed": True,
        "parity": {"passed": True},
        "lifecycle": {"passed": True},
    }
    dump("funding_crowding_paper_5y.json", paper)
    dump("btc_spot_trend_paper_9y.json", paper)


if __name__ == "__main__":
    main()
