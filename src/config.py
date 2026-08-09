import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
_runtime_env_value = os.getenv("RUNTIME_ENV_PATH")
EFFECTIVE_CONFIG_PATH = (
    Path(_runtime_env_value).expanduser() if _runtime_env_value else BASE_DIR / ".env"
)
if not EFFECTIVE_CONFIG_PATH.is_absolute():
    EFFECTIVE_CONFIG_PATH = BASE_DIR / EFFECTIVE_CONFIG_PATH
# Khi service chỉ truyền RUNTIME_ENV_PATH, file được chọn là SSOT và thắng các
# biến shell/launchd cũ còn sót lại. Secret không có trong paper.env vẫn có thể
# nằm ở .env chung: load base trước rồi override bằng runtime file.
load_dotenv(BASE_DIR / ".env")
if EFFECTIVE_CONFIG_PATH != BASE_DIR / ".env":
    load_dotenv(EFFECTIVE_CONFIG_PATH, override=True)

EXCHANGE_ID = os.getenv("EXCHANGE_ID", "okx")
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
EXCHANGE_API_PASSPHRASE = os.getenv("EXCHANGE_API_PASSPHRASE", "")

# Binance — hiện chỉ dùng cho Collector/Feature Store (cross-exchange check),
# chưa tham gia Rule Engine/Decision Engine chính (hệ thống hiện chạy quyết định
# trên 1 sàn tại 1 thời điểm, không phải Binance có vai trò thấp hơn OKX — kế
# hoạch sẽ bổ sung thêm sàn ngang hàng sau).
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "5m")

# "spot" (hiện đang chạy) | "swap" (Perpetual Swap — hướng đã chọn để giảm chi
# phí + mở khả năng Short thật, xem docs/decisions.md). Đổi giá trị này phải
# đổi cả symbol dùng để fetch giá (xem `market.resolve_symbol`) — nguyên tắc
# "đúng sàn/thị trường đang trade", không dùng giá spot cho quyết định swap.
# CHƯA đổi mặc định "spot" cho tới khi #4 (đánh giá Short) xác nhận edge dương.
MARKET_TYPE = os.getenv("MARKET_TYPE", "spot")
# Đòn bẩy khi MARKET_TYPE=swap — không áp dụng ở spot (không có khái niệm này).
LEVERAGE = float(os.getenv("LEVERAGE", "1"))

# Định danh lineage của feature/strategy đang tạo quyết định. Đây là metadata,
# không phải model serving: runtime hiện tại vẫn là Rule Engine thuần.
FEATURE_VERSION = os.getenv("FEATURE_VERSION", "rule_features_v1")
STRATEGY_PACKAGE_ID = os.getenv("STRATEGY_PACKAGE_ID", "rule_engine_v1")
SCORING_PROFILE = os.getenv("SCORING_PROFILE", "champion")
RUNTIME_ENGINE_VERSION = os.getenv("RUNTIME_ENGINE_VERSION", "runtime_v2")
PAPER_ENGINE_VERSION = os.getenv("PAPER_ENGINE_VERSION", "paper_engine_v2")

