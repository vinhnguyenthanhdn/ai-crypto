# AI Trading Platform V2 – Solution (Final)

## 1. Mục tiêu

Mục tiêu không phải là tạo bot trading kiếm tiền nhanh, mà là xây dựng một **AI Trading Platform** có khả năng học liên tục (Continuous Learning). Bot trading chỉ là một ứng dụng của nền tảng.

Thứ có giá trị nhất theo thời gian là:

- Dữ liệu giao dịch
- Knowledge Base
- Framework nghiên cứu
- Các Strategy Package đã được kiểm chứng

Lợi nhuận sẽ đến từ việc hệ thống ngày càng thông minh hơn.

## 2. Success Criteria

Ưu tiên theo thứ tự:

1. Hệ thống chạy ổn định 24/7.
2. Thu thập dữ liệu chất lượng.
3. Backtest sát thực tế.
4. Paper Trading có edge dương sau phí.
5. Continuous Learning hoạt động.
6. Mới tối ưu lợi nhuận.

**Vốn ban đầu:** 100 USD
**Target ban đầu:** +10 USD/tháng — đây chỉ là KPI để kiểm chứng hệ thống, không phải mục tiêu cuối.

## 3. Triết lý thiết kế

Không lấy AI làm trung tâm. Hệ thống gồm 5 khối:

- **Facts** — lưu toàn bộ dữ liệu thị trường.
- **Knowledge** — lưu kinh nghiệm nghiên cứu.
- **Prediction** — ML dự đoán xác suất.
- **Risk** — bảo vệ vốn.
- **Learning** — tự cải thiện theo thời gian.

## 4. Kiến trúc tổng quan

```
Exchange
   ↓
Collector
   ↓
Market Database
   ↓
Feature Engine
   ↓
Rule Engine
   ↓
Entry Model
   ↓
Risk Engine
   ↓
Execution
   ↓
Trade Logger
   ↓
Backtest
   ↓
Knowledge Base
   ↓
LLM Review
   ↓
Continuous Learning
```

## 5. Nguồn dữ liệu V1

**Chỉ dùng:** Binance, OKX. OKX là sàn chính đã tích hợp và chạy thật ở Phase 1 (xem mục 5b); Binance là nguồn thứ 2, API key read-only đã cấu hình trong `.env` (`BINANCE_API_KEY`/`BINANCE_API_SECRET`).

**Thu thập — chỉ dùng WebSocket cho phần thực sự cần continuous stream, còn lại REST polling:**

| Dữ liệu | Cách lấy | Vì sao |
|---|---|---|
| Trade Stream (tính CVD, order flow) | **WebSocket** | REST poll theo chu kỳ sẽ bỏ sót trade xảy ra giữa 2 lần poll → CVD tính sai |
| Liquidation | **WebSocket** | Binance/OKX chỉ expose liquidation realtime qua WS (`forceOrder`...), không có REST endpoint tương đương đủ tốt |
| OHLCV, Order Book, Funding Rate, Open Interest | **REST polling** (5–30s tuỳ mục đích) | Đổi chậm, sai số do poll gap không đáng kể — REST đơn giản hơn nhiều, không cần xử lý reconnect/backfill |

Collector chạy 24/7 (khác mô hình cron của Phase 1 — xem mục 5b) vẫn chỉ cần giữ kết nối WS cho 2 luồng trên; phần REST polling có thể chạy trong cùng process theo interval riêng.

**Chưa dùng (chỉ thêm khi V1 đã chứng minh có edge):** On-chain, Whale Tracking, Glassnode, Arkham, Nansen, Tin tức.

## 5b. Reuse từ Phase 1

Phase 1 đã build xong và chạy thật một pipeline cron-based (OKX, dry-run/live signal). Thay vì build lại từ đầu, các khối kiến trúc ở trên map vào code đã có như sau:

| Khối (plan-02) | Module đã có (Phase 1) | Trạng thái |
|---|---|---|
| Collector | `src/data/market.py` (CCXT), `crossmarket.py` (yfinance), `sentiment.py` (Fear & Greed) | Reuse trực tiếp |
| Market Database | `src/state_store.py` (SQLite, `data/state.db`) | Reuse — xem mục 6 |
| Feature Engine | `src/indicators/technical.py` + `src/engine/{regime,orderflow,derivatives,sentiment_score,crossmarket_score}.py` | Reuse, nhưng cần bổ sung: hiện mỗi lớp chỉ xuất **score 0-100**, chưa tách riêng **raw feature** (EMA, RSI, ATR, imbalance...) thành Feature Store — cần tách bước này ra để Entry Model (mục 7) và Feature Lineage (mục 13.11) dùng lại được |
| Rule Engine | `src/engine/decision.py` (`compute_total_score`, `decide_entry`) | Reuse trực tiếp — đúng vai trò "V1 chỉ có Rule Engine" ở mục 7 |
| Risk Engine | `src/engine/risk.py` (position sizing theo ATR + %, R:R 1.5), `state_store.py` (`daily_pnl`, `is_trading_halted_today`) | Reuse phần Position Sizing + Daily Loss Limit; còn thiếu Max Drawdown, Kill Switch, Cooldown, Max Concurrent Position (mục 8) — bổ sung sau |
| Execution | Không có — chỉ phát tín hiệu, không đặt lệnh thật | Giữ nguyên, khớp chủ đích V1 |
| Trade Logger | `signal_log` table trong `state_store.py` | Reuse làm nền, cần nâng cấp lên Event Sourcing (mục 13) — hiện mỗi lần chạy ghi 1 dòng tổng hợp, chưa phải Raw Event theo từng bước lifecycle (Signal/Risk Check/Entry/Exit...) |
| Run orchestration / lock | `src/run.py`, `run_lock()` trong `state_store.py` | Reuse trực tiếp — vẫn chống chạy chồng, nhưng nay là cơ chế **bỏ qua khi overlap chủ đích** (cửa sổ theo dõi y phút ≥ chu kỳ cron x phút, xem mục 5d), không chỉ để chặn lỗi treo process |
| Entry Model (ML) | Chưa có | Việc mới — Phase 3 |
| Backtest Engine | Chưa có (Phase 1 chỉ định dùng Freqtrade/vectorbt offline cho lớp Technical, chưa build) | Việc mới — Phase 2 |
| Knowledge Base | Chưa có | Đã tạo vault tại `knowledge/` (xem mục 12b) |
| LLM Review | `src/notify/ai_report.py` (gọi Claude Code CLI local qua OAuth, không dùng Anthropic API key) | Reuse **cơ chế gọi LLM** cho cả AI Report (đang dùng) và AI Review Backtest (mục 11) — khác vai trò, cùng cách gọi |
| Continuous Learning | Chưa có | Việc mới — Phase sau |

