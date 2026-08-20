"""Deterministic regression tests for the aggregate-L2 maker fill model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.l2_maker import AggregateBook, PassiveOrder, signed_markout_bps


def test_same_price_trade_consumes_queue_before_order() -> None:
    order = PassiveOrder("BUY", 100.0, 2.0, 5.0, 0)
    assert order.apply_trade("sell", 100.0, 4.0, 1) == 0
    assert order.queue_ahead == 1
    assert order.apply_trade("sell", 100.0, 2.5, 2) == 1.5
    assert order.remaining_quantity == .5


def test_cancellation_does_not_advance_queue() -> None:
    book = AggregateBook()
    book.update("snapshot", [["100", "5", "1"]], [["101", "3", "1"]], 0)
    order = book.post_at_touch("BUY", 1, 0)
    book.update("update", [["100", "1", "1"]], [], 1)
    assert order.queue_ahead == 5
    assert order.apply_trade("sell", 100, 1, 2) == 0
    assert order.queue_ahead == 4


def test_trade_through_forces_complete_fill() -> None:
    order = PassiveOrder("BUY", 100.0, 2.0, 500.0, 0)
    assert order.apply_trade("sell", 99.9, .1, 1) == 2
    assert order.complete
    assert order.average_fill_price == 100


def test_wrong_aggressor_and_price_do_not_fill() -> None:
    buy = PassiveOrder("BUY", 100.0, 1.0, 0.0, 0)
    sell = PassiveOrder("SELL", 101.0, 1.0, 0.0, 0)
    assert buy.apply_trade("buy", 99, 10, 1) == 0
    assert buy.apply_trade("sell", 101, 10, 1) == 0
    assert sell.apply_trade("sell", 102, 10, 1) == 0
    assert sell.apply_trade("buy", 100, 10, 1) == 0


def test_book_delta_and_markout_symmetry() -> None:
    book = AggregateBook()
    book.update("snapshot", [["100", "5", "1"]], [["101", "3", "1"]], 0)
    book.update("update", [["100", "0", "0"], ["99", "7", "2"]],
                [["100.5", "2", "1"]], 1)
    assert book.best_bid == 99
    assert book.best_ask == 100.5
    assert round(signed_markout_bps("BUY", 100, 101), 8) == 100
    assert round(signed_markout_bps("SELL", 100, 99), 8) == 100


def test_passive_offset_rounds_away_from_market() -> None:
    book = AggregateBook()
    book.update("snapshot", [["100", "5", "1"]], [["100.1", "3", "1"]], 0)
    buy = book.post_passive("BUY", 1, 0, offset_bps=5)
    sell = book.post_passive("SELL", 1, 0, offset_bps=5)
    assert buy.price == 99.9
    assert sell.price == 100.2
    assert buy.queue_ahead == sell.queue_ahead == 0


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS {len(tests)} tests")