# Research Champion BTC Spot trend đã qua historical production/Paper parity.
# Runtime mặc định OFF cho tới khi daily scheduler + venue order-resize forward
# Paper được nối và quan sát; run.py fail-closed nếu bật sớm.
BTC_SPOT_TREND_ENABLED = os.getenv("BTC_SPOT_TREND_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
BTC_SPOT_TREND_PACKAGE_ID = os.getenv(
    "BTC_SPOT_TREND_PACKAGE_ID", "btc_spot_vol_scaled_trend_v1",
)

# Candidate 4h staggered pullback cũ đã qua research replay nhưng chưa qua live
# Spot/Swap lifecycle parity. Flag mặc định OFF và hiện chỉ cấp contract cho
# offline parity; không được dùng để ngầm thay SCORING_PROFILE đang chạy.
STAGGERED_PULLBACK_ENABLED = os.getenv("STAGGERED_PULLBACK_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
STAGGERED_PULLBACK_PACKAGE_ID = os.getenv(
    "STAGGERED_PULLBACK_PACKAGE_ID", "btc_staggered_pullback_4h_profit_v2",
)
STAGGERED_PULLBACK_RISK_PER_EXCURSION_PCT = float(os.getenv(
    "STAGGERED_PULLBACK_RISK_PER_EXCURSION_PCT", "1.0",
))

# EXP-SR-SCORE-V4 — score V3 + TP multi-high/Fibonacci V2.
SR_EXPERIMENT_ID = os.getenv("SR_EXPERIMENT_ID", "EXP-SR-SCORE-V4")
SR_SCORE_VERSION = os.getenv("SR_SCORE_VERSION", "sr_score_v3_partial_hyperbolic")
SR_TP_POLICY_VERSION = os.getenv("SR_TP_POLICY_VERSION", "tp_multi_high_fib_v2")
SR_SWING_WINDOW = int(os.getenv("SR_SWING_WINDOW", "3"))
SR_SWING_LOOKBACK = int(os.getenv("SR_SWING_LOOKBACK", "100"))
SR_REQUIRED_SWINGS = int(os.getenv("SR_REQUIRED_SWINGS", "2"))
SR_DECISION_THRESHOLD = float(os.getenv("SR_DECISION_THRESHOLD", "70"))
SR_SAME_ZONE_MAX_SPREAD_ATR = float(os.getenv("SR_SAME_ZONE_MAX_SPREAD_ATR", "0.25"))
SR_APPROACH_WIDTH_ATR = float(os.getenv("SR_APPROACH_WIDTH_ATR", "0.30"))
SR_BUY_THRESHOLD_DISTANCE_ATR = float(os.getenv("SR_BUY_THRESHOLD_DISTANCE_ATR", "0.09"))
SR_SCORE_FLOOR = float(os.getenv("SR_SCORE_FLOOR", "0.01"))
SR_FAKE_BREAK_WICK_ATR = float(os.getenv("SR_FAKE_BREAK_WICK_ATR", "0.15"))
SR_INVALIDATION_CLOSE_ATR = float(os.getenv("SR_INVALIDATION_CLOSE_ATR", "0.20"))
SR_ZONE_QUALITY_1 = float(os.getenv("SR_ZONE_QUALITY_1", "0.50"))
SR_ZONE_QUALITY_2 = float(os.getenv("SR_ZONE_QUALITY_2", "1.00"))
SR_ZONE_QUALITY_3 = float(os.getenv("SR_ZONE_QUALITY_3", "0.90"))
SR_ZONE_QUALITY_4_PLUS = float(os.getenv("SR_ZONE_QUALITY_4_PLUS", "0.70"))
SR_SL_BUFFER_ATR = float(os.getenv("SR_SL_BUFFER_ATR", "0.20"))
SR_FAR_RESISTANCE_ATR = float(os.getenv("SR_FAR_RESISTANCE_ATR", "3.00"))
SR_FIB_LEVELS = tuple(float(v) for v in os.getenv("SR_FIB_LEVELS", "0.382,0.500,0.618,0.786").split(","))
SR_MIN_RISK_REWARD = float(os.getenv("SR_MIN_RISK_REWARD", "1.50"))


def support_resistance_manifest() -> dict:
    """Toàn bộ contract tham số S/R hiện hành để lineage không ghi thiếu."""
    return {
        "experiment_id": SR_EXPERIMENT_ID,
        "score_version": SR_SCORE_VERSION,
        "tp_policy_version": SR_TP_POLICY_VERSION,
        "required_swings": SR_REQUIRED_SWINGS,
        "swing_window": SR_SWING_WINDOW,
        "swing_lookback": SR_SWING_LOOKBACK,
        "same_zone_max_spread_atr": SR_SAME_ZONE_MAX_SPREAD_ATR,
        "approach_width_atr": SR_APPROACH_WIDTH_ATR,
        "buy_threshold_distance_atr": SR_BUY_THRESHOLD_DISTANCE_ATR,
        "score_floor": SR_SCORE_FLOOR,
        "fake_break_wick_atr": SR_FAKE_BREAK_WICK_ATR,
        "invalidation_close_atr": SR_INVALIDATION_CLOSE_ATR,
        "zone_quality": {
            "1": SR_ZONE_QUALITY_1, "2": SR_ZONE_QUALITY_2,
            "3": SR_ZONE_QUALITY_3, "4_plus": SR_ZONE_QUALITY_4_PLUS,
        },
        "sl_buffer_atr": SR_SL_BUFFER_ATR,
        "far_resistance_atr": SR_FAR_RESISTANCE_ATR,
        "fib_levels": list(SR_FIB_LEVELS),
        "minimum_risk_reward": SR_MIN_RISK_REWARD,
        "decision_threshold": SR_DECISION_THRESHOLD,
        "sell_basis": "support_breakdown",
    }

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ACCOUNT_EQUITY_USD = float(os.getenv("ACCOUNT_EQUITY_USD", "500"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.5"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "5"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "15"))
COOLDOWN_MINUTES = float(os.getenv("COOLDOWN_MINUTES", "30"))
# Không có minimum hold: mọi exit hợp lệ được áp dụng ngay. Vị thế không được
# tồn tại vô hạn; quá horizon này phải đóng bằng TIMEOUT_EXIT ở live/backtest.
MAX_HOLD_MINUTES = float(os.getenv("MAX_HOLD_MINUTES", "1440"))

# Risk Engine (src/engine/risk.py) — khoảng cách stop theo ATR + risk:reward.
# Tách ra config để backtest thử nhiều mức mà không sửa code.
ATR_STOP_MULTIPLIER = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))
RISK_REWARD_RATIO = float(os.getenv("RISK_REWARD_RATIO", "1.5"))

# Chi phí giao dịch — nguồn duy nhất cho cả Risk Engine (cost gate bên dưới) và
# Backtest Engine. FEE_PCT/SLIPPAGE_PCT là chi phí MỘT chiều, dạng thập phân
# (0.001 = 0.1%); chi phí khứ hồi = (FEE_PCT + SLIPPAGE_PCT) * 2.
# Mặc định theo MARKET_TYPE: spot taker OKX ~0.1%, swap maker OKX ~0.02% — đây
# giả định khởi đầu cho Swap; phải xác minh qua `TODO-SWAP-PARITY`, luôn override khi có
# số thật mới hơn, không hard-code lại ở nơi khác.
_DEFAULT_FEE_PCT = "0.0002" if MARKET_TYPE == "swap" else "0.001"
FEE_PCT = float(os.getenv("FEE_PCT", _DEFAULT_FEE_PCT))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0005"))  # 0.05%, xấp xỉ độ sâu order book

# Cost gate: từ chối vào lệnh khi khoảng cách Take Profit không đủ lớn so với chi
# phí khứ hồi. Không có ràng buộc này, phần lớn lệnh trên khung nhiễu (ATR nhỏ)
# lỗ ngay cả khi chạm đúng TP.
# k=2.5 nghĩa là TP phải cách entry ít nhất 2.5 lần chi phí khứ hồi.
MIN_TP_COST_RATIO = float(os.getenv("MIN_TP_COST_RATIO", "2.5"))
# Số lệnh tối đa mở đồng thời (cùng symbol, lệch thời điểm entry) — enforce ở
# `run.py::_handle_entry` (chặn entry mới khi đã đủ slot) và `risk.py` (ngân
# sách rủi ro danh mục = risk/lệnh x số này, không tính riêng từng lệnh).
# `state_store.position_state` lưu nhiều row theo `trade_id`, không còn giới
# hạn cứng 1 row. Vẫn chỉ 1 symbol/lúc.
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "1"))

