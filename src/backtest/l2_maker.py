"""Conservative L2 queue model for post-only maker research.

Aggregate L2 cannot identify an order's exact queue position.  The model puts
our order behind all displayed size and only advances that queue with public
trades.  Book cancellations never improve the estimated position.
"""
from dataclasses import dataclass
import math


@dataclass
class PassiveOrder:
    side: str
    price: float
    quantity: float
    queue_ahead: float
    created_at_ms: int
    filled_quantity: float = 0.0
    fill_notional: float = 0.0
    first_fill_at_ms: int | None = None
    last_fill_at_ms: int | None = None

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def complete(self) -> bool:
        return self.remaining_quantity <= 1e-12

    @property
    def average_fill_price(self) -> float | None:
        if self.filled_quantity <= 0:
            return None
        return self.fill_notional / self.filled_quantity

    def apply_trade(self, aggressor_side: str, trade_price: float,
                    trade_quantity: float, timestamp_ms: int) -> float:
        """Apply one public trade and return newly filled quantity.

        ``aggressor_side`` follows the exchange convention: a sell aggressor
        consumes bids and a buy aggressor consumes asks.
        """
        if self.complete or trade_quantity <= 0:
            return 0.0
        if self.side == "BUY":
            relevant = aggressor_side == "sell" and trade_price <= self.price
            through = trade_price < self.price
        elif self.side == "SELL":
            relevant = aggressor_side == "buy" and trade_price >= self.price
            through = trade_price > self.price
        else:
            raise ValueError("side must be BUY or SELL")
        if not relevant:
            return 0.0

        available = trade_quantity
        if not through:
            consumed_ahead = min(self.queue_ahead, available)
            self.queue_ahead -= consumed_ahead
            available -= consumed_ahead
        else:
            # A print beyond our price implies the market order swept our
            # entire level; displayed queue ahead cannot remain.
            self.queue_ahead = 0.0
            available = self.remaining_quantity

        filled = min(self.remaining_quantity, available)
        if filled > 0:
            self.filled_quantity += filled
            self.fill_notional += filled * self.price
            self.first_fill_at_ms = self.first_fill_at_ms or timestamp_ms
            self.last_fill_at_ms = timestamp_ms
        return filled


class AggregateBook:
    """Minimal price-level book reconstructed from OKX snapshot/deltas."""

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.timestamp_ms: int | None = None

    @staticmethod
    def _apply(levels: dict[float, float], updates: list[list[str]]) -> None:
        for raw in updates:
            price, quantity = float(raw[0]), float(raw[1])
            if quantity == 0:
                levels.pop(price, None)
            else:
                levels[price] = quantity

    def update(self, action: str, bids: list[list[str]], asks: list[list[str]],
               timestamp_ms: int) -> None:
        if action == "snapshot":
            self.bids.clear()
            self.asks.clear()
        elif action != "update":
            raise ValueError(f"unsupported book action: {action}")
        self._apply(self.bids, bids)
        self._apply(self.asks, asks)
        self.timestamp_ms = timestamp_ms

    @property
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def post_at_touch(self, side: str, quantity: float, timestamp_ms: int,
                      queue_multiplier: float = 1.0) -> PassiveOrder:
        return self.post_passive(side, quantity, timestamp_ms, 0.0, queue_multiplier)

    def post_passive(self, side: str, quantity: float, timestamp_ms: int,
                     offset_bps: float = 0.0, queue_multiplier: float = 1.0,
                     tick_size: float = 0.1) -> PassiveOrder:
        """Post at touch or farther from the market by ``offset_bps``."""
        if quantity <= 0 or queue_multiplier < 1:
            raise ValueError("quantity must be positive and queue_multiplier >= 1")
        if offset_bps < 0 or tick_size <= 0:
            raise ValueError("offset_bps must be non-negative and tick_size positive")
        if side == "BUY":
            touch = self.best_bid
            levels = self.bids
        elif side == "SELL":
            touch = self.best_ask
            levels = self.asks
        else:
            raise ValueError("side must be BUY or SELL")
        if touch is None:
            raise ValueError("cannot post on an empty book")
        raw_price = touch * (1 - offset_bps / 10_000) if side == "BUY" else touch * (
            1 + offset_bps / 10_000
        )
        ticks = math.floor(raw_price / tick_size + 1e-9) if side == "BUY" else math.ceil(
            raw_price / tick_size - 1e-9
        )
        decimals = max(0, int(round(-math.log10(tick_size))))
        price = round(ticks * tick_size, decimals)
        return PassiveOrder(
            side=side, price=price, quantity=quantity,
            queue_ahead=levels.get(price, 0.0) * queue_multiplier,
            created_at_ms=timestamp_ms,
        )


def signed_markout_bps(side: str, fill_price: float, future_mid: float) -> float:
    """Return positive markout when the post-fill move benefits the maker."""
    direction = 1.0 if side == "BUY" else -1.0 if side == "SELL" else None
    if direction is None:
        raise ValueError("side must be BUY or SELL")
    return direction * (future_mid / fill_price - 1.0) * 10_000
