"""Collector WebSocket 24/7 — Trade Stream + Liquidation.

Chạy như 1 process riêng, độc lập với activation định kỳ của `run.py`:
    python -m src.collector_ws

Chỉ 2 luồng này cần WebSocket (continuous stream):
- Trade Stream: REST poll sẽ bỏ sót trade xảy ra giữa 2 lần gọi → CVD tính sai.
- Liquidation: không có REST endpoint tương đương đủ tốt cho luồng này.

Các dữ liệu còn lại (OHLCV, Order Book, Funding, OI) vẫn REST polling trong
`run.py` — không cần WebSocket. Dùng `ccxt.pro` (đã bundle miễn phí trong ccxt
hiện tại, không phải bản trả phí riêng).
"""
import asyncio
import sys
from datetime import datetime, timezone

import ccxt.pro as ccxtpro

from . import config, state_store
from .data import market

RECONNECT_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 60
CVD_FLUSH_INTERVAL_SECONDS = 30
ORDER_BOOK_SAMPLE_INTERVAL_SECONDS = 30


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _make_exchange():
    exchange_cls = getattr(ccxtpro, config.EXCHANGE_ID)
    return exchange_cls(
        {
            "apiKey": config.EXCHANGE_API_KEY or None,
            "secret": config.EXCHANGE_API_SECRET or None,
            "password": config.EXCHANGE_API_PASSPHRASE or None,
            "enableRateLimit": True,
            "options": {"defaultType": config.MARKET_TYPE},
        }
    )


