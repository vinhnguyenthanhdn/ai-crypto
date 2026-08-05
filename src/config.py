import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance")
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ACCOUNT_EQUITY_USD = float(os.getenv("ACCOUNT_EQUITY_USD", "500"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.5"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "5"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "15"))
COOLDOWN_MINUTES = float(os.getenv("COOLDOWN_MINUTES", "30"))
# Thời gian giữ lệnh tối thiểu trước khi rule thoát theo momentum (MACD/RSI/Volume/
# EMA) được áp dụng — SL/TP vẫn kiểm tra ngay từ bar đầu. Phát hiện từ AI Review
# Backtest (exit quá sớm làm win rate thấp bất thường).
MIN_HOLD_MINUTES = float(os.getenv("MIN_HOLD_MINUTES", "15"))

# Risk Engine (src/engine/risk.py) — khoảng cách stop theo ATR + risk:reward.
# Tách ra config để backtest thử nhiều mức mà không sửa code.
ATR_STOP_MULTIPLIER = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))
RISK_REWARD_RATIO = float(os.getenv("RISK_REWARD_RATIO", "1.5"))

# Chi phí giao dịch — nguồn duy nhất cho cả Risk Engine (cost gate bên dưới) và
# Backtest Engine. FEE_PCT/SLIPPAGE_PCT là chi phí MỘT chiều, dạng thập phân
# (0.001 = 0.1%); chi phí khứ hồi = (FEE_PCT + SLIPPAGE_PCT) * 2.
FEE_PCT = float(os.getenv("FEE_PCT", "0.001"))  # 0.1% taker
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0005"))  # 0.05%, xấp xỉ độ sâu order book

# Cost gate: từ chối vào lệnh khi khoảng cách Take Profit không đủ lớn so với chi
# phí khứ hồi. Không có ràng buộc này, phần lớn lệnh trên khung nhiễu (ATR nhỏ)
# lỗ ngay cả khi chạm đúng TP.
# k=2.5 nghĩa là TP phải cách entry ít nhất 2.5 lần chi phí khứ hồi.
MIN_TP_COST_RATIO = float(os.getenv("MIN_TP_COST_RATIO", "2.5"))
# Kiến trúc hiện tại chỉ giữ 1 position_state (1 symbol/1 lệnh tại 1 thời điểm — xem
# state_store.position_state). Max Concurrent Position > 1 cần refactor state đa vị thế,
# chưa hỗ trợ — giữ nguyên 1 cho tới khi có multi-symbol.
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