BUY_SCORE_THRESHOLD = float(os.getenv("BUY_SCORE_THRESHOLD", "70"))
WATCH_SCORE_THRESHOLD = float(os.getenv("WATCH_SCORE_THRESHOLD", "55"))

# Pullback filter: chặn BUY/SHORT ngay lúc breakout vừa xác nhận (giá đã chạy xa
# khỏi EMA20, dễ đảo chiều ngay sau — nguyên nhân edge âm đo được ở đa số khung),
# chỉ cho vào lệnh khi giá đã hồi về gần EMA20 (trong biên độ ATR*hệ số này) mà
# vẫn còn giữ được cấu trúc trend.
PULLBACK_ATR_BUFFER = float(os.getenv("PULLBACK_ATR_BUFFER", "0.5"))

# Cron (x) + cửa sổ theo dõi liên tục bằng WS tick (y). y có thể >= x trong
# tương lai (chấp nhận overlap chủ đích qua run_lock).
MONITOR_WINDOW_MINUTES = float(os.getenv("MONITOR_WINDOW_MINUTES", "5"))
MONITOR_POLL_SECONDS = float(os.getenv("MONITOR_POLL_SECONDS", "5"))
ACTIVATION_INTERVAL_MINUTES = float(os.getenv("ACTIVATION_INTERVAL_MINUTES", "60"))
RUN_SCHEDULED = os.getenv("RUN_SCHEDULED", "false").lower() in ("1", "true", "yes", "on")
RUN_CONTINUOUS = os.getenv("RUN_CONTINUOUS", "false").lower() in ("1", "true", "yes", "on")
CONTINUOUS_RETRY_SECONDS = float(os.getenv("CONTINUOUS_RETRY_SECONDS", "30"))
MARKET_TICK_MAX_AGE_SECONDS = float(os.getenv("MARKET_TICK_MAX_AGE_SECONDS", "30"))

