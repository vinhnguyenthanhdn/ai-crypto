"""Lấy dữ liệu từ sàn qua CCXT: OHLCV đa khung, order book, trades, funding, OI.

Retry ngắn theo plan-02.md (rủi ro "Exchange API downtime/rate-limit"): thử lại
tối đa 3 lần, backoff ngắn; nếu vẫn lỗi thì raise để caller quyết định bỏ qua
lần chạy này thay vì tính toán trên dữ liệu thiếu.
"""
import time

import ccxt

from .. import config

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


def _with_retry(fn, *args, **kwargs):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_err


def get_exchange():
    exchange_cls = getattr(ccxt, config.EXCHANGE_ID)
    exchange = exchange_cls(
        {
            "apiKey": config.EXCHANGE_API_KEY or None,
            "secret": config.EXCHANGE_API_SECRET or None,
            "password": config.EXCHANGE_API_PASSPHRASE or None,
            "enableRateLimit": True,
        }
    )
    return exchange


def get_binance_exchange():
    """Sàn thứ 2 chỉ để Collector đối chiếu (xem plan-02.md mục 5b) — không dùng cho Rule Engine."""
    return ccxt.binance(
        {
            "apiKey": config.BINANCE_API_KEY or None,
            "secret": config.BINANCE_API_SECRET or None,
            "enableRateLimit": True,
        }
    )


def fetch_cross_exchange_price(symbol):
    """Giá spot nhanh từ Binance để đối chiếu — best-effort, lỗi thì trả None thay vì
    chặn pipeline chính (Binance ở đây chỉ là nguồn phụ, xem plan-02.md mục 5b)."""
    try:
        exchange = get_binance_exchange()
        ticker = _with_retry(exchange.fetch_ticker, symbol)
        return ticker.get("last")
    except Exception:
        return None


def fetch_ohlcv_multi_tf(exchange, symbol, timeframes=("1m", "5m", "15m"), limit=200):
    """Đa khung thời gian cho lớp Technical (xem plan-02.md phần Score Engine)."""
    result = {}
    for tf in timeframes:
        result[tf] = _with_retry(exchange.fetch_ohlcv, symbol, timeframe=tf, limit=limit)
    return result


def fetch_order_book(exchange, symbol, limit=100):
    return _with_retry(exchange.fetch_order_book, symbol, limit=limit)


def fetch_recent_trades(exchange, symbol, limit=500):
    return _with_retry(exchange.fetch_trades, symbol, limit=limit)


def fetch_funding_rate(exchange, symbol):
    """Funding rate — chỉ áp dụng cho thị trường futures/perpetual."""
    try:
        if exchange.has.get("fetchFundingRate"):
            data = _with_retry(exchange.fetch_funding_rate, symbol)
            return data.get("fundingRate")
    except Exception:
        return None
    return None


def fetch_historical_ohlcv(exchange, symbol, timeframe, days):
    """Tải OHLCV lịch sử nhiều trang (dùng cho Backtest Engine mục 11, Entry Model mục 7)."""
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
    all_rows = []
    while True:
        batch = _with_retry(exchange.fetch_ohlcv, symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= since:
            break
        since = last_ts + tf_ms
        if last_ts >= exchange.milliseconds() - tf_ms:
            break
        time.sleep(exchange.rateLimit / 1000)
    return all_rows


def fetch_open_interest(exchange, symbol):
    try:
        if exchange.has.get("fetchOpenInterest"):
            data = _with_retry(exchange.fetch_open_interest, symbol)
            return data.get("openInterestAmount") or data.get("openInterestValue")
    except Exception:
        return None
    return None