**Quyết định kỹ thuật phát sinh khi code Phase 1** (khác nhẹ so với thiết kế ban đầu, giữ lại vì vẫn áp dụng cho V2):

- **`ta` thay cho `pandas-ta`**: `pandas-ta` không còn bảo trì, lỗi với numpy 2.x/pandas 2.x hiện tại. Dùng `ta` (cùng nhóm indicator, API khác cú pháp) để không phải ghim numpy/pandas về bản cũ.
- **Order Flow của Phase 1 dùng REST** (`fetch_order_book` + `fetch_trades`) thay vì WebSocket: vì mỗi lần chạy là 1 process ngắn do cron kích hoạt, không có process nền giữ kết nối WS liên tục giữa các lần chạy. Ở V2, do Collector chạy 24/7 (mục 5), Trade Stream/Liquidation chuyển sang WebSocket thật — ràng buộc "process ngắn" của Phase 1 không còn nữa.

**Trạng thái Phase 1:** hoàn tất và đã chạy thật — cấu hình `.env` với OKX API key read-only + Telegram bot, xác thực OKX OK, gửi Telegram OK, AI Report qua Claude CLI cục bộ OK. Pipeline `run.py` chạy đúng logic: đọc state SQLite → 6 lớp score (Technical, Order Flow, Derivatives, Cross-market, Sentiment, Regime) → Decision Engine → Risk Engine → Telegram + AI Report khi có BUY/SELL. Còn thiếu: thiết lập launchd/cron để chạy định kỳ tự động (mới test dry-run/manual).

## 5c. Khảo sát Open Source

Trước khi tự build tiếp các khối còn thiếu ở mục 5b, đã khảo sát open source có thể tái sử dụng thay vì viết từ đầu:

| Nhu cầu | OSS phù hợp | Đánh giá |
|---|---|---|
| Collector + Execution + Market Database + Backtest **parity với live** (mục 4, 11, 13.7) | **NautilusTrader** (`nautechsystems/nautilus_trader`) | Rust-native, event-driven, deterministic — backtest và live dùng **chung 1 engine, cùng semantics**, mô phỏng fee/latency/order book đúng như mục 11 yêu cầu. Match tốt nhất về kiến trúc, nhưng **chi phí migrate cao**: phải viết lại theo mô hình Strategy/Actor của Nautilus, bỏ pipeline cron custom đã chạy thật ở Phase 1. Cần đánh giá riêng, chưa quyết định adopt. |
| Feature Engine + Entry Model (ML, mục 7) | **Freqtrade FreqAI** | Đúng pattern cần: tách `feature_engineering_*` (base indicator) khỏi model, train LightGBM/XGBoost per-pair, có sẵn feature drift/retrain pipeline. Nhưng là add-on **gắn chặt vào framework Freqtrade** — dùng được nghĩa là chuyển cả execution/collector sang Freqtrade, không tách lẻ module dễ dàng. Dùng để **tham khảo thiết kế** (cách tách feature vs model), không adopt nguyên khối. |
| Market Structure indicator (BOS/CHoCH/Order Block) | **`smart-money-concepts`** (joshyattridge, PyPI) | Đã đúng thư viện được nhắc ở phần Indicator (plan-01 cũ) — xác nhận là lib thật, đang maintain, dùng trực tiếp được, không cần tự viết. |
| Backtest engine độc lập (không đổi cả framework) | ~~vectorbt~~ — **đã loại**, tự viết (`src/backtest/engine.py`) | Cân nhắc ban đầu vectorbt (nhanh, vector hoá) hoặc Backtrader, nhưng vectorbt 1.1.0 yêu cầu `pandas>=3.0.3` trong khi MLflow yêu cầu `pandas<3` — xung đột cứng, không cài chung 1 venv được. Vì MLflow phục vụ nhiều task hơn (Experiment + Champion-Challenger), giữ MLflow và tự viết Backtest Engine (replay Decision Engine trực tiếp, tự tính Sharpe/Drawdown/Win Rate — vài chục dòng, không phức tạp). Đã build + test với dữ liệu thật. |
| Experiment tracking + Model Registry + Champion/Challenger (mục 11, 16, 18) | **MLflow** | Match rất sát: alias `@champion`/`@challenger` đúng khái niệm plan-02 mô tả, Model Registry = Model Card, Experiment Tracking = mục 13.10. Bolt-on library (không phải framework thay thế) — **chi phí adopt thấp**, nên dùng thay vì tự xây Experiment logging từ đầu. Lưu ý triển khai thực tế: (1) MLflow yêu cầu `pandas<3`, xung đột cứng với `vectorbt` (`pandas>=3.0.3`) — đã bỏ vectorbt ở mục Backtest, tự tính Sharpe/Drawdown/Win Rate; (2) MLflow 3.x đã đưa filesystem tracking backend (`file:./mlruns`) vào maintenance mode, báo lỗi thẳng — dùng backend **sqlite** (`data/mlflow.db`) theo khuyến nghị chính thức, đã implement ở `src/experiment.py`. |
| Knowledge Base RAG/MCP trên Obsidian (mục 12, khi vượt vài nghìn note) | **obsidian-kb-plugin (OKB)** hoặc **"Vault as MCP"** | Đúng nhu cầu "Hybrid Search, Embedding, Graph RAG, MCP" mà mục 12 ghi là "sau vài nghìn note mới cần". Chưa cần cài ngay, nhưng đã có lựa chọn cụ thể sẵn khi tới lúc, không cần tự build RAG layer. |
| RL cho Exit AI (Phase 7, tương lai) | **FinRL** (AI4Finance-Foundation) | Chỉ liên quan Phase 7 (Exit AI) — chưa cần ở scope hiện tại (mục 7 dùng supervised LightGBM/XGBoost, không phải RL). Giữ làm tham khảo cho sau. |

