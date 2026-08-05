# AI Crypto Signal Engine

Signal engine phát hiện BUY/SELL trên crypto và gửi report qua Telegram. Không tự đặt lệnh thật — chỉ phát tín hiệu. Chi tiết kiến trúc/quyết định thiết kế xem [docs/decisions.md](docs/decisions.md); việc còn phải làm + lưu ý xem [docs/todo.md](docs/todo.md).

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

## Paper Trading Dashboard

Dashboard quản lý instance Paper Trading (cấu hình hiện tại xem `config/paper.env` hoặc trực tiếp trên dashboard) — xem trạng thái, sửa config, bật/tắt cron, xem log, xem lịch sử lệnh + PnL, kill switch.

**Chạy local (không cần cloud):**
```bash
source .venv/bin/activate
python3 scripts/dashboard_server.py   # http://127.0.0.1:8787
```
Lần đầu chạy in ra mật khẩu ngẫu nhiên — lưu lại, không hiện lại lần 2 (xoá `config/dashboard_secret.json` để sinh mật khẩu mới).

**Đã cài sẵn để chạy nền + truy cập từ xa** (launchd, xem `~/Library/LaunchAgents/com.ai-crypto.dashboard.plist` và `com.ai-crypto.cloudflared.plist`): dashboard chạy nền qua launchd, và Cloudflare Tunnel (quick tunnel, không cần tài khoản) expose ra 1 URL public. Lấy URL hiện tại:
```bash
grep trycloudflare logs/cloudflared.log | tail -1
```
**Lưu ý quan trọng: URL quick tunnel KHÔNG cố định** — đổi mỗi khi `cloudflared` restart (reboot máy, hoặc launchd tự khởi động lại khi rớt kết nối). Muốn URL cố định cần tài khoản Cloudflare + 1 domain đã add vào Cloudflare để tạo named tunnel — chưa thiết lập, làm sau nếu cần.

**3 service chạy nền cho instance Paper Trading** (bắt buộc phải đủ cả 3, thiếu `collector-ws-paper` thì giá sẽ đứng yên suốt cửa sổ theo dõi — xem `docs/todo.md`):
- `com.ai-crypto.dashboard` — web dashboard
- `com.ai-crypto.cloudflared` — tunnel truy cập từ xa
- `com.ai-crypto.collector-ws-paper` — WebSocket tick giá thật (24/7, không theo chu kỳ cron, khác `run_paper.sh`)

Quản lý: `launchctl unload/load ~/Library/LaunchAgents/com.ai-crypto.<tên>.plist`.

## Scope hiện tại

Chỉ chạy: Technical, Order Flow, Derivatives, Cross-market, Sentiment (Fear & Greed), Market Regime — tất cả rule-based. AI Filter (News/Macro) và On-chain/Whale chưa triển khai, xem lý do và kế hoạch trong [docs/decisions.md](docs/decisions.md).
