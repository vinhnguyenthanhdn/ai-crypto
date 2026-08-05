# Task List — Implementation Tracking

Theo dõi tiến độ implement theo Roadmap ở `plan-02.md` mục 20. Danh sách rủi ro/quyết định OSS/reuse xem `plan-02.md` mục 5b, 5c, 8b. Ý tưởng chưa chốt (cần đánh giá thêm trước khi thành task) xem `backlog.md`.

**Cách cập nhật:** tick `[x]` khi xong, thêm ghi chú ngắn (ngày + 1 dòng) ngay dưới task nếu cần bối cảnh. Không viết lại toàn bộ mục — chỉ sửa đúng task đang đổi trạng thái. Task mới phát sinh thì thêm vào đúng Phase liên quan, không dồn vào cuối file.

---

## Phase 1 — Collector, Database, Feature Engine, Rule Engine, Logging

- [x] Collector OKX (CCXT: OHLCV, order book, trades, funding, OI) — `src/data/market.py`
- [x] Collector Cross-market (yfinance) + Sentiment (Fear & Greed) — `src/data/crossmarket.py`, `src/data/sentiment.py`
- [x] Collector Binance song song OKX — `market.get_binance_exchange()`/`fetch_cross_exchange_price()`, ghi vào `feature_snapshot.binance_cross_check` (đã test với key thật: lệch giá 0.0055% so với OKX). Chỉ để đối chiếu ở Feature Store, không tham gia Rule Engine (giữ đúng single-exchange decision của Phase 1, xem mục 5b)
- [x] Chuyển Trade Stream + Liquidation sang WebSocket (mục 5) — `src/collector_ws.py`, process riêng chạy 24/7 (`python -m src.collector_ws`), dùng `ccxt.pro` (đã bundle miễn phí trong ccxt hiện tại, khác ghi chú "trả phí" cũ ở Phase 1). Tích CVD từ trade stream thật, flush vào `kv_store` mỗi 30s; log Liquidation + WS_GAP_START/END vào `event_log`. Đã smoke-test ~35s với kết nối thật, không lỗi, CVD flush đúng (`cvd_ws_BTC/USDT = -0.747`). `run.py`/cron vẫn REST cho OHLCV/Order Book/Funding/OI, không đổi
- [x] Market Database SQLite — `src/state_store.py` (mục 6)
- [x] Indicator + Technical score engine (`ta`) — `src/indicators/technical.py`
- [x] Order Flow / Derivatives / Regime / Sentiment / Cross-market score (rule-based) — `src/engine/*.py`
- [x] Feature Store: tách **raw feature** (EMA, RSI, ATR, imbalance...) ra khỏi bước tính score — mỗi engine (`technical`, `orderflow`, `derivatives`, `regime`, `sentiment_score`, `crossmarket_score`) trả thêm key `raw`; `state_store.log_feature_snapshot()` ghi vào bảng `feature_snapshot` mỗi lần chạy (đã test với dữ liệu thật)
- [x] Rule Engine (tổng hợp trọng số + ngưỡng BUY/WATCH/IGNORE) — `src/engine/decision.py`
- [x] Nâng cấp Logging lên Event Sourcing + Snapshot (mục 13.1-13.4) — bảng `event_log` (`state_store.log_event/get_events`), type `FEATURE_UPDATED/SIGNAL_GENERATED/RISK_REJECTED/ENTRY/EXIT`; payload ENTRY/EXIT chính là Snapshot; `get_trade_summary()` ghép cặp ENTRY/EXIT theo `trade_id`. Trade Evaluation + LLM Review (mục 13.5/13.6) để chung với task "AI Review Backtest" ở Phase 5
- [x] `run.py` chuyển sang mô hình cron 60 phút + cửa sổ theo dõi liên tục 5 phút bằng WS tick (mục 5d) — `_current_price()` đọc `state_store.get_last_tick()` (do `collector_ws.py` ghi mỗi trade batch), vòng lặp `MONITOR_WINDOW_MINUTES`/`MONITOR_POLL_SECONDS` gọi lại `decide_entry`/`decide_exit` mỗi tick; `signal_log` chỉ ghi khi action đổi (tránh spam). Đã smoke-test với cửa sổ rút ngắn, log đúng, không trùng lặp
- [x] **[Review trọng số/TA, mục 7b]** Sửa dấu hệ số DXY trong `crossmarket_score.py` — tách `CROSSMARKET_{NASDAQ,DXY,VIX}_COEF` ra `config.py`/`.env`, đổi dấu DXY sang thuận (đúng tương quan hiện tại). Đã test với dữ liệu thật, không lỗi
- [x] **[Review trọng số/TA, mục 7b]** `derivatives._funding_score` bổ sung bắt tín hiệu **đảo chiều** (funding flip dấu, so với `last_funding_pct` lưu ở `kv_store`) — flip dương→âm cho điểm cao (95, gần đáy cục bộ), flip âm→dương cho điểm thấp (15, gần đỉnh cục bộ), ngoài mức tĩnh cũ
- [x] **[Review trọng số/TA, mục 7b]** Order Flow: `run.py` giờ ưu tiên lấy CVD từ `state_store.get_ws_cvd()` (do `collector_ws.py` flush mỗi 30s) thay REST snapshot khi có sẵn và đủ mới (≤120s); `orderflow.py` tự fallback REST nếu WS chưa chạy/dữ liệu cũ, ghi rõ `cvd_source` vào raw feature để biết đang dùng nguồn nào. Chưa đổi lại trọng số 28% — cần theo dõi thực tế khi WS chạy ổn định trước khi quyết định tăng/giảm

