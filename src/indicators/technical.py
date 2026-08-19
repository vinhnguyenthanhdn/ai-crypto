"""Indicator + Score Engine cho lớp Technical.

Điểm tối đa 100. Mỗi thành phần chấm LIÊN TỤC trong khoảng [0, weight] theo
khoảng cách/tỷ lệ chuẩn hoá ATR hoặc vị trí trong vùng chỉ báo — không phải
on/off — để điểm phản ứng theo từng tick giá nhỏ khi tick-recompute mỗi poll
("giá đổi mà score không đổi là bất hợp lý"). Ngoại lệ duy nhất: `pattern`
(nến engulfing) vẫn on/off vì chỉ có ý nghĩa theo nến đã đóng, không có dạng
liên tục hợp lý. Các hằng số chuẩn hoá (bội số ATR, vùng RSI/ADX...) là ước
lượng ban đầu, CHƯA calibrate lại bằng backtest — chỉ backtest lại khi user
yêu cầu.
Đa khung thời gian (1m/5m/15m) dùng làm bộ lọc xác nhận: nếu các khung không
đồng thuận hướng trend, phần điểm Trend/MACD bị giảm theo tỷ lệ đồng thuận.
"""
import pandas as pd
import ta

from .. import config

SCORE_WEIGHTS = {
    "ema_trend": 15,
    "macd_cross": 15,
    "rsi_oversold": 10,
    "adx_strong": 10,
    "volume_spike": 10,
    "supertrend": 20,
    "vwap": 10,
    "pattern": 10,
}
assert sum(SCORE_WEIGHTS.values()) == 100


def to_dataframe(ohlcv):
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df