**Kết luận:** không đổi cả framework (Nautilus/Freqtrade) ngay bây giờ — Phase 1 đã chạy thật trên pipeline custom, migrate sẽ tốn công và có rủi ro phá vỡ cái đang hoạt động. Ưu tiên theo thứ tự:
1. Adopt MLflow khi tới Phase 2 (Backtest Engine) thay vì tự xây log Experiment.
2. Giữ nguyên `smart-money-concepts` cho Market Structure.
3. Tham khảo thiết kế FreqAI (tách Feature Engine khỏi Model) khi build Entry Model ở Phase 3, không cần cài Freqtrade.
4. Đánh giá riêng NautilusTrader nếu muốn thật sự đạt "backtest = live parity" (mục 13.7) một cách nghiêm túc — quyết định lớn, chưa chốt.

## 5d. Cron với cửa sổ theo dõi liên tục

Thay vì mỗi lần cron chỉ lấy 1 snapshot giá rồi quyết định ngay (bỏ sót biến động giữa 2 lần cron — xem hạn chế đã nêu), `run.py` chuyển sang mô hình:

- Cron kích hoạt mỗi **x = 60 phút**, cấu hình qua launchd/cron.
- Mỗi lần được kích hoạt, process **không thoát ngay** mà mở một cửa sổ theo dõi **y = 5 phút**, trong cửa sổ đó đọc **tick giá thật từ WebSocket** (`collector_ws.py`, đã chạy 24/7 — mục 5) để đánh giá `decide_entry`/`decide_exit` liên tục thay vì 1 lần duy nhất. REST polling (OHLCV/Order Book/Funding/OI) giữ nguyên chu kỳ riêng như mục 5, không đổi.
- Vào lệnh (BUY) hoặc thoát lệnh (SELL) có thể xảy ra tại **bất kỳ thời điểm nào trong cửa sổ y phút**, không chỉ tại thời điểm bắt đầu.
- Với x=60, y=5 hiện tại, y < x nên không xảy ra overlap thật — nhưng thiết kế vẫn giữ nguyên tắc chấp nhận **y ≥ x**: nếu sau này rút ngắn x xuống gần/dưới 5 phút và cửa sổ theo dõi trước chưa kết thúc mà cron kế tiếp đã kích hoạt, dùng `run_lock()` hiện có (mục 5b) để bỏ qua lần cron đó — giống cơ chế chống chạy chồng đã có, chỉ khác là overlap khi đó là tình huống **chủ đích chấp nhận**, không còn thuần là lỗi treo process (xem cập nhật mục 8b).
- Đây là bước trung gian tiến gần event-driven mà không cần đổi cả framework sang NautilusTrader (mục 5c) — vẫn là cron ngắn hạn có giới hạn thời gian sống, không phải daemon 24/7 thật.

## 6. Database

Không dùng Obsidian lưu dữ liệu giao dịch — chỉ dùng cho Knowledge Memory (xem phần 12b, đã tạo tại `knowledge/`).

**V1 dùng lại SQLite hiện có** (`src/state_store.py`, `data/state.db` — đã build ở Phase 1, xem mục 5b) làm Market Database, thay vì dựng mới PostgreSQL/ClickHouse. Ở quy mô 1 sàn/1 symbol, cron chạy theo phút, SQLite đủ đáp ứng và không tốn thêm hạ tầng.

Lưu:

- Tick, Trade, Order Book, Candle, Funding, OI, Liquidation
- **Feature Store** — toàn bộ feature đã tính (EMA, RSI, ATR, Spread, Orderbook Imbalance, Funding Delta, OI Delta...)
- **Trade Store** — Entry, Exit, Fee, Slippage, Latency, PnL

