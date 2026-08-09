# AI Crypto Signal Engine

Rule-based signal engine cho crypto, có Paper Trading và Telegram report. Hệ thống không đặt lệnh thật trên sàn.

- Quyết định kiến trúc: `docs/decisions.md`.
- Việc đang làm và dependency chặn test: `docs/inprogress.md`.
- Việc chưa bắt đầu và rủi ro còn lại: `docs/todo.md`.
- Kết quả và contract backtest đã chạy: `docs/backtest-results.md`.
- Chi phí thực thi, thuế và ràng buộc pháp lý: `docs/execution-cost.md`.
- Mức tin cậy từng nhánh code và lỗi đã xác nhận: `docs/code-audit.md`.
- Quy tắc làm việc: `CLAUDE.md`.

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

API key chỉ được cấp quyền read-only, không cấp trade/withdraw. Không có API key hệ thống vẫn có thể dùng public market data. Telegram token/chat ID là tùy chọn.

AI Report gọi Claude CLI cục bộ. Nếu CLI lỗi hoặc timeout, hệ thống dùng report rule-based.

## Chạy một lần

```bash
source .venv/bin/activate
python3 -m src.run
```

State mặc định nằm ở `data/state.db`.

## Runtime Paper Trading hiện tại

macOS dùng launchd, không dùng cron. Bốn service hiện tại:

- `com.ai-crypto.paper`: Rule Engine theo scheduled monitoring window.
- `com.ai-crypto.collector-ws-paper`: WebSocket trades/tick/liquidation.
- `com.ai-crypto.health-check`: health-check độc lập.
- `com.ai-crypto.dashboard`: dashboard local.

Các plist nằm tại `~/Library/LaunchAgents/`. Rule Engine, collector và dashboard
Rule Engine scheduler, collector và dashboard dùng `KeepAlive`; health-check
dùng `StartInterval`.

```bash
launchctl print gui/$(id -u)/com.ai-crypto.paper
launchctl print gui/$(id -u)/com.ai-crypto.collector-ws-paper
launchctl print gui/$(id -u)/com.ai-crypto.health-check
launchctl print gui/$(id -u)/com.ai-crypto.dashboard
```

Scheduler nhẹ của Rule Engine neo mốc bắt đầu mỗi
`ACTIVATION_INTERVAL_MINUTES`; mỗi activation poll giá theo
`MONITOR_POLL_SECONDS` trong `MONITOR_WINDOW_MINUTES`, rồi nhả run lock và ngủ
tới mốc start kế tiếp. Nếu một window chạy quá interval thì bỏ qua slot đã lỡ,
không catch-up dồn; run lock vẫn là lớp bảo vệ thứ hai.

## Runtime config

`config/paper.env` là runtime SSOT cho Rule Engine và collector qua
`RUNTIME_ENV_PATH`; dashboard đọc/ghi cùng file. Thay đổi cần restart các service
đang chạy để process nạp lại config.

## Dashboard

```bash
source .venv/bin/activate
python3 scripts/dashboard_server.py
```

Dashboard bind tại `http://127.0.0.1:8787` và có xác thực bằng session/password. Cloudflare Tunnel chưa được cài trong runtime hiện tại.

Dashboard đọc trực tiếp trạng thái launchd của `com.ai-crypto.paper`, hiển thị
daemon/poll/refresh cadence và không còn endpoint/nút cron cũ.

## Health-check

Health-check chạy bằng launchd riêng và đọc DB Paper Trading:

```bash
.venv/bin/python3 scripts/health_check.py --db-path data/state_paper.db --label paper
```

Heartbeat hiện đã được cập nhật trong tick loop, nhưng health model vẫn cần tách process/data/collector freshness. Xem `TODO-HEARTBEAT`.

## Backtest

Quét nhanh bar-close:

```bash
.venv/bin/python3 scripts/run_backtest.py
```

Paper-style tick proxy:

```bash
.venv/bin/python3 scripts/run_paper_backtest.py --timeframe 1h --tick-timeframe 1m --days 180 --walk-forward 2 --no-mlflow
```

Paper-style backtest S/R dùng chung decision/risk/accounting primitives với live,
fill proxy OHLC adverse-first và manifest tái lập được. Kết quả cũ của các engine
khác vẫn provisional cho tới `TODO-REVALIDATE-BACKTESTS`.

Accelerated Paper lifecycle cho staggered-pullback, dùng DB riêng và simulated
clock (runner từ chối DB runtime thật):

```bash
.venv/bin/python scripts/run_staggered_paper_replay.py \
  --flow-cache data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz \
  --split-artifact data/backtests/staggered_slow_pullback_9y.json \
  --years 3 --db data/backtests/staggered_paper_replay_3y.db \
  --out data/backtests/staggered_paper_replay_3y.json --overwrite
```

## Scope hiện tại

- Runtime ra quyết định bằng Rule Engine; Entry Model chưa serving.
- Champion hiện tại là `rule_engine_v1`; Challenger chưa được vận hành.
- Basis-risk gate đã implement nhưng mặc định tắt.
- Swap chưa được phép bật trước khi hoàn tất integration/parity tests.
- Related Trade/RAG và Cloudflare remote access đang pending.