## Phase 2 — Backtest Engine, Experiment Engine, Paper Trading

- [x] Backtest Engine mô phỏng Fee/Slippage, không look-ahead bias (mục 11) — `src/backtest/engine.py` + `scripts/run_backtest.py`. **Không dùng vectorbt** (phát hiện xung đột cứng: vectorbt cần `pandas>=3`, MLflow cần `pandas<3` — không cài chung được), tự tính Total Return/Max Drawdown/Sharpe/Win Rate. Chỉ replay được Technical+Regime (Order Flow/Derivatives/Cross-market/Sentiment neutral 50 — thiếu dữ liệu lịch sử), nên tổng điểm tối đa backtest đạt được (~68.7) luôn dưới `BUY_SCORE_THRESHOLD` mặc định (70) — dùng tham số `buy_threshold`/`watch_threshold` để calibrate riêng, không đổi ngưỡng live. Đã test với dữ liệu thật 30 ngày BTC/USDT 5m (8640 bar, ~11s sau khi tối ưu O(n) — ban đầu O(n²) bị treo)
- [x] Experiment Engine — adopt **MLflow** (`src/experiment.py`, `log_backtest_run()`), wiring vào `scripts/run_backtest.py`. Dùng backend **sqlite** (`data/mlflow.db`) chứ không phải filesystem `mlruns/` — phát hiện thực tế: MLflow 3.x đã đưa filesystem backend vào maintenance mode, báo lỗi thẳng nếu dùng (khuyến nghị chính thức chuyển sang DB backend). Đã test: log run thật, query lại bằng `mlflow.search_runs()` ra đúng metrics
- [x] Paper Trading loop — `scripts/paper_trading_report.py`: thống kê edge (win rate, total return, max drawdown, Sharpe) từ lệnh ENTRY/EXIT **thật** đã phát sinh qua `run.py` (dùng chung `compute_stats()` với Backtest Engine, đúng mục 13.7 "dùng chung pipeline"), log vào MLflow experiment riêng `ai-crypto-paper-trading`. Đã test end-to-end với 1 trade giả lập (đã dọn sạch sau test). Bản thân vòng lặp Paper Trading = `run.py` chạy định kỳ qua cron/launchd (đã có hướng dẫn trong README) — không đặt lệnh thật, đúng chủ đích V1 (mục 5b, Execution)

## Phase 3 — Entry Model, Strategy Package, Champion–Challenger

- [x] Entry Model LightGBM (mục 7) — `src/ml/entry_model.py` (`build_dataset`/`train_entry_model`) + `scripts/train_entry_model.py`. Feature = raw indicator từ `technical.score_from_indicators`/`regime.classify_regime` (tách feature khỏi model, tham khảo FreqAI ở mục 5c, không cài Freqtrade); label = forward return sau `horizon_bars` vượt ngưỡng; **walk-forward split** (không random, đúng mục 8b chống overfitting). Đã train với dữ liệu thật 60 ngày BTC/USDT 5m (17058 dòng): accuracy 0.892, AUC 0.614 — khiêm tốn nhưng đúng kỳ vọng vì chỉ dùng Technical+Regime (cùng giới hạn dữ liệu như Backtest Engine). Log qua MLflow + đăng ký vào Model Registry (`entry-model` v1) — đã verify query lại được
- [x] Strategy Package format (Config, Model Weight, Feature Version, Risk Profile, Exit Rule, Backtest/Paper Result, Model Card — mục 15) — `src/strategy_package.py` + `scripts/build_strategy_package.py`. Lưu 2 nơi đúng phân tách mục 12b: manifest JSON (Data Memory) tại `data/strategy_packages/<name>.json`, Model Card Markdown (Knowledge Memory, mục 18) tại `knowledge/Models/<name>.md`. Đã test tạo `S001` thật từ model `entry-model` v1 + kết quả backtest thật
- [x] Champion–Challenger qua MLflow alias `@champion`/`@challenger` (mục 16) — `src/champion_challenger.py` + `scripts/champion_challenger.py` (status/set-challenger/promote). Serving code chỉ cần `models:/entry-model@champion`, không cần đổi code khi promote version mới. Đã test end-to-end thật: set challenger → promote → `load_champion_model()` → `predict_proba()` ra kết quả đúng. **Phát hiện + fix quan trọng khi test:** MLflow (sqlite tracking backend) mặc định lưu artifact (model file) tương đối theo cwd (`./mlruns`) — dễ vỡ nếu chạy script từ thư mục khác hoặc xoá nhầm tưởng là thư mục rác. Đã sửa `experiment.py` chỉ định `artifact_location` tuyệt đối dưới `data/mlruns/<experiment>` khi tạo experiment mới