**Khi nào migrate sang PostgreSQL/ClickHouse:** nhiều symbol/sàn chạy song song, cần concurrent write từ nhiều process, hoặc Feature Store/Raw Event (mục 13) phình tới mức query phân tích trên SQLite chậm rõ rệt. Tới lúc đó mới đổi engine, schema logic (xem mục 13) giữ nguyên.

## 7. AI

V1 chỉ có **Rule Engine** + **Entry Model**. Thuật toán ưu tiên: LightGBM, XGBoost. Exit vẫn dùng rule.

Không xây ngay từ đầu: Exit AI, Meta Model, Regime Model.

## 7b. Trọng số Decision Engine

`config.WEIGHTS` (nguồn duy nhất — code không hard-code lại bảng này):

| Lớp | Trọng số | Cơ sở |
|---|---|---|
| Technical | 35% | Từng indicator riêng lẻ yếu (vd MACD cross alone ~40% win rate), nhưng **kết hợp nhiều indicator** cho kết quả tốt hơn hẳn (RSI+MACD ~77% win rate trong backtest công khai) — `technical.py` cộng điểm từ 8 tín hiệu độc lập, đúng tinh thần này nên xứng đáng trọng số cao nhất |
| Order Flow | 28% | Nghiên cứu học thuật cho thấy order flow liên tục cải thiện Sharpe ratio rõ rệt (~3.0-3.6 so với ~1.1-2.7 khi không có). **Lưu ý:** trọng số này giả định chất lượng dữ liệu order flow liên tục — implementation hiện tại (`orderflow.py`) chỉ là REST snapshot 1 thời điểm, chưa dùng WS trade stream thật (đã có ở `collector_ws.py`, mục 5). Nên chuyển sang dùng WS khi Order Flow tham gia Rule Engine để trọng số khớp với chất lượng input |
| Derivatives | 21% | Funding rate + OI được xác nhận có giá trị dự báo, đặc biệt ở **mức cực trị và tốc độ đổi chiều** (funding flip từ dương sang âm thường xảy ra gần đáy/đỉnh cục bộ). `derivatives._funding_score` hiện chỉ đọc **mức tĩnh**, chưa bắt tín hiệu flip này — cải thiện chưa làm, xem `tasks.md` |
| Cross-market | 7% | BTC-Nasdaq tương quan dương nhưng đang yếu dần (92% giữa 2025 → 69% đầu 2026); BTC-DXY **đã đảo chiều từ âm (2020-2024, -0.4 đến -0.8) sang dương** (từ khoảng đầu 2026, theo JPMorgan). `crossmarket_score.py` hiện **hard-code giả định DXY nghịch chiều** (`score = 50 + nasdaq*4 - dxy*4 - vix*1.5`) — công thức này đang lệch với tương quan thực tế hiện tại, cần sửa dấu hệ số DXY và tách hệ số ra khỏi hard-code để định kỳ đối chiếu lại mà không phải sửa logic (xem `tasks.md`) |
| Sentiment | 6% | Fear & Greed là tín hiệu contrarian **chậm** (cập nhật 1 lần/ngày, nhiều lần cron trong ngày đọc cùng giá trị) — trọng số thấp phù hợp vai trò "xác nhận thêm", không phải tín hiệu dẫn dắt |
| Regime | 3% (+ vai trò gate riêng) | Điểm 3% chỉ phản ánh phần đóng góp vào tổng điểm — Regime còn có **vai trò gate cứng độc lập** trong `decide_entry` (mục 8b: `HIGH_VOLATILITY`/`UNKNOWN` chặn BUY tuyệt đối, bất kể tổng điểm). Ảnh hưởng thực tế của Regime lớn hơn nhiều so với con số 3% — không được hiểu nhầm là "ít quan trọng" |

**Nguyên tắc chung:** trọng số phản ánh mức độ tin cậy của layer *tại chất lượng dữ liệu hiện có*, không phải mức độ quan trọng tuyệt đối của khái niệm — khi implementation của 1 layer nâng cấp (vd Order Flow chuyển sang WS), cần xem lại trọng số tương ứng thay vì giữ cố định.

## 8. Risk Engine

Module độc lập, có quyền từ chối mọi lệnh. Bao gồm: Position Sizing, Max Risk/Trade, Daily Loss Limit, Max Drawdown, Kill Switch, Cooldown, Max Concurrent Position.

## 8b. Rủi ro vận hành (kế thừa từ Phase 1)

Danh sách rủi ro vận hành đã xác định và có giải pháp từ Phase 1 — vẫn áp dụng cho V2, một số cần điều chỉnh khi Collector chuyển sang chạy 24/7 (ghi chú riêng):