async def _watch_trades_loop(exchange, symbol, state_symbol=None):
    """Tích luỹ CVD từ trade stream thật, flush định kỳ vào `kv_store`.

    Reconnect có backoff (chống rủi ro "WebSocket rớt kết nối"); đánh dấu
    WS_GAP_START/WS_GAP_END vào event_log để Feature Engine biết loại trừ
    khoảng mất kết nối thay vì tính CVD nhầm trên dữ liệu thiếu.
    """
    state_symbol = state_symbol or symbol
    buy_vol = 0.0
    sell_vol = 0.0
    last_flush = asyncio.get_event_loop().time()
    backoff = RECONNECT_BACKOFF_SECONDS
    gap_open = False

    while True:
        try:
            trades = await exchange.watch_trades(symbol)
            if gap_open:
                state_store.log_event("WS_GAP_END", {"stream": "trades", "symbol": symbol})
                gap_open = False
            backoff = RECONNECT_BACKOFF_SECONDS

            for t in trades:
                side = t.get("side")
                amount = t.get("amount") or 0
                if side == "buy":
                    buy_vol += amount
                elif side == "sell":
                    sell_vol += amount

            # Tick giá thật, ghi ngay mỗi batch — dùng cho cửa sổ theo dõi liên tục
            # của run.py, không đợi chu kỳ flush CVD 30s.
            last_price = trades[-1].get("price") if trades else None
            if last_price is not None:
                state_store.set_kv(f"last_tick_price_{state_symbol}", last_price)
                state_store.set_kv(f"last_tick_price_{state_symbol}_at", _now_iso())

            now = asyncio.get_event_loop().time()
            if now - last_flush >= CVD_FLUSH_INTERVAL_SECONDS:
                total = buy_vol + sell_vol
                cvd = (buy_vol - sell_vol) / total if total > 0 else 0.0
                sample_ts = _now_iso()
                state_store.set_kv(f"cvd_ws_{state_symbol}", cvd)
                state_store.set_kv(f"cvd_ws_{state_symbol}_updated_at", sample_ts)
                state_store.log_event("ORDER_FLOW_SAMPLE", {
                    "stream": "trades", "symbol": state_symbol,
                    "window_seconds": CVD_FLUSH_INTERVAL_SECONDS,
                    "buy_volume": buy_vol, "sell_volume": sell_vol,
                    "total_volume": total, "cvd": cvd,
                    "sample_timestamp": sample_ts,
                })
                buy_vol = sell_vol = 0.0
                last_flush = now
        except Exception as e:
            if not gap_open:
                state_store.log_event(
                    "WS_GAP_START", {"stream": "trades", "symbol": symbol, "error": str(e)}
                )
                gap_open = True
            print(f"[collector_ws] trade stream lỗi ({e}), reconnect sau {backoff}s", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


def _order_book_sample(order_book, depth=20):
    bids = order_book.get("bids", [])[:depth]
    asks = order_book.get("asks", [])[:depth]
    bid_volume = sum(float(row[1]) for row in bids)
    ask_volume = sum(float(row[1]) for row in asks)
    total = bid_volume + ask_volume
    imbalance = (bid_volume - ask_volume) / total if total > 0 else 0.0
    best_bid = float(bids[0][0]) if bids else None
    best_ask = float(asks[0][0]) if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread_bps = (best_ask - best_bid) / mid * 10_000 if mid else None
    return {
        "depth": depth, "bid_volume": bid_volume, "ask_volume": ask_volume,
        "imbalance": imbalance, "best_bid": best_bid, "best_ask": best_ask,
        "mid_price": mid, "spread_bps": spread_bps,
        "exchange_timestamp": order_book.get("timestamp"),
    }


async def _watch_order_book_loop(exchange, symbol, state_symbol=None):
    """Lưu snapshot L2 top-20 append-only, throttle 30 giây."""
    state_symbol = state_symbol or symbol
    last_flush = 0.0
    backoff = RECONNECT_BACKOFF_SECONDS
    gap_open = False
    while True:
        try:
            order_book = await exchange.watch_order_book(symbol, limit=20)
            if gap_open:
                state_store.log_event("WS_GAP_END", {"stream": "order_book", "symbol": symbol})
                gap_open = False
            backoff = RECONNECT_BACKOFF_SECONDS
            now = asyncio.get_event_loop().time()
            if now - last_flush < ORDER_BOOK_SAMPLE_INTERVAL_SECONDS:
                continue
            state_store.log_event("ORDER_BOOK_SAMPLE", {
                "stream": "order_book", "symbol": state_symbol,
                "sample_timestamp": _now_iso(),
                **_order_book_sample(order_book),
            })
            last_flush = now
        except Exception as e:
            if not gap_open:
                state_store.log_event(
                    "WS_GAP_START", {"stream": "order_book", "symbol": symbol, "error": str(e)}
                )
                gap_open = True
            print(f"[collector_ws] order-book stream lỗi ({e}), reconnect sau {backoff}s", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


async def _watch_liquidations_loop(exchange, symbol):
    backoff = RECONNECT_BACKOFF_SECONDS
    gap_open = False
    while True:
        try:
            liquidations = await exchange.watch_liquidations(symbol)
            if gap_open:
                state_store.log_event("WS_GAP_END", {"stream": "liquidations", "symbol": symbol})
                gap_open = False
            backoff = RECONNECT_BACKOFF_SECONDS

            items = liquidations if isinstance(liquidations, list) else [liquidations]
            for liq in items:
                state_store.log_event(
                    "LIQUIDATION",
                    {
                        "symbol": liq.get("symbol", symbol),
                        "side": liq.get("side"),
                        "price": liq.get("price"),
                        "quantity": liq.get("contracts") or liq.get("amount"),
                    },
                )
        except Exception as e:
            if not gap_open:
                state_store.log_event(
                    "WS_GAP_START", {"stream": "liquidations", "symbol": symbol, "error": str(e)}
                )
                gap_open = True
            print(f"[collector_ws] liquidation stream lỗi ({e}), reconnect sau {backoff}s", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


async def main_async():
    exchange = _make_exchange()
    execution_symbol = market.resolve_symbol(config.SYMBOL)
    # Liquidation chỉ tồn tại trên thị trường phái sinh, kể cả khi execution là Spot.
    liquidation_symbol = market.resolve_symbol(config.SYMBOL, "swap")
    try:
        await asyncio.gather(
            _watch_trades_loop(exchange, execution_symbol, state_symbol=config.SYMBOL),
            _watch_order_book_loop(exchange, execution_symbol, state_symbol=config.SYMBOL),
            _watch_liquidations_loop(exchange, liquidation_symbol),
        )
    finally:
        await exchange.close()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