# Lease stale chỉ dùng khi heartbeat của owner đã dừng. Vị thế được lưu trong DB,
# nên process thay thế có thể tiếp tục theo dõi mà không giữ lock chết 24 giờ.
RUN_LOCK_STALE_MINUTES = float(os.getenv("RUN_LOCK_STALE_MINUTES", "5"))

# Override qua env để chạy nhiều instance độc lập (vd Paper Trading Champion–Challenger
# trên khung khác) mà không đụng state của instance đang live.
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "state.db")))
LOG_PATH = Path(os.getenv("LOG_PATH", str(BASE_DIR / "logs" / "run.log")))

# Timeframe chính dùng để tính Technical score/indicator trong run.py, và bộ khung
# phụ để tính agreement_ratio đa khung — mặc định khớp TIMEFRAME ở trên, đổi cả 2
# khi chạy 1 instance cho khung khác (vd Paper Trading 2h).
MTF_TIMEFRAMES = tuple(os.getenv("MTF_TIMEFRAMES", "1m,5m,15m").split(","))

# Nhãn phân biệt khi chạy nhiều instance song song (vd Paper Trading Champion–
# Challenger khác khung) — chèn vào đầu message Telegram để không nhầm tín hiệu
# paper với tín hiệu live thật (cùng chung bot/chat nếu không đổi .env riêng).
STRATEGY_LABEL = os.getenv("STRATEGY_LABEL", "")

# Basis-risk gate (`TODO-BASIS-GATE`, hướng veto độc lập, xem
# `docs/decisions.md` mục nguồn dữ liệu): từ chối entry nếu giá
# Binance lệch OKX (sàn thực thi) vượt ngưỡng này — dấu hiệu lỗi data/bất
# thường ở sàn thực thi, KHÔNG dùng giá Binance để tính score. Ngưỡng đo thật
# 2026-08-06 (BTC/USDT, 14 ngày, 20160 nến 1m khớp timestamp giữa OKX-Binance):
# p99=0.019%, p99.9=0.028%, max quan sát=0.034% — mặc định 0.15% (~5x tail đo
# được, gần như không bao giờ trigger ở điều kiện thường, chỉ chặn bất thường
# thật). Mặc định TẮT (`False`) — đổi hành vi chặn entry live, cần bật thủ công
# sau khi xác nhận, không tự bật ngầm.
CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED = os.getenv("CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED", "false").lower() == "true"
MAX_CROSS_EXCHANGE_DIVERGENCE_PCT = float(os.getenv("MAX_CROSS_EXCHANGE_DIVERGENCE_PCT", "0.15"))

# Trọng số Decision Engine — scope hiện tại
WEIGHTS = {
    "technical": 35,
    "order_flow": 28,
    "derivatives": 21,
    "cross_market": 7,
    "sentiment": 6,
    "regime": 3,
}

# Hệ số tương quan Cross-market — tách khỏi hard-code để cập nhật khi tương quan
# thị trường đổi mà không phải sửa logic crossmarket_score.py.
# BTC-DXY đã đảo chiều từ nghịch (2020-2024) sang thuận (từ ~đầu 2026) — dấu DXY_COEF
# dương phản ánh đúng chiều hiện tại, không phải hard-code nghịch chiều như cũ.
CROSSMARKET_NASDAQ_COEF = float(os.getenv("CROSSMARKET_NASDAQ_COEF", "4"))
CROSSMARKET_DXY_COEF = float(os.getenv("CROSSMARKET_DXY_COEF", "4"))
CROSSMARKET_VIX_COEF = float(os.getenv("CROSSMARKET_VIX_COEF", "-1.5"))

# Cache AI Report/AI Review theo thời gian ngắn — tránh gọi LLM trùng (tốn phí/
# thời gian) khi có nhiều lệnh gọi cho cùng 1 trade trong khoảng ngắn.
AI_REPORT_CACHE_TTL_SECONDS = float(os.getenv("AI_REPORT_CACHE_TTL_SECONDS", "300"))

# Health-check độc lập: nếu quá X phút không có lần chạy nào ghi nhận vào
# run_health, coi hệ thống có thể đã dừng và báo Telegram. Health-check được
# launchd lên lịch độc lập với Rule Engine.
HEALTHCHECK_MAX_STALE_MINUTES = float(os.getenv("HEALTHCHECK_MAX_STALE_MINUTES", "30"))