| Rủi ro | Tác động | Giải pháp |
|---|---|---|
| Không có position sizing / daily loss limit | Một chuỗi lệnh sai liên tiếp có thể lỗ không giới hạn | Risk Engine tính size lệnh theo % vốn cố định (vd 1–2%/lệnh); có bộ đếm lỗ trong ngày, chạm ngưỡng thì tự chuyển toàn hệ thống sang chế độ chỉ-quan-sát đến hôm sau |
| Cron chạy chồng (overlap) | Từ mục 5d, overlap không còn chỉ do process treo — cửa sổ theo dõi y phút có thể ≥ chu kỳ cron x phút, nên lần cron kế tiếp gặp lock là tình huống **bình thường**, không phải lỗi | Dùng file lock/cờ "đang chạy" trong SQLite trước khi xử lý; đang chạy (dù do treo hay do cửa sổ theo dõi chưa xong) thì lần cron mới bỏ qua, tự log lại — không cần phân biệt 2 nguyên nhân. *Ở V2 nếu Collector là 1 process 24/7 (không phải cron), rủi ro này đổi dạng thành "Collector crash/restart" — cần supervisor tự restart thay vì lock giữa các lần cron.* |
| WebSocket rớt kết nối / reconnect (mới, phát sinh từ mục 5) | Mất dữ liệu Trade Stream/Liquidation trong lúc rớt kết nối → CVD/feature tính thiếu | Auto-reconnect có backoff; đánh dấu gap thời gian mất kết nối vào log để Feature Engine biết loại trừ khoảng đó thay vì tính nhầm |
| Exchange API downtime / rate-limit | Fetch lỗi giữa chừng → tính toán trên dữ liệu thiếu, có thể ra tín hiệu sai | Retry có giới hạn (2–3 lần, backoff ngắn); nếu vẫn lỗi thì không tính toán/không phát tín hiệu ở lần đó; log lỗi riêng để theo dõi tỷ lệ downtime |
| Phí + slippage ăn hết lợi nhuận | **Đã xảy ra thật, không còn là rủi ro giả định** — trên khung 5m, khoảng cách Take Profit còn nhỏ hơn cả chi phí khứ hồi, tức lệnh chạy đúng kịch bản tốt nhất vẫn lỗ | Backtest trừ phí maker/taker thực tế + slippage; **Cost Gate ở Risk Engine chặn ngay từ lúc vào lệnh** (mục 8c) thay vì chỉ phát hiện sau khi backtest xong |
| LLM lỗi/timeout | Thiếu lớp filter định tính đúng lúc quan trọng | Timeout ngắn (5–8s) + fallback mặc định về không vào lệnh; log riêng các lần fallback để đánh giá tần suất |
| Backtest overfitting | Score/trọng số "đẹp" trên dữ liệu lịch sử nhưng thất bại khi chạy thật | Walk-forward: tách train/test, không tối ưu trên toàn bộ lịch sử rồi test lại chính nó; theo dõi kết quả live so với kỳ vọng backtest |
| Single point of failure (1 sàn, 1 máy chạy) | Sàn bảo trì/lỗi hoặc máy tắt/mất mạng → hệ thống ngừng mà không ai biết | Health-check riêng biệt, báo Telegram nếu quá X phút không có lần chạy/heartbeat nào — tách khỏi pipeline chính |
| API key bị lộ / quá quyền | Lộ key có quyền rút tiền/đặt lệnh gây thiệt hại trực tiếp | Dùng API key **read-only** (hệ thống chỉ phát tín hiệu, không đặt lệnh thật); lưu qua env/secret store, không hard-code, không commit vào git |
| Chi phí LLM tăng theo tần suất | Cron/collector chạy liên tục sẽ gọi LLM dù dữ liệu chưa đổi → tốn chi phí không cần thiết | Cache kết quả theo thời gian hợp lý (1–5 phút), chỉ gọi lại khi cache hết hạn hoặc có sự kiện mới |
| Lệch thời gian giữa các nguồn dữ liệu | Giá realtime nhưng on-chain/news cập nhật chậm hơn → dùng nhầm dữ liệu cũ | Gắn timestamp cho từng lớp dữ liệu khi log; từ chối dùng lớp nào có dữ liệu quá cũ so với ngưỡng cho phép thay vì âm thầm dùng tạm |
| Thiếu log/observability | Không biết vì sao một tín hiệu đúng/sai, không có dữ liệu để hiệu chỉnh về sau | Log đầy đủ input từng lớp, điểm, đánh giá LLM, quyết định cuối, và kết quả thực tế sau đó (xem mục 13 — thiết kế logging chi tiết hơn Phase 1 nhiều) |

**Lưu ý quan trọng:** không thể đảm bảo lặp lại ổn định lợi nhuận nhỏ trong thời gian ngắn. Trước khi giao dịch thật, phải backtest trên dữ liệu lịch sử và paper trade để đánh giá win rate, drawdown, và ảnh hưởng thực tế của phí/slippage.

## 8c. Cost Gate — biên lợi nhuận phải lớn hơn chi phí

Position Sizing theo ATR (mục 8) trả lời "vào bao nhiêu tiền" nhưng không trả lời "setup này có đáng vào không". Khoảng cách TP/SL sinh ra từ ATR không tự động lớn hơn chi phí giao dịch: trên khung có ATR nhỏ so với giá, TP có thể nằm gần entry hơn cả chi phí khứ hồi — lệnh đi đúng hướng tới tận Take Profit vẫn lỗ.

**Ràng buộc:** Risk Engine từ chối entry khi `TP_distance < MIN_TP_COST_RATIO × chi phí khứ hồi`. Đây là quyền phủ quyết thứ hai của Risk Engine, độc lập với các ngưỡng rủi ro ở mục 8 — nó không hỏi "lỗ tối đa bao nhiêu" mà hỏi "thắng thì có lãi không".

**Nguyên tắc SSOT về chi phí:** `FEE_PCT`/`SLIPPAGE_PCT` khai một lần ở `config.py`, dùng chung cho cả Cost Gate và Backtest Engine. Nếu khai riêng ở 2 nơi, gate sẽ lọc theo mức chi phí khác với mức thực trừ vào PnL — sai lệch âm thầm, không có lỗi nào báo ra.