## Phase 4 — Risk Engine hoàn chỉnh

- [x] Position Sizing theo ATR + % vốn, R:R 1.5 — `src/engine/risk.py`
- [x] Daily Loss Limit — `state_store.py` (`daily_pnl`, `is_trading_halted_today`)
- [x] Max Drawdown — `state_store.get_max_drawdown_pct()`, tự bật Kill Switch khi vượt `MAX_DRAWDOWN_PCT` (`run.py`)
- [x] Kill Switch — `state_store.{is_kill_switch_on,set_kill_switch}`, toggle thủ công qua `scripts/kill_switch.py`
- [x] Cooldown — `state_store.{record_exit_now,cooldown_remaining_seconds}`, chặn BUY trong `COOLDOWN_MINUTES` sau lệnh SELL gần nhất
- [x] Max Concurrent Position — kiến trúc hiện tại chỉ có 1 `position_state` (1 symbol), đã enforce = 1 tự nhiên qua nhánh `IN_POSITION` return sớm trong `run.py`; `MAX_CONCURRENT_POSITIONS` config để chuẩn bị cho multi-symbol sau này
- [x] **Cost Gate** (mục 8c) — `config.MIN_TP_COST_RATIO` (mặc định 2.5); `risk.compute_position_plan`/`compute_short_position_plan` trả thêm `edge_viable`/`skip_reason`/`tp_distance_pct`, `run.py` + cả 2 Backtest Engine bỏ qua entry và đếm `n_skipped_cost_gate`. `FEE_PCT`/`SLIPPAGE_PCT` gom về `config.py` (trước hard-code riêng trong Backtest Engine). Đã verify end-to-end: 5m lọc 296/296 lệnh (không lệnh nào đủ điều kiện), 1h còn 63/80 lệnh, win rate 33.3%. Bối cảnh phát hiện + số liệu đầy đủ: `research-technical-signal-edge.md`

## Phase 6 — Đưa chiến lược về mức có edge dương

Chốt lại từ kết quả chẩn đoán (`research-technical-signal-edge.md` mục 9). Thứ tự dưới đây là thứ tự phụ thuộc, không phải mức độ khó: chưa xong mục 1 thì mọi đo đạc ở các mục sau đều bị chi phí lấn át.

- [ ] **Chốt `TIMEFRAME` mới (1h)** — với 5m, Cost Gate chặn 100% tín hiệu nên bot không vào lệnh nào. 1h là khung duy nhất hiện có kỳ vọng gross dương (Long). Sau khi đổi phải calibrate lại `BUY_SCORE_THRESHOLD` trên khung mới, không dùng lại ngưỡng cũ
- [ ] **Hạ chi phí giao dịch: taker → maker** — gross tốt nhất hiện tại (+0.10%/lệnh trên 1h) vẫn chỉ bằng 1/3 chi phí khứ hồi 0.30%. Maker fee (~0.02%) đưa chi phí về ~0.14%, đủ lật dấu kỳ vọng. Đòn bẩy lớn hơn mọi cải thiện tín hiệu ở quy mô hiện tại. Cần đánh giá kèm rủi ro lệnh maker không khớp
- [ ] Thêm điều kiện pullback trước entry (vào khi giá hồi trong trend, không vào ngay lúc breakout xác nhận) — nhằm sửa đúng bản chất lagging của bộ tín hiệu, thay vì bù bằng tham số
- [ ] Đánh giá lại chiến lược Short trên khung mới — Short 1h có gross âm rõ (-0.13%), khác hẳn mức ≈0 ở 5m; chưa dùng được
- [ ] Test với đủ 6 lớp tín hiệu thật (cần tích luỹ Feature Store qua thời gian chạy `run.py`) thay vì chỉ Technical+Regime

## Phase 5 — AI Review, Obsidian Knowledge Base, RAG

- [x] Obsidian vault tạo tại `knowledge/` (mục 12b)
- [x] Cơ chế gọi LLM (Claude CLI local, OAuth) — `src/notify/ai_report.py`, đang dùng cho AI Report
- [x] AI Review Backtest — `src/ai_review.py` + `scripts/review_backtest.py`, tái dùng cơ chế gọi Claude CLI local (như `ai_report.py`) nhưng timeout dài hơn (120s, phân tích sâu hơn). Lưu review vào `knowledge/Backtests/<name>.md` (mục 13.6, Knowledge Memory). Đã test thật với kết quả backtest 30 ngày — Claude đọc đúng số liệu, sinh Lesson/Pattern/Anti Pattern/Recommendation có căn cứ (vd phát hiện exit "Volume giảm mạnh" và entry timing lệch pha là nguyên nhân lỗ chính), fallback rule-based khi CLI lỗi/timeout
