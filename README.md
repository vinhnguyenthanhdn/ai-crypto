# AI Crypto Signal Engine

Signal engine phát hiện BUY/SELL trên crypto và gửi report qua Telegram. Không tự đặt lệnh thật — chỉ phát tín hiệu. Chi tiết kiến trúc/quyết định thiết kế xem [plan.md](plan.md).

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # điền API key, Telegram token...
```

`.env` cần:
- `EXCHANGE_API_KEY` / `EXCHANGE_API_SECRET` / `EXCHANGE_API_PASSPHRASE`: API key **read-only** (không cấp quyền trade/withdraw).
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.

Không có các key này, hệ thống vẫn chạy được (dữ liệu giá qua CCXT là public), chỉ không gửi được Telegram.

**AI Report** gọi qua Claude Code CLI cục bộ (`claude --print --model ...`), dùng OAuth subscription đã đăng nhập sẵn trên máy — giống cách các cron job khác trong hệ thống (OpenClaw) gọi Claude, không dùng Anthropic API key trả phí theo token. Yêu cầu máy chạy cron đã đăng nhập `claude` CLI (`claude /login`). Nếu lệnh `claude` lỗi/timeout, hệ thống tự dùng bản tóm tắt rule-based thay thế.

## Chạy thử 1 lần

```bash
source .venv/bin/activate
python3 -m src.run
```

Kết quả ghi vào `data/state.db` (bảng `signal_log`, `position_state`) và `logs/`.

## Chạy định kỳ (launchd, macOS)

macOS `crontab` chỉ hỗ trợ độ phân giải theo phút. Nếu cần chu kỳ dưới 1 phút, dùng launchd:

Tạo file `~/Library/LaunchAgents/com.ai-crypto.run.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ai-crypto.run</string>
  <key>ProgramArguments</key>
  <array>
    <string>/absolute/path/to/ai-crypto/.venv/bin/python3</string>
    <string>-m</string>
    <string>src.run</string>
  </array>
  <key>WorkingDirectory</key><string>/absolute/path/to/ai-crypto</string>
  <key>StartInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/absolute/path/to/ai-crypto/logs/run.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/to/ai-crypto/logs/run.err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.ai-crypto.run.plist
```

Nếu chỉ cần chu kỳ theo phút, dùng `crontab -e` với dòng `* * * * * cd /path/to/ai-crypto && .venv/bin/python3 -m src.run` là đủ, không cần launchd.

## Scope hiện tại

Chỉ chạy Phase 1: Technical, Order Flow, Derivatives, Cross-market, Sentiment (Fear & Greed), Market Regime — tất cả rule-based. AI Filter (News/Macro) và On-chain/Whale chưa triển khai, xem lý do và kế hoạch trong [plan.md](plan.md).