**Hệ quả chọn khung thời gian:** tỷ lệ `TP_distance / chi phí` là điều kiện cần để một khung thời gian dùng được. Khung nào có tỷ lệ dưới ~2× thì mọi cấu hình entry/exit đều nằm dưới ngưỡng hoà vốn, không phải vấn đề tinh chỉnh tham số. Số đo thực tế theo từng khung và bằng chứng thực nghiệm: `research-technical-signal-edge.md` mục 8.

## 9. Stop Loss

Ban đầu hoàn toàn Rule-Based (ATR, Volatility, Fixed Risk, Trailing Stop). Sau khi đủ dữ liệu mới cho AI học Exit.

## 10. Logging

Mỗi trade đều được lưu: Snapshot, Feature, Entry, Exit, Funding, OI, Fee, Slippage, Latency, Max Profit, Max Drawdown, Outcome. Đây là tài sản quan trọng nhất của hệ thống (chi tiết thiết kế ở phần 13).

## 11. Backtest

Backtest không chỉ để biết lời hay lỗ — phải mô phỏng Fee, Spread, Slippage, Latency, Thanh khoản, và không được có look-ahead bias.

Mỗi Backtest sinh ra một **Experiment**: Feature, Hyperparameter, Dataset, Result, Drawdown, Sharpe, Win Rate — được lưu vào Knowledge Base.

**AI Review Backtest:** LLM đọc Metrics, Trade Log, Config → sinh Summary, Lesson Learned, Pattern, Anti Pattern, Recommendation.

**Chẩn đoán tách nguồn (`scripts/diagnose_backtest.py`):** một chỉ số tổng hợp như win rate không phân biệt được lỗ đến từ tín hiệu, từ rule thoát, hay từ chi phí — dễ dẫn tới sửa nhầm chỗ. Trước khi kết luận nguyên nhân, đo tách rời từng nguồn trên cùng một tập dữ liệu cache:
- **Edge thuần của tín hiệu** — forward return từ điểm fill, không exit rule, không chi phí, so với baseline toàn bộ bar. Đây là phép đo duy nhất trả lời "tín hiệu có dự báo được hướng giá không".
- **Ảnh hưởng exit rule** — cùng tập entry, chạy lại với các cơ chế thoát khác nhau (đủ rule / chỉ SL-TP / thoát cố định sau N bar).
- **Ảnh hưởng chi phí** — mỗi lệnh log 3 mức PnL: gross / sau slippage / sau đủ phí. Lưu ý "gross" phải bỏ **cả** slippage lẫn fee; slippage nằm sẵn trong giá entry/exit nên rất dễ bị tính nhầm là gross.
- **Baseline ngẫu nhiên** — entry random cùng số lệnh, cùng exit rule. Tín hiệu không tách được khỏi baseline này nghĩa là chưa có edge.

**Knowledge từ Backtest:** mỗi lần backtest phải tạo ra Experiment, Lesson Learned, Pattern, Anti Pattern, Decision. Backtest chính là cỗ máy tạo Knowledge.

## 12. Knowledge Base

Chỉ có **một** KB chung, không có KB riêng cho từng model. Dùng **Obsidian**.

**Lưu:** Strategy, Pattern, Anti Pattern, Lesson Learned, Experiment, Architecture, Design Decision, Daily Review.

**Không lưu:** Tick, Order Book, Feature Vector, Dataset.

**Quy tắc note:** một note = một ý tưởng.

| Loại note | Kích thước |
|---|---|
| Trade Review | 20–50 dòng |
| Experiment | 30–80 dòng |
| Strategy | 50–150 dòng |
| Architecture | 100–200 dòng |

**Template chung:** Summary, Context, Observation, Evidence, Decision, Lesson Learned, Related Notes.

**Vector DB:** ban đầu không cần, chỉ dùng Markdown + Full Text Search. Sau vài nghìn note mới cần Hybrid Search, Embedding, Graph RAG, MCP.

### 12b. Vai trò của Obsidian — Data Memory vs Knowledge Memory

"Không dùng Obsidian lưu dữ liệu" nghĩa là không dùng Obsidian làm database giao dịch — Obsidian vẫn rất phù hợp ở vai trò **Knowledge Memory**, không phải **Data Memory**.

```
                    AI Trading Platform
                            │
        ┌───────────────────┴───────────────────┐
        │                                        │
   Data Memory                            Knowledge Memory
        │                                        │
PostgreSQL / ClickHouse                    Obsidian Vault
        │                                        │
 Tick Data                                  Strategy
 Order Book                                 Experiment
 OHLCV                                      Lesson Learned
 Funding                                    Pattern
 OI                                         Anti Pattern
 Trade History                              Architecture
 Feature Store                              Design Decision
                                             Model Card
                                             Daily Review
```

**Vì sao Obsidian phù hợp:**

- Markdown nên AI đọc/ghi rất dễ.
- Có liên kết hai chiều (wikilink), phù hợp xây Knowledge Graph.
- Git quản lý version rất tốt.
- Có plugin MCP/RAG để AI truy cập trực tiếp.
- Con người cũng đọc và chỉnh sửa được.

Nó giống như "Research Notebook" của toàn bộ hệ thống.

**Cấu trúc Vault:** một Vault duy nhất cho toàn hệ thống, đã tạo tại `knowledge/` (mở thư mục này trực tiếp bằng Obsidian):