def _supertrend(df, period=10, multiplier=3.0):
    """Trả về (direction, band) — `band` là đường trailing đang active (lower
    khi direction=1, upper khi direction=-1), cần để chấm điểm liên tục theo
    khoảng cách giá tới đường Supertrend (xem `score_from_indicators`)."""
    atr = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    direction = pd.Series(index=df.index, dtype="int64")
    direction.iloc[0] = 1
    final_upper = upper.copy()
    final_lower = lower.copy()

    for i in range(1, len(df)):
        if df["close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            if direction.iloc[i] == 1 and lower.iloc[i] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = final_lower.iloc[i - 1]
            if direction.iloc[i] == -1 and upper.iloc[i] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = final_upper.iloc[i - 1]
    band = final_lower.where(direction == 1, final_upper)
    return direction, band


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["ema200"] = ta.trend.ema_indicator(df["close"], window=200)
    df["rsi"] = ta.momentum.rsi(df["close"], window=14)
    macd = ta.trend.MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["adx"] = ta.trend.adx(df["high"], df["low"], df["close"], window=14)
    df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    df["vwap"] = ta.volume.volume_weighted_average_price(
        df["high"], df["low"], df["close"], df["volume"], window=14
    )
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["supertrend_dir"], df["supertrend_band"] = _supertrend(df)
    return df


def _trend_direction(df: pd.DataFrame, idx: int = -1) -> int:
    last = df.iloc[idx]
    if pd.isna(last["ema20"]) or pd.isna(last["ema50"]):
        return 0
    return 1 if last["ema20"] > last["ema50"] else -1


def _detect_bullish_pattern(df: pd.DataFrame, idx: int = -1) -> bool:
    """Bullish engulfing đơn giản trên 2 nến gần nhất."""
    if len(df) < 2 or idx == 0:
        return False
    prev, last = df.iloc[idx - 1], df.iloc[idx]
    prev_bearish = prev["close"] < prev["open"]
    last_bullish = last["close"] > last["open"]
    engulfs = last["close"] >= prev["open"] and last["open"] <= prev["close"]
    return bool(prev_bearish and last_bullish and engulfs)


def _normalize_idx(n: int, idx: int) -> int:
    return idx if idx >= 0 else n + idx


def find_recent_swing_low(df: pd.DataFrame, idx: int = -1, window: int = 3, lookback: int = 50) -> float | None:
    """Swing low gần nhất TRƯỚC `idx` (SL structural provisional) —
    fractal đơn giản: 1 bar được coi là swing low nếu `low` thấp hơn `window`
    bar liền trước VÀ `window` bar liền sau nó. Chỉ xét bar đã có đủ `window`
    bar SAU nó trong `df` để xác nhận — không nhìn tương lai thật, các bar quá
    gần `idx` (chưa đủ bar sau để confirm) tự động bị loại khỏi kết quả.

    Trả về giá `low` của swing point gần `idx` nhất trong `lookback` bar trước
    đó, `None` nếu không tìm được (dữ liệu quá ngắn hoặc không có swing rõ).
    """
    n = len(df)
    end = _normalize_idx(n, idx)
    lows = df["low"].to_numpy()
    start = max(window, end - lookback)
    for i in range(end - window, start - 1, -1):
        if i - window < 0 or i + window >= end:
            continue
        left, right = lows[i - window:i], lows[i + 1:i + 1 + window]
        if lows[i] < left.min() and lows[i] < right.min():
            return float(lows[i])
    return None


def find_recent_swing_high(df: pd.DataFrame, idx: int = -1, window: int = 3, lookback: int = 50) -> float | None:
    """Mirror của `find_recent_swing_low` — dùng làm TP structural (resistance/
    previous high gần nhất) thay vì đo thuần theo R:R."""
    n = len(df)
    end = _normalize_idx(n, idx)
    highs = df["high"].to_numpy()
    start = max(window, end - lookback)
    for i in range(end - window, start - 1, -1):
        if i - window < 0 or i + window >= end:
            continue
        left, right = highs[i - window:i], highs[i + 1:i + 1 + window]
        if highs[i] > left.max() and highs[i] > right.max():
            return float(highs[i])
    return None


def pullback_ok(df: pd.DataFrame, idx: int = -1, side: str = "long", current_price: float | None = None) -> bool:
    """Pullback filter: chỉ cho vào lệnh khi giá đã hồi về gần EMA20 trong khi
    trend (EMA20 vs EMA50) còn giữ đúng hướng — chặn breakout chasing (giá đã
    chạy xa khỏi EMA20 lúc tín hiệu vừa xác nhận, dễ đảo chiều ngay sau đó).

    `current_price`: EMA20/EMA50/ATR là indicator theo nến đóng, không cần tính lại
    giữa các lần poll (nến chưa đóng thì giá trị không đổi) — nhưng vị trí giá SO
    VỚI vùng pullback thì có ý nghĩa theo tick thật, không phải theo giá đóng cửa
    nến gần nhất. Backtest (không có tick thật) mặc định `None` → dùng giá đóng cửa
    của nến `idx` như cũ; `run.py` truyền tick giá thật từ cửa sổ theo dõi.
    """
    last = df.iloc[idx]
    ema20, ema50, atr = last.get("ema20"), last.get("ema50"), last.get("atr")
    close = last.get("close") if current_price is None else current_price
    if pd.isna(ema20) or pd.isna(ema50) or pd.isna(atr) or atr <= 0:
        return False
    buffer = config.PULLBACK_ATR_BUFFER * atr
    if side == "long":
        return bool(ema20 > ema50 and ema50 - buffer <= close <= ema20 + buffer)
    return bool(ema20 < ema50 and ema20 - buffer <= close <= ema50 + buffer)


def score_from_indicators(primary_enriched: pd.DataFrame, idx: int = -1, agreement_ratio: float = 1.0) -> dict:
    """Tính breakdown/raw từ dataframe ĐÃ `add_indicators()` — không tính lại
    indicator, chỉ đọc theo `idx` (mặc định -1 = bar cuối). Tách riêng khỏi
    `compute_technical_score` để Backtest Engine có thể gọi lại nhiều lần trên
    cùng 1 dataframe đã enrich sẵn (O(1)/bar) thay vì recompute indicator trên
    slice tăng dần mỗi bar (O(n) x O(n) = quá chậm cho lịch sử dài).

    Mỗi thành phần trả về 1 tỷ lệ [0,1] nhân với weight — LIÊN TỤC theo khoảng
    cách chuẩn hoá ATR hoặc vị trí trong vùng chỉ báo, không phải on/off (xem
    docstring đầu file). `pattern` là ngoại lệ, giữ on/off.
    """
    last = primary_enriched.iloc[idx]
    breakdown = {}
    atr = last.get("atr")
    has_atr = not pd.isna(atr) and atr > 0

    # ema_trend: gate cấu trúc dài hạn (ema50>ema200) vẫn on/off — đổi hướng
    # trend dài hạn không nên liên tục — nhưng ĐỘ MẠNH trend (ema20 lệch khỏi
    # ema50 bao nhiêu ATR) chấm liên tục, và ema20 phản ứng theo tick giá thật.
    ema20, ema50, ema200 = last.get("ema20"), last.get("ema50"), last.get("ema200")
    ema_ratio = 0.0
    if has_atr and not pd.isna(ema20) and not pd.isna(ema50) and not pd.isna(ema200) and ema50 > ema200:
        ema_ratio = _clamp01((ema20 - ema50) / atr)
    breakdown["ema_trend"] = SCORE_WEIGHTS["ema_trend"] * ema_ratio * agreement_ratio

    # macd_cross: đổi từ "vừa cắt lên" (event 1 lần) sang cường độ momentum liên
    # tục — histogram (macd-signal) chuẩn hoá ATR, full điểm khi histogram >= 0.5 ATR.
    macd, macd_signal = last.get("macd"), last.get("macd_signal")
    macd_ratio = 0.0
    if has_atr and not pd.isna(macd) and not pd.isna(macd_signal):
        macd_ratio = _clamp01((macd - macd_signal) / (0.5 * atr))
    breakdown["macd_cross"] = SCORE_WEIGHTS["macd_cross"] * macd_ratio * agreement_ratio

    # rsi_oversold: đổi từ "vừa cắt lên 30" (event) sang vị trí liên tục trong
    # vùng phục hồi 30-70 (0 tại <=30, full tại >=70).
    rsi = last.get("rsi")
    rsi_ratio = 0.0 if pd.isna(rsi) else _clamp01((rsi - 30) / 40)
    breakdown["rsi_oversold"] = SCORE_WEIGHTS["rsi_oversold"] * rsi_ratio

    # adx_strong: liên tục theo ADX quanh ngưỡng cũ 25 (0 tại <=15, full tại >=40).
    adx = last.get("adx")
    adx_ratio = 0.0 if pd.isna(adx) else _clamp01((adx - 15) / 25)
    breakdown["adx_strong"] = SCORE_WEIGHTS["adx_strong"] * adx_ratio

    # volume_spike: liên tục theo tỷ lệ volume/trung bình 20 nến (0 tại <=1x, full tại >=2x).
    vol_sma20, volume = last.get("vol_sma20"), last.get("volume")
    vol_ratio = 0.0
    if not pd.isna(vol_sma20) and vol_sma20 > 0 and not pd.isna(volume):
        vol_ratio = _clamp01((volume / vol_sma20) - 1.0)
    breakdown["volume_spike"] = SCORE_WEIGHTS["volume_spike"] * vol_ratio

    # supertrend: liên tục theo khoảng cách giá tới đường Supertrend chuẩn hoá
    # ATR — `close` đổi theo tick nên đây là thành phần nhạy giá nhất.
    supertrend_dir, supertrend_band = last.get("supertrend_dir"), last.get("supertrend_band")
    st_ratio = 0.0
    if has_atr and supertrend_dir == 1 and not pd.isna(supertrend_band):
        st_ratio = _clamp01((last["close"] - supertrend_band) / atr)
    breakdown["supertrend"] = SCORE_WEIGHTS["supertrend"] * st_ratio

    # vwap: liên tục theo khoảng cách giá so với VWAP chuẩn hoá ATR — cũng nhạy
    # giá tick trực tiếp qua `close`.
    vwap = last.get("vwap")
    vwap_ratio = 0.0
    if has_atr and not pd.isna(vwap):
        vwap_ratio = _clamp01((last["close"] - vwap) / atr)
    breakdown["vwap"] = SCORE_WEIGHTS["vwap"] * vwap_ratio

    breakdown["pattern"] = SCORE_WEIGHTS["pattern"] if _detect_bullish_pattern(primary_enriched, idx) else 0

    total = round(sum(breakdown.values()), 2)

    # Feature Store: raw indicator value, tách khỏi điểm số breakdown ở trên —
    # dùng để train Entry Model và Feature Lineage sau này.
    raw = {
        "ema20": _safe_float(last.get("ema20")),
        "ema50": _safe_float(last.get("ema50")),
        "ema200": _safe_float(last.get("ema200")),
        "rsi": _safe_float(last.get("rsi")),
        "macd": _safe_float(last.get("macd")),
        "macd_signal": _safe_float(last.get("macd_signal")),
        "adx": _safe_float(last.get("adx")),
        "atr": _safe_float(last.get("atr")),
        "vwap": _safe_float(last.get("vwap")),
        "vol_sma20": _safe_float(last.get("vol_sma20")),
        "supertrend_dir": _safe_float(last.get("supertrend_dir")),
        "supertrend_band": _safe_float(last.get("supertrend_band")),
        "mtf_agreement_ratio": round(agreement_ratio, 3),
    }

    return {
        "total": total,
        "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "raw": raw,
        "last_price": float(last["close"]),
    }


def compute_technical_score(df_by_tf: dict, primary_tf: str) -> dict:
    """df_by_tf: {timeframe: DataFrame OHLCV thô}. Trả về breakdown điểm 0-100."""
    enriched = {tf: add_indicators(df) for tf, df in df_by_tf.items()}
    primary = enriched[primary_tf]

    # Đồng thuận đa khung cho Trend/MACD
    directions = [_trend_direction(df) for df in enriched.values()]
    primary_dir = _trend_direction(primary)
    agree = sum(1 for d in directions if d == primary_dir and d != 0)
    agreement_ratio = agree / len(directions) if directions else 0

    return score_from_indicators(primary, idx=-1, agreement_ratio=agreement_ratio)


def _detect_bearish_pattern(df: pd.DataFrame, idx: int = -1) -> bool:
    """Bearish engulfing — mirror của `_detect_bullish_pattern`, dùng để thử
    nghiệm chiến lược Short riêng (phát hiện "buy đỉnh cục bộ" từ AI Review
    Backtest). Chưa dùng trong Rule Engine live."""
    if len(df) < 2 or idx == 0:
        return False
    prev, last = df.iloc[idx - 1], df.iloc[idx]
    prev_bullish = prev["close"] > prev["open"]
    last_bearish = last["close"] < last["open"]
    engulfs = last["close"] <= prev["open"] and last["open"] >= prev["close"]
    return bool(prev_bullish and last_bearish and engulfs)


def score_short_from_indicators(primary_enriched: pd.DataFrame, idx: int = -1, agreement_ratio: float = 1.0) -> dict:
    """Mirror của `score_from_indicators` cho tín hiệu Short — cùng SCORE_WEIGHTS
    và cùng cách chấm liên tục (xem docstring `score_from_indicators`), đảo
    hướng từng điều kiện (bearish stack, MACD momentum âm, RSI vùng topping,
    Supertrend xuống, dưới VWAP, bearish engulfing)."""
    last = primary_enriched.iloc[idx]
    breakdown = {}
    atr = last.get("atr")
    has_atr = not pd.isna(atr) and atr > 0

    ema20, ema50, ema200 = last.get("ema20"), last.get("ema50"), last.get("ema200")
    ema_ratio = 0.0
    if has_atr and not pd.isna(ema20) and not pd.isna(ema50) and not pd.isna(ema200) and ema50 < ema200:
        ema_ratio = _clamp01((ema50 - ema20) / atr)
    breakdown["ema_trend"] = SCORE_WEIGHTS["ema_trend"] * ema_ratio * agreement_ratio

    macd, macd_signal = last.get("macd"), last.get("macd_signal")
    macd_ratio = 0.0
    if has_atr and not pd.isna(macd) and not pd.isna(macd_signal):
        macd_ratio = _clamp01((macd_signal - macd) / (0.5 * atr))
    breakdown["macd_cross"] = SCORE_WEIGHTS["macd_cross"] * macd_ratio * agreement_ratio

    rsi = last.get("rsi")
    rsi_ratio = 0.0 if pd.isna(rsi) else _clamp01((70 - rsi) / 40)
    breakdown["rsi_oversold"] = SCORE_WEIGHTS["rsi_oversold"] * rsi_ratio

    adx = last.get("adx")
    adx_ratio = 0.0 if pd.isna(adx) else _clamp01((adx - 15) / 25)
    breakdown["adx_strong"] = SCORE_WEIGHTS["adx_strong"] * adx_ratio

    vol_sma20, volume = last.get("vol_sma20"), last.get("volume")
    vol_ratio = 0.0
    if not pd.isna(vol_sma20) and vol_sma20 > 0 and not pd.isna(volume):
        vol_ratio = _clamp01((volume / vol_sma20) - 1.0)
    breakdown["volume_spike"] = SCORE_WEIGHTS["volume_spike"] * vol_ratio

    supertrend_dir, supertrend_band = last.get("supertrend_dir"), last.get("supertrend_band")
    st_ratio = 0.0
    if has_atr and supertrend_dir == -1 and not pd.isna(supertrend_band):
        st_ratio = _clamp01((supertrend_band - last["close"]) / atr)
    breakdown["supertrend"] = SCORE_WEIGHTS["supertrend"] * st_ratio

    vwap = last.get("vwap")
    vwap_ratio = 0.0
    if has_atr and not pd.isna(vwap):
        vwap_ratio = _clamp01((vwap - last["close"]) / atr)
    breakdown["vwap"] = SCORE_WEIGHTS["vwap"] * vwap_ratio

    breakdown["pattern"] = SCORE_WEIGHTS["pattern"] if _detect_bearish_pattern(primary_enriched, idx) else 0

    total = round(sum(breakdown.values()), 2)
    return {
        "total": total,
        "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "last_price": float(last["close"]),
    }


def _safe_float(x):
    return None if x is None or pd.isna(x) else float(x)
