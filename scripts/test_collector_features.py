"""Regression deterministic cho raw microstructure samples."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collector_ws import _order_book_sample


def main():
    sample = _order_book_sample({
        "timestamp": 123,
        "bids": [[100.0, 3.0], [99.0, 1.0]],
        "asks": [[101.0, 2.0], [102.0, 2.0]],
    })
    assert sample["bid_volume"] == 4.0
    assert sample["ask_volume"] == 4.0
    assert sample["imbalance"] == 0.0
    assert sample["best_bid"] == 100.0 and sample["best_ask"] == 101.0
    assert round(sample["mid_price"], 2) == 100.5
    assert round(sample["spread_bps"], 6) == round(1 / 100.5 * 10_000, 6)
    assert sample["exchange_timestamp"] == 123

    empty = _order_book_sample({"bids": [], "asks": []})
    assert empty["imbalance"] == 0.0
    assert empty["mid_price"] is None and empty["spread_bps"] is None
    print("=== collector feature regression: 2/2 PASS ===")


if __name__ == "__main__":
    main()