```
knowledge/
├── Strategies/
├── Experiments/
├── Lessons/
├── Patterns/
├── AntiPatterns/
├── Architecture/
├── Decisions/
├── Research/
├── DailyReview/
├── Models/
│   ├── S001.md
│   ├── S002.md
│   └── Champion.md
└── Backtests/
```

`Models/` không phải Knowledge Base riêng, mà chỉ là Model Card của từng Strategy Package, vẫn nằm trong cùng Vault.

**Artifact nhị phân không vào Obsidian:** file model (`model.pkl`), `config.yaml`, `metrics.json` lưu ở filesystem hoặc object storage. Obsidian chỉ lưu metadata và tri thức dạng note, ví dụ:

```
# Strategy S001

## Summary
## Feature
## Risk
## Backtest
## Paper Trading
## Strength
## Weakness
## Deployment History
## Related Experiments
```

**Kết luận phân tách:**

- PostgreSQL/ClickHouse = Data Memory (sự thật, dữ liệu định lượng).
- Obsidian = Knowledge Memory (kinh nghiệm, quyết định, nghiên cứu, bài học).
- Strategy Package = artifact triển khai (model, config, metrics...).

## 13. Thiết kế Logging chi tiết

Log là trái tim của hệ thống — chỉ lưu BUY/SELL/Profit sẽ không thể học được gì về sau. Thiết kế theo **Event Sourcing + Snapshot + Knowledge**.

### 13.1. Lifecycle của một trade

```
Signal
  ↓
Risk Check
  ↓
Entry
  ↓
Monitoring
  ↓
Exit
  ↓
Evaluation
  ↓
Knowledge
```

Mỗi bước đều sinh log.

### 13.2. Raw Event (Database)

Lưu mọi sự kiện, không overwrite — đây là nguồn sự thật (Source of Truth).

```
Event
- id
- timestamp
- trade_id
- type
- payload (json)
```

`type` ví dụ: `MARKET_TICK`, `FEATURE_UPDATED`, `SIGNAL_GENERATED`, `RISK_REJECTED`, `ENTRY`, `STOP_MOVED`, `TAKE_PROFIT`, `EXIT`, `MODEL_SELECTED`.

```json
{
  "time": "10:01:02.123",
  "type": "ENTRY",
  "price": 114235,
  "confidence": 0.83
}
```

### 13.3. Snapshot

Đây mới là thứ AI đọc — một trade thường chỉ cần Entry Snapshot + Exit Snapshot, không cần đọc toàn bộ Event.

```json
{
  "tradeId": "T001",
  "market": {
    "price": 114235,
    "spread": 0.01,
    "volatility": "medium"
  },
  "feature": {
    "ema20": 114000,
    "ema50": 113700,
    "rsi": 61,
    "funding": 0.012,
    "oi_delta": 5.4
  },
  "risk": {
    "position": 100,
    "sl": 113900
  },
  "model": "S001"
}
```

### 13.4. Trade Summary

Sau khi trade kết thúc, sinh Trade Summary để dùng cho dashboard: Entry, Exit, Holding Time, PnL, Fee, Slippage, Latency, Max Profit, Max DD, Win/Loss.

### 13.5. Trade Evaluation

Ground truth cho AI:

```json
{
  "good_entry": true,
  "good_exit": false,
  "stop_too_close": true,
  "reason": "Orderbook reversed"
}
```

### 13.6. LLM Review

LLM đọc Snapshot + Summary + Evaluation, sinh Observation ("Funding đúng", "Orderbook đảo chiều") và Lesson ("OI phải tăng đồng thời") — lưu vào Obsidian.

### 13.7. Backtest dùng chung pipeline

Backtest không có format log riêng — dùng chung: `Backtest → Trade → Summary → Evaluation → Lesson`. Nhờ vậy Paper Trading và Backtest dùng chung pipeline logging.

### 13.8. Load dữ liệu để AI đánh giá — 4 tầng

Không bao giờ `SELECT *` toàn bộ trades cho LLM đọc (sẽ vượt context). Chia 4 tầng:

| Tầng | Nội dung | Ví dụ |
|---|---|---|
| Level 1 | Trade Summary | 1000 trade → lọc theo Loss |
| Level 2 | Trade Snapshot | Load Snapshot của 1 trade cụ thể |
| Level 3 | Related Trade | Embedding Search → 20 trade giống nhất (hoặc Obsidian + RAG) |
| Level 4 | Knowledge | Lesson, Pattern, Decision liên quan — LLM đọc Knowledge trước tiên |

### 13.9. Vòng lặp Improve

Ví dụ: trong 100 trade, 70 trade "Funding tăng → Win", 30 trade "Funding tăng → Loss". LLM so sánh 30 trade lỗi qua Snapshot, phát hiện OI giảm ở nhóm này → sinh Knowledge: "Funding chỉ có ý nghĩa nếu OI tăng." Knowledge này quay lại Feature Engine.

### 13.10. Experiment logging

Mỗi Experiment (ví dụ `EXP032`) log Config, Feature, Metrics, Decision — LLM sinh Recommendation (ví dụ: giữ Funding, loại RSI).

### 13.11. Feature Lineage

Không chỉ lưu feature hiện tại, phải lưu feature được tạo ra như thế nào — để biết tác động khi thay đổi/loại bỏ một feature.

