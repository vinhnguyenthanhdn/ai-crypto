"""Test cho từng thành phần chấm điểm (Decision Engine) — không dùng framework
test (repo chưa có pytest), tự viết runner nhẹ bằng `assert` + `_run()`.

Test theo TỪNG ITEM tính score, không phải theo layer tổng, để trả lời đúng
câu hỏi "công thức có thật tính ra giá trị khác 0 không, hay bị kẹt/gate sai":
mỗi item được ép vào điều kiện lý thuyết cho ra điểm MAX và điểm 0 (hoặc dải
giữa), xác nhận công thức phản hồi đúng theo input tổng hợp — không phải suy
diễn từ 1 lần quan sát dashboard.

Usage:
    python scripts/test_scoring.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.engine import decision, regime as regime_engine, orderflow, sentiment_score, crossmarket_score  # noqa: E402
from src.indicators import technical  # noqa: E402

_FAILURES = []
_N = 0


def _run(name, fn):
    global _N
    _N += 1
    try:
        fn()
        print(f"  PASS  {name}")
    except AssertionError as e:
        _FAILURES.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001 — muốn bắt cả lỗi runtime để báo rõ item nào crash
        _FAILURES.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ERROR {name}: {type(e).__name__}: {e}")


def _approx(a, b, tol=0.05):
    return abs(a - b) <= tol


# --------------------------------------------------------------- technical

_ROW_DEFAULTS = {
    "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0,
    "ema20": 100.0, "ema50": 100.0, "ema200": 100.0,
    "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "adx": 20.0, "atr": 1.0,
    "vwap": 100.0, "vol_sma20": 100.0,
    "supertrend_dir": 1, "supertrend_band": 99.0,
}


def _build_df(*rows):
    """Mỗi row là dict override so với `_ROW_DEFAULTS` — trả DataFrame nhiều
    dòng để `_detect_bullish_pattern`/`idx=-1` đọc được dòng trước đó."""
    data = [{**_ROW_DEFAULTS, **r} for r in rows]
    return pd.DataFrame(data)


def test_technical_ema_trend_max():
    # ema50>ema200 (gate mở) + ema20 lệch ema50 đúng 1 ATR -> full điểm
    df = _build_df({}, {"ema50": 100.0, "ema200": 90.0, "ema20": 101.0, "atr": 1.0})
    tech = technical.score_from_indicators(df, idx=-1)
    assert _approx(tech["breakdown"]["ema_trend"], 15.0), tech["breakdown"]["ema_trend"]


def test_technical_ema_trend_gated_zero_when_downtrend_structure():
    # ema50<=ema200 (cấu trúc dài hạn chưa phải uptrend) -> gate về 0 dù ema20 rất cao
    df = _build_df({}, {"ema50": 90.0, "ema200": 100.0, "ema20": 200.0, "atr": 1.0})
    tech = technical.score_from_indicators(df, idx=-1)
    assert tech["breakdown"]["ema_trend"] == 0.0, tech["breakdown"]["ema_trend"]


def test_technical_macd_cross_max_and_zero():
    df_max = _build_df({}, {"macd": 1.0, "macd_signal": 0.0, "atr": 1.0})  # histogram 1.0 >= 0.5*atr
    tech_max = technical.score_from_indicators(df_max, idx=-1)
    assert _approx(tech_max["breakdown"]["macd_cross"], 15.0), tech_max["breakdown"]["macd_cross"]

    df_zero = _build_df({}, {"macd": 0.0, "macd_signal": 1.0, "atr": 1.0})  # bearish histogram
    tech_zero = technical.score_from_indicators(df_zero, idx=-1)
    assert tech_zero["breakdown"]["macd_cross"] == 0.0, tech_zero["breakdown"]["macd_cross"]


def test_technical_rsi_oversold_full_range():
    df_low = _build_df({"rsi": 30.0})
    df_mid = _build_df({"rsi": 50.0})
    df_high = _build_df({"rsi": 70.0})
    lo = technical.score_from_indicators(df_low, idx=-1)["breakdown"]["rsi_oversold"]
    mid = technical.score_from_indicators(df_mid, idx=-1)["breakdown"]["rsi_oversold"]
    hi = technical.score_from_indicators(df_high, idx=-1)["breakdown"]["rsi_oversold"]
    assert lo == 0.0, lo
    assert _approx(mid, 5.0), mid  # (50-30)/40 * 10 = 5
    assert _approx(hi, 10.0), hi


def test_technical_adx_strong_full_range():
    lo = technical.score_from_indicators(_build_df({"adx": 15.0}), idx=-1)["breakdown"]["adx_strong"]
    hi = technical.score_from_indicators(_build_df({"adx": 40.0}), idx=-1)["breakdown"]["adx_strong"]
    assert lo == 0.0, lo
    assert _approx(hi, 10.0), hi


def test_technical_volume_spike_full_range():
    lo = technical.score_from_indicators(_build_df({"volume": 100.0, "vol_sma20": 100.0}), idx=-1)["breakdown"]["volume_spike"]
    hi = technical.score_from_indicators(_build_df({"volume": 200.0, "vol_sma20": 100.0}), idx=-1)["breakdown"]["volume_spike"]
    assert lo == 0.0, lo
    assert _approx(hi, 10.0), hi


def test_technical_supertrend_max_and_gated_zero():
    df_max = _build_df({"supertrend_dir": 1, "supertrend_band": 99.0, "close": 100.0, "atr": 1.0})
    df_gated = _build_df({"supertrend_dir": -1, "supertrend_band": 50.0, "close": 200.0, "atr": 1.0})
    st_max = technical.score_from_indicators(df_max, idx=-1)["breakdown"]["supertrend"]
    st_gated = technical.score_from_indicators(df_gated, idx=-1)["breakdown"]["supertrend"]
    assert _approx(st_max, 20.0), st_max
    assert st_gated == 0.0, st_gated  # dir=-1 -> luôn 0 dù giá cách band rất xa


def test_technical_vwap_full_range():
    lo = technical.score_from_indicators(_build_df({"close": 99.0, "vwap": 100.0, "atr": 1.0}), idx=-1)["breakdown"]["vwap"]
    hi = technical.score_from_indicators(_build_df({"close": 101.0, "vwap": 100.0, "atr": 1.0}), idx=-1)["breakdown"]["vwap"]
    assert lo == 0.0, lo
    assert _approx(hi, 10.0), hi


def test_technical_pattern_bullish_engulfing():
    df_pattern = _build_df(
        {"open": 100.0, "close": 95.0},   # nến đỏ
        {"open": 94.0, "close": 101.0},   # nến xanh nuốt trọn nến trước
    )
    df_no_pattern = _build_df({"open": 100.0, "close": 95.0}, {"open": 96.0, "close": 97.0})
    with_pattern = technical.score_from_indicators(df_pattern, idx=-1)["breakdown"]["pattern"]
    without_pattern = technical.score_from_indicators(df_no_pattern, idx=-1)["breakdown"]["pattern"]
    assert with_pattern == 10, with_pattern
    assert without_pattern == 0, without_pattern


def test_technical_all_components_can_be_nonzero_simultaneously():
    """Chống hồi quy cho phát hiện dashboard 2026-08-06: 7/8 item = 0 cùng lúc
    là do ĐIỀU KIỆN THỊ TRƯỜNG thật (SIDEWAY/downtrend), không phải do công
    thức bị kẹt — dựng 1 kịch bản bullish rõ ràng để xác nhận TẤT CẢ item đều
    có thể lên full điểm cùng lúc khi input ủng hộ."""
    row = {
        "open": 94.0, "close": 105.0, "high": 106.0, "low": 93.0, "volume": 300.0,
        "ema20": 105.0, "ema50": 100.0, "ema200": 90.0,
        "rsi": 70.0, "macd": 2.0, "macd_signal": 0.0, "adx": 40.0, "atr": 1.0,
        "vwap": 100.0, "vol_sma20": 100.0, "supertrend_dir": 1, "supertrend_band": 99.0,
    }
    # nến trước phải ĐỎ (bearish) để nến sau (xanh, nuốt trọn) hợp lệ pattern engulfing
    prev_row = {**_ROW_DEFAULTS, "open": 100.0, "close": 95.0}
    df = _build_df(prev_row, row)
    tech = technical.score_from_indicators(df, idx=-1)
    for item, value in tech["breakdown"].items():
        assert value > 0, f"{item} vẫn = 0 dù input được dựng để bullish tối đa: {tech['breakdown']}"
    assert tech["total"] == 100.0, tech["total"]


def test_technical_short_mirror_max_and_zero():
    df_max = _build_df(
        # nến trước phải XANH (bullish) để nến sau (đỏ, nuốt trọn) hợp lệ bearish engulfing
        {"open": 95.0, "close": 102.0},
        {
            "open": 103.0, "close": 90.0, "high": 104.0, "low": 89.0, "volume": 300.0,
            "ema20": 90.0, "ema50": 100.0, "ema200": 110.0,
            "rsi": 30.0, "macd": 0.0, "macd_signal": 2.0, "adx": 40.0, "atr": 1.0,
            "vwap": 100.0, "vol_sma20": 100.0, "supertrend_dir": -1, "supertrend_band": 91.0,
        },
    )
    tech = technical.score_short_from_indicators(df_max, idx=-1)
    for item, value in tech["breakdown"].items():
        assert value > 0, f"short.{item} = 0: {tech['breakdown']}"
    assert tech["total"] == 100.0, tech["total"]

    df_zero = _build_df({}, {"ema50": 110.0, "ema200": 100.0, "supertrend_dir": 1})
    tech_zero = technical.score_short_from_indicators(df_zero, idx=-1)
    assert tech_zero["breakdown"]["ema_trend"] == 0.0
    assert tech_zero["breakdown"]["supertrend"] == 0.0


# --------------------------------------------------------------- derivatives
# Cần isolate state_store (funding/OI trend dùng kv_store nhớ lần chạy trước)
# -> trỏ config.DB_PATH vào 1 thư mục tạm riêng, tự dọn ở cuối `main()`.

_TMP_DIR = tempfile.mkdtemp(prefix="ai_crypto_test_scoring_")
_tmp_db_counter = 0


def _fresh_db():
    global _tmp_db_counter
    _tmp_db_counter += 1
    config.DB_PATH = Path(_TMP_DIR) / f"test_{_tmp_db_counter}.db"
    return config.DB_PATH


def test_derivatives_funding_none_and_static_bands():
    from src.engine import derivatives
    _fresh_db()
    assert derivatives._funding_score(None) == 50.0
    _fresh_db()
    assert derivatives._funding_score(-0.0005) == 90.0  # pct=-0.05
    _fresh_db()
    assert derivatives._funding_score(0.0) == 65.0
    _fresh_db()
    assert derivatives._funding_score(0.0001) == 50.0  # pct=0.01
    _fresh_db()
    assert derivatives._funding_score(0.0003) == 30.0  # pct=0.03
    _fresh_db()
    assert derivatives._funding_score(0.001) == 10.0  # pct=0.1


def test_derivatives_funding_flip_detection():
    from src.engine import derivatives
    _fresh_db()
    derivatives._funding_score(0.0001)  # pct=+0.01, lưu prev
    flipped_down = derivatives._funding_score(-0.0001)  # +/- flip -> 95
    assert flipped_down == 95.0, flipped_down

    _fresh_db()
    derivatives._funding_score(-0.0001)  # pct=-0.01
    flipped_up = derivatives._funding_score(0.0001)  # -/+ flip -> 15
    assert flipped_up == 15.0, flipped_up


def test_derivatives_oi_trend_cold_start_and_each_branch():
    from src.engine import derivatives
    _fresh_db()
    assert derivatives._oi_trend_score(None, 100.0) == 50.0  # thiếu data
    _fresh_db()
    assert derivatives._oi_trend_score(1000.0, 100.0) == 50.0  # cold start, chưa có prev

    _fresh_db()
    derivatives._oi_trend_score(1000.0, 100.0)
    assert derivatives._oi_trend_score(1100.0, 105.0) == 85.0  # giá tăng + OI tăng

    _fresh_db()
    derivatives._oi_trend_score(1000.0, 100.0)
    assert derivatives._oi_trend_score(900.0, 105.0) == 45.0  # giá tăng + OI giảm

    _fresh_db()
    derivatives._oi_trend_score(1000.0, 100.0)
    assert derivatives._oi_trend_score(1100.0, 95.0) == 20.0  # giá giảm + OI tăng

    _fresh_db()
    derivatives._oi_trend_score(1000.0, 100.0)
    assert derivatives._oi_trend_score(900.0, 95.0) == 50.0  # giá giảm + OI giảm


# --------------------------------------------------------------- order_flow

def test_orderflow_imbalance_extremes():
    all_bids = {"bids": [[100, 10]], "asks": []}
    all_asks = {"bids": [], "asks": [[100, 10]]}
    balanced = {"bids": [[100, 10]], "asks": [[99, 10]]}
    r_bid = orderflow.compute_order_flow_score(all_bids, [])
    r_ask = orderflow.compute_order_flow_score(all_asks, [])
    r_bal = orderflow.compute_order_flow_score(balanced, [])
    assert r_bid["breakdown"]["bid_ask_imbalance"] == 100.0, r_bid
    assert r_ask["breakdown"]["bid_ask_imbalance"] == 0.0, r_ask
    assert r_bal["breakdown"]["bid_ask_imbalance"] == 50.0, r_bal


def test_orderflow_cvd_ws_vs_rest_source():
    trades = [{"side": "buy", "amount": 5}, {"side": "sell", "amount": 1}]
    r_rest = orderflow.compute_order_flow_score({"bids": [], "asks": []}, trades)
    assert r_rest["raw"]["cvd_source"] == "rest_snapshot", r_rest
    assert r_rest["breakdown"]["cvd"] > 50.0, r_rest  # buy > sell -> dương

    r_ws = orderflow.compute_order_flow_score({"bids": [], "asks": []}, trades, ws_cvd=-0.8)
    assert r_ws["raw"]["cvd_source"] == "websocket", r_ws
    assert r_ws["breakdown"]["cvd"] < 50.0, r_ws  # ws_cvd âm -> ưu tiên dùng WS, ghi đè REST


# --------------------------------------------------------------- sentiment

def test_sentiment_all_bands_and_none():
    assert sentiment_score.compute_sentiment_score(None)["total"] == 50.0
    assert sentiment_score.compute_sentiment_score({"value": 10, "classification": "x"})["total"] == 70.0
    assert sentiment_score.compute_sentiment_score({"value": 40, "classification": "x"})["total"] == 55.0
    assert sentiment_score.compute_sentiment_score({"value": 50, "classification": "x"})["total"] == 50.0
    assert sentiment_score.compute_sentiment_score({"value": 60, "classification": "x"})["total"] == 45.0
    assert sentiment_score.compute_sentiment_score({"value": 90, "classification": "x"})["total"] == 30.0


# --------------------------------------------------------------- cross_market

def test_crossmarket_neutral_and_directional_and_clip():
    neutral = crossmarket_score.compute_cross_market_score({})
    assert neutral["total"] == 50.0, neutral

    positive = crossmarket_score.compute_cross_market_score({"nasdaq": 1.0, "dxy": 0.0, "vix": 0.0})
    assert positive["total"] > 50.0, positive  # NASDAQ_COEF dương

    clipped = crossmarket_score.compute_cross_market_score({"nasdaq": 100.0, "dxy": 100.0, "vix": -100.0})
    assert clipped["total"] == 100.0, clipped  # clip trên


# --------------------------------------------------------------- regime

def test_regime_all_labels():
    def _df(adx, atr_pct):
        atr = atr_pct  # close=100 -> atr_pct = atr/close*100 = atr nếu close=100
        return pd.DataFrame([{"adx": adx, "atr": atr, "close": 100.0}])

    assert regime_engine.classify_regime(pd.DataFrame([{"adx": None, "atr": None, "close": 100.0}]))["label"] == "UNKNOWN"
    assert regime_engine.classify_regime(_df(30, 1))["label"] == "STRONG_TREND"
    assert regime_engine.classify_regime(_df(30, 5))["label"] == "HIGH_VOLATILITY_TREND"
    assert regime_engine.classify_regime(_df(20, 1))["label"] == "WEAK_TREND"
    assert regime_engine.classify_regime(_df(10, 5))["label"] == "HIGH_VOLATILITY"
    assert regime_engine.classify_regime(_df(10, 1))["label"] == "SIDEWAY"


# --------------------------------------------------------------- decision (tổng hợp)

def test_compute_total_score_weighted_sum_and_default():
    layer_scores = {"technical": 100, "order_flow": 0, "derivatives": 50, "cross_market": 50, "sentiment": 50, "regime": 50}
    total = decision.compute_total_score(layer_scores)
    expected = 100 * 0.35 + 0 * 0.28 + 50 * 0.21 + 50 * 0.07 + 50 * 0.06 + 50 * 0.03
    assert _approx(total, expected, tol=0.01), (total, expected)

    missing_layer = decision.compute_total_score({})  # tất cả layer thiếu -> default 50 mỗi layer
    assert missing_layer == 50.0, missing_layer


def main():
    print("=== Test từng item tính score ===\n")
    print("-- Technical (long) --")
    _run("ema_trend max", test_technical_ema_trend_max)
    _run("ema_trend gate=0 khi cấu trúc chưa uptrend", test_technical_ema_trend_gated_zero_when_downtrend_structure)
    _run("macd_cross max/zero", test_technical_macd_cross_max_and_zero)
    _run("rsi_oversold full range", test_technical_rsi_oversold_full_range)
    _run("adx_strong full range", test_technical_adx_strong_full_range)
    _run("volume_spike full range", test_technical_volume_spike_full_range)
    _run("supertrend max/gate=0", test_technical_supertrend_max_and_gated_zero)
    _run("vwap full range", test_technical_vwap_full_range)
    _run("pattern bullish engulfing on/off", test_technical_pattern_bullish_engulfing)
    _run("TẤT CẢ item Technical đều lên full điểm khi input bullish", test_technical_all_components_can_be_nonzero_simultaneously)
    _run("Technical short mirror max/zero", test_technical_short_mirror_max_and_zero)

    print("\n-- Derivatives --")
    _run("funding: None + các mức tĩnh", test_derivatives_funding_none_and_static_bands)
    _run("funding: phát hiện flip dấu", test_derivatives_funding_flip_detection)
    _run("OI trend: cold-start + 4 nhánh", test_derivatives_oi_trend_cold_start_and_each_branch)

    print("\n-- Order Flow --")
    _run("bid/ask imbalance cực trị", test_orderflow_imbalance_extremes)
    _run("CVD: nguồn WS vs REST", test_orderflow_cvd_ws_vs_rest_source)

    print("\n-- Sentiment --")
    _run("Fear&Greed đủ dải + None", test_sentiment_all_bands_and_none)

    print("\n-- Cross-market --")
    _run("neutral/directional/clip", test_crossmarket_neutral_and_directional_and_clip)

    print("\n-- Regime --")
    _run("đủ 5 nhãn + UNKNOWN", test_regime_all_labels)

    print("\n-- Decision (tổng hợp) --")
    _run("compute_total_score: weighted sum + default layer thiếu", test_compute_total_score_weighted_sum_and_default)

    shutil.rmtree(_TMP_DIR, ignore_errors=True)

    print(f"\n=== {_N - len(_FAILURES)}/{_N} PASS ===")
    if _FAILURES:
        print("\nFAILED:")
        for name, msg in _FAILURES:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
