"""Collector WebSocket 24/7 — Trade Stream + Liquidation (xem plan-02.md mục 5, 8b).

Chạy như 1 process riêng, độc lập với pipeline cron ở `run.py`:
    python -m src.collector_ws

Chỉ 2 luồng này cần WebSocket (continuous stream, xem mục 5):
- Trade Stream: REST poll sẽ bỏ sót trade xảy ra giữa 2 lần gọi → CVD tính sai.
- Liquidation: không có REST endpoint tương đương đủ tốt cho luồng này.

Các dữ liệu còn lại (OHLCV, Order Book, Funding, OI) vẫn REST polling trong
`run.py` — không cần WebSocket. Dùng `ccxt.pro` (đã bundle miễn phí trong ccxt
hiện tại — khác với ghi chú "CCXT Pro trả phí" ở Phase 1, xác nhận lại khi build).
"""
import asyncio
import sys
from datetime import datetime, timezone

import ccxt.pro as ccxtpro

from . import config, state_store

RECONNECT_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 60
CVD_FLUSH_INTERVAL_SECONDS = 30


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
        }
    )


async def _watch_trades_loop(exchange, symbol):
    """Tích luỹ CVD từ trade stream thật, flush định kỳ vào `kv_store`.

    Reconnect có backoff (mục 8b, rủi ro "WebSocket rớt kết nối"); đánh dấu
    WS_GAP_START/WS_GAP_END vào event_log để Feature Engine biết loại trừ
    khoảng mất kết nối thay vì tính CVD nhầm trên dữ liệu thiếu.
    """
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
            # của run.py (mục 5d), không đợi chu kỳ flush CVD 30s.
            last_price = trades[-1].get("price") if trades else None
            if last_price is not None:
                state_store.set_kv(f"last_tick_price_{symbol}", last_price)
                state_store.set_kv(f"last_tick_price_{symbol}_at", _now_iso())

            now = asyncio.get_event_loop().time()
            if now - last_flush >= CVD_FLUSH_INTERVAL_SECONDS:
                total = buy_vol + sell_vol
                cvd = (buy_vol - sell_vol) / total if total > 0 else 0.0
                state_store.set_kv(f"cvd_ws_{symbol}", cvd)
                state_store.set_kv(f"cvd_ws_{symbol}_updated_at", _now_iso())
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
    try:
        await asyncio.gather(
            _watch_trades_loop(exchange, config.SYMBOL),
            _watch_liquidations_loop(exchange, config.SYMBOL),
        )
    finally:
        await exchange.close()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