```
Feature: funding_delta_5m

Source: Funding API
Transformation: Funding(now) - Funding(5 phút trước)
Used by: S001, S004, S007
Experiments: EXP012, EXP021, EXP045
Kết quả: +3.2% Sharpe
```

Nhờ vậy khi AI đề xuất bỏ một feature, biết ngay có bao nhiêu Strategy Package đang dùng, nó đã được thử trong experiment nào, và đã cải thiện hay làm giảm hiệu quả.

### 13.12. Kiến trúc log tổng thể

```
Raw Events (PostgreSQL/ClickHouse)
        │
        ▼
Trade Snapshot
        │
        ▼
Trade Summary
        │
        ▼
Trade Evaluation
        │
        ├── Dashboard
        ├── Backtest Metrics
        ├── Model Comparison
        └── LLM Review
                  │
                  ▼
         Obsidian Knowledge Base
                  │
                  ▼
      RAG + Continuous Learning
```

Mỗi tầng chỉ xử lý đúng mức thông tin cần thiết: Raw Events để tái tạo và kiểm toán, Snapshot để ML/LLM phân tích một giao dịch, Summary để thống kê và so sánh, Knowledge để tích lũy kinh nghiệm lâu dài — nhờ vậy không cần cho LLM đọc hàng triệu dòng log mà vẫn đủ ngữ cảnh để đánh giá và đề xuất cải tiến.

## 14. Continuous Learning

```
Trade
  ↓
Log
  ↓
LLM Review
  ↓
Knowledge
  ↓
Improve Feature
  ↓
Retrain
  ↓
Deploy
  ↓
Trade tiếp
```

LLM không tự sửa model — chỉ phân tích, đề xuất, sinh Knowledge. ML pipeline quyết định có retrain hay không.

## 15. Strategy Package

Không build nhiều project — chỉ có **một** Trading Framework. Model chỉ là một Strategy Package, gồm: Config, Model Weight, Feature Version, Risk Profile, Exit Rule, Backtest Result, Paper Result, Model Card. Framework luôn giống nhau, chỉ thay Strategy Package.

## 16. Champion–Challenger

Luôn có Champion (đang giao dịch) và Challenger (đang thử nghiệm). Challenger phải backtest tốt, paper trading ổn định, và vượt Champion trong thời gian đủ dài mới được thay Champion.

## 17. Đánh giá Strategy Package

Không chọn theo Profit đơn thuần — đánh giá theo nhiều tiêu chí: Return, Sharpe Ratio, Sortino Ratio, Profit Factor, Win Rate, Expectancy, Max Drawdown, Volatility, Stability qua nhiều giai đoạn, Paper Trading. Tổng hợp thành một Overall Score.

## 18. Model Card

Mỗi Strategy Package có: Version, Feature, Training Dataset, Metrics, Điểm mạnh, Điểm yếu, Điều kiện phù hợp, Decision History — đây là lịch sử của model, không phải Knowledge Base.

## 19. Regime (Phase sau)

Không cố tìm một model thắng mọi lúc. Sau khi V1 ổn định sẽ thêm Regime Detection, Model Selector, Risk Profile Selector:

```
Trending        → Model A
Sideway         → Model B
High Volatility → Model C
```

Không phải nhiều code — chỉ nhiều Strategy Package.

## 20. Roadmap

| Phase | Nội dung |
|---|---|
| 1 | Collector, Database, Feature Engine, Rule Engine, Logging |
| 2 | Backtest Engine, Experiment Engine, Paper Trading |
| 3 | Entry Model, Strategy Package, Champion–Challenger |
| 4 | Risk Engine hoàn chỉnh |
| 5 | AI Review, Obsidian Knowledge Base, RAG |
| 6 | **Đưa chiến lược về mức có edge dương** — chốt khung thời gian, hạ chi phí giao dịch, sửa timing entry |
| 7 | Exit AI |
| 8 | Regime Detection, Dynamic Strategy Selection |
| 9 | Multi-model, Multi-regime, On-chain, Macro Data, Advanced Feature Engineering |

Phase 6 chèn vào giữa vì là **điều kiện chặn**: chẩn đoán ở mục 11 cho thấy chi phí giao dịch đang lớn hơn biên lợi nhuận mục tiêu, nên mọi lớp thông minh hơn thêm vào sau đó (Exit AI, Regime Detection) đều không thể đo được cải thiện — cải thiện sẽ nằm dưới nhiễu của chi phí.

## 21. Triết lý cuối cùng

Đây không phải dự án xây một bot trading, mà là xây một **Research & Learning Platform for Quantitative Trading**. Mọi thành phần đều phục vụ một vòng lặp duy nhất:

> Thu thập dữ liệu → Thử nghiệm → Đánh giá → Sinh tri thức → Cải tiến → Triển khai → Thu thập dữ liệu mới.

Nếu sau 2–3 năm hệ thống thành công, tài sản lớn nhất sẽ không phải là Champion model, mà là:

- Market Data được tích lũy.
- Feature Store ngày càng phong phú.
- Knowledge Base chứa hàng nghìn bài học và thí nghiệm.
- Hàng trăm Strategy Package đã được kiểm chứng.
- Framework nghiên cứu giúp tạo, đánh giá và triển khai chiến lược mới một cách có hệ thống.

Đó là lợi thế cạnh tranh bền vững hơn nhiều so với việc cố gắng tìm một mô hình AI "thần kỳ" ngay từ đầu.
