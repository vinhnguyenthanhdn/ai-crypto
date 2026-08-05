import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance")
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
EXCHANGE_API_PASSPHRASE = os.getenv("EXCHANGE_API_PASSPHRASE", "")

# Sàn thứ 2 — chỉ dùng cho Collector/Feature Store (cross-exchange check), KHÔNG
# tham gia Rule Engine/Decision Engine chính (vẫn 1 sàn duy nhất, xem plan-02.md mục 5b).
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
# EMA) được áp dụng — SL/TP vẫn kiểm tra ngay từ bar đầu. Xem mục 9, phát hiện từ
# AI Review Backtest (exit quá sớm làm win rate thấp bất thường).
MIN_HOLD_MINUTES = float(os.getenv("MIN_HOLD_MINUTES", "15"))

# Risk Engine (xem plan-02.md mục 8, src/engine/risk.py) — khoảng cách stop theo
# ATR + risk:reward. Tách ra config để backtest thử nhiều mức mà không sửa code.
ATR_STOP_MULTIPLIER = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))
RISK_REWARD_RATIO = float(os.getenv("RISK_REWARD_RATIO", "1.5"))

# Chi phí giao dịch — nguồn duy nhất cho cả Risk Engine (cost gate bên dưới) và
# Backtest Engine. FEE_PCT/SLIPPAGE_PCT là chi phí MỘT chiều, dạng thập phân
# (0.001 = 0.1%); chi phí khứ hồi = (FEE_PCT + SLIPPAGE_PCT) * 2.
FEE_PCT = float(os.getenv("FEE_PCT", "0.001"))  # 0.1% taker
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0005"))  # 0.05%, xấp xỉ độ sâu order book

# Cost gate (xem docs/research-technical-signal-edge.md mục 6.1): từ chối vào lệnh
# khi khoảng cách Take Profit không đủ lớn so với chi phí khứ hồi. Không có ràng
# buộc này, phần lớn lệnh trên khung nhiễu (ATR nhỏ) lỗ ngay cả khi chạm đúng TP.
# k=2.5 nghĩa là TP phải cách entry ít nhất 2.5 lần chi phí khứ hồi.
MIN_TP_COST_RATIO = float(os.getenv("MIN_TP_COST_RATIO", "2.5"))
# Kiến trúc hiện tại chỉ giữ 1 position_state (1 symbol/1 lệnh tại 1 thời điểm — xem
# state_store.position_state). Max Concurrent Position > 1 cần refactor state đa vị thế,
# chưa hỗ trợ — giữ nguyên 1 cho tới khi có multi-symbol.
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "1"))

BUY_SCORE_THRESHOLD = float(os.getenv("BUY_SCORE_THRESHOLD", "70"))
WATCH_SCORE_THRESHOLD = float(os.getenv("WATCH_SCORE_THRESHOLD", "55"))

# Cron (x) + cửa sổ theo dõi liên tục bằng WS tick (y) — xem plan-02.md mục 5d.
# y có thể >= x trong tương lai (chấp nhận overlap chủ đích qua run_lock, mục 8b).
MONITOR_WINDOW_MINUTES = float(os.getenv("MONITOR_WINDOW_MINUTES", "5"))
MONITOR_POLL_SECONDS = float(os.getenv("MONITOR_POLL_SECONDS", "5"))

DB_PATH = BASE_DIR / "data" / "state.db"
LOG_PATH = BASE_DIR / "logs" / "run.log"

# Trọng số Decision Engine — scope hiện tại (xem plan-02.md, phần "Nhóm tín hiệu & trọng số")
WEIGHTS = {
    "technical": 35,
    "order_flow": 28,
    "derivatives": 21,
    "cross_market": 7,
    "sentiment": 6,
    "regime": 3,
}

# Hệ số tương quan Cross-market (xem plan-02.md mục 7b) — tách khỏi hard-code để
# cập nhật khi tương quan thị trường đổi mà không phải sửa logic crossmarket_score.py.
# BTC-DXY đã đảo chiều từ nghịch (2020-2024) sang thuận (từ ~đầu 2026) — dấu DXY_COEF
# dương phản ánh đúng chiều hiện tại, không phải hard-code nghịch chiều như cũ.
CROSSMARKET_NASDAQ_COEF = float(os.getenv("CROSSMARKET_NASDAQ_COEF", "4"))
CROSSMARKET_DXY_COEF = float(os.getenv("CROSSMARKET_DXY_COEF", "4"))
CROSSMARKET_VIX_COEF = float(os.getenv("CROSSMARKET_VIX_COEF", "-1.5"))
