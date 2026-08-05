"""CLI sinh AI Review cho 1 lần backtest đã chạy (xem plan-02.md mục 11, 13.6).

Usage:
    python scripts/review_backtest.py <name> --backtest-json path/to/result.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ai_review  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--backtest-json", required=True, help="Path tới output JSON của scripts/run_backtest.py")
    args = parser.parse_args()

    result = json.loads(Path(args.backtest_json).read_text(encoding="utf-8"))
    params = {
        "symbol": result.get("symbol"),
        "timeframe": result.get("timeframe"),
        "fee_pct": result.get("fee_pct"),
        "slippage_pct": result.get("slippage_pct"),
    }
    path = ai_review.review_backtest(args.name, result, params)
    print(f"Đã ghi review vào {path}")


if __name__ == "__main__":
    main()
