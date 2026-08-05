# Kiến trúc & Quyết định đã chốt

Chỉ ghi quyết định/kiến trúc đã chốt và **lý do** — không lặp lại cách implement, đọc trực tiếp code khi cần chi tiết (giá trị config cụ thể có thể đổi, xem `config.py`/`.env`/`config/paper.env` để lấy số mới nhất). Việc còn phải làm + rủi ro cần nhớ: xem `todo.md`.

## Mục tiêu

Mục tiêu tối thượng của dự án: **tối đa hóa lợi nhuận**. Continuous Learning, Knowledge Base, Strategy Package... là công cụ phục vụ mục tiêu này, không phải mục tiêu tự thân.

**Chiến lược đạt mục tiêu:** tìm cấu hình (threshold/filter/timeframe...) đạt **win rate > 50% trên mẫu lớn**, xác nhận đồng thời qua cả Paper Trading và Backtest — không chốt cấu hình chỉ dựa trên 1 trong 2 nguồn. Sau khi đạt ngưỡng này, tiếp tục dùng chính 2 nguồn đó để tăng win rate cao nhất có thể (không dừng ở mốc 50%).

**Success Criteria theo thứ tự ưu tiên:** hệ thống chạy ổn định 24/7 → dữ liệu chất lượng → backtest sát thực tế → Paper Trading có edge dương sau phí → win rate > 50% xác nhận qua cả 2 nguồn → tối đa hóa win rate/lợi nhuận liên tục.

**Vốn vận hành:** `config.ACCOUNT_EQUITY_USD` (`.env`).

## Kiến trúc tổng quan

```
Exchange → Collector → Market Database → Feature Engine → Rule Engine
→ Entry Model → Risk Engine → Execution → Trade Logger → Backtest
→ Knowledge Base → LLM Review → Continuous Learning
```

Không có Execution thật — hệ thống chỉ phát tín hiệu (Telegram + AI Report), không tự đặt lệnh trên sàn. "Execution" ở Paper Trading là mô phỏng qua state machine (`state_store.position_state`), không chạm sàn thật.

Không đổi sang framework có sẵn (NautilusTrader, Freqtrade) — pipeline custom (cron + SQLite + MLflow) đã chạy thật, chi phí migrate không đáng so với lợi ích. MLflow dùng cho Experiment Tracking + Model Registry + Champion/Challenger; tự viết Backtest Engine (không dùng vectorbt — xung đột version `pandas` với MLflow).

## Data Memory vs Knowledge Memory

Hai vai trò tách biệt, không dùng lẫn:
- **Data Memory** (SQLite, `data/state.db`) — sự thật định lượng: tick, OHLCV, order book, funding, feature store, trade history. Migrate sang PostgreSQL/ClickHouse chỉ khi nhiều symbol/sàn chạy đồng thời hoặc SQLite chậm rõ rệt khi query phân tích — chưa tới lúc đó.
- **Knowledge Memory** (Obsidian vault tại `knowledge/`) — kinh nghiệm: Strategy, Pattern, Anti Pattern, Lesson Learned, Experiment, Architecture, Design Decision, Model Card. Không lưu tick/order book/feature vector ở đây. Một note = một ý tưởng.

Artifact nhị phân (model `.pkl`, `metrics.json`) lưu filesystem/`data/`, không vào Obsidian — Obsidian chỉ lưu metadata + tri thức dạng note.

## Nguồn dữ liệu

Chỉ dùng Binance + OKX. OKX là sàn duy nhất tham gia Rule Engine/Decision Engine hiện tại (hệ thống chỉ chạy quyết định trên 1 sàn tại 1 thời điểm); Binance mới chỉ dùng để đối chiếu giá ở Feature Store — không phải vì vai trò thấp hơn OKX, mà là điểm sẽ mở rộng: kế hoạch bổ sung thêm sàn ngang hàng sau. WebSocket chỉ cho phần cần continuous stream thật (Trade Stream để tính CVD, Liquidation) — còn OHLCV/Order Book/Funding/OI dùng REST polling (đổi chậm, sai số poll gap không đáng kể, đơn giản hơn nhiều, không cần xử lý reconnect/backfill).

**Nguyên tắc "đúng sàn/thị trường đang trade":** giá dùng để tính feature/quyết định phải lấy từ đúng sàn + đúng loại thị trường (spot/perpetual) nơi lệnh thực sự khớp — không dùng giá nguồn khác dù gần giống, để tránh basis risk không phát hiện được. `EXCHANGE_ID` là 1 config dùng chung cho toàn bộ giá; đổi sàn/thị trường thực thi phải đổi luôn nguồn giá theo, không được lệch pha.

## Decision Engine — trọng số theo layer

`config.WEIGHTS` là nguồn duy nhất cho trọng số (không hard-code lại bảng này ở nơi khác). Trọng số phản ánh mức tin cậy của layer **tại chất lượng dữ liệu hiện có**, không phải mức quan trọng tuyệt đối của khái niệm — implementation của 1 layer nâng cấp thì trọng số cần xem lại theo, không giữ cố định mãi. Cơ sở khi chốt (xem `config.WEIGHTS` để lấy % hiện hành):

- **Technical (cao nhất)** — từng indicator riêng lẻ yếu, nhưng kết hợp nhiều indicator cho kết quả tốt hơn hẳn (RSI+MACD ~77% win rate trong backtest công khai); `technical.py` cộng điểm từ 8 tín hiệu độc lập.
- **Order Flow** — order flow liên tục cải thiện Sharpe rõ rệt theo nghiên cứu học thuật, nhưng trọng số này giả định chất lượng dữ liệu order flow liên tục — implementation ưu tiên CVD từ WS trade stream thật khi có sẵn và đủ mới, fallback REST snapshot khi chưa.
- **Derivatives** — funding rate + OI có giá trị dự báo, đặc biệt lúc **đổi chiều** (funding flip dấu thường xảy ra gần đáy/đỉnh cục bộ) — không chỉ mức tĩnh.
- **Cross-market** — tương quan BTC-Nasdaq/DXY đổi theo thời gian (BTC-DXY đã đảo từ nghịch sang thuận từ ~đầu 2026) — hệ số tương quan tách ra `.env` để cập nhật khi tương quan thị trường đổi, không phải sửa logic mỗi lần.
- **Sentiment** — Fear & Greed là tín hiệu contrarian chậm (cập nhật 1 lần/ngày) — trọng số thấp phù hợp vai trò xác nhận thêm, không phải tín hiệu dẫn dắt.
- **Regime (trọng số thấp nhất nhưng ảnh hưởng lớn hơn số % thể hiện)** — ngoài đóng góp vào tổng điểm, Regime còn có vai trò **gate cứng độc lập** trong `decide_entry` (`HIGH_VOLATILITY`/`UNKNOWN` chặn BUY tuyệt đối, bất kể tổng điểm) — không được hiểu nhầm là "ít quan trọng" chỉ vì % thấp.

**Đa khung thời gian (`MTF_TIMEFRAMES`):** hiện chỉ dùng làm hệ số đồng thuận (agreement_ratio) chiết giảm điểm `ema_trend`/`macd_cross` khi các khung không cùng hướng trend — chưa phải hệ thống confluence đa khung đầy đủ (tổ hợp nhiều khung thành 1 quyết định riêng, xem `todo.md`).

## Chấm điểm Technical — liên tục theo ATR (2026-08-05)

7/8 thành phần của `technical.score_from_indicators`/`score_short_from_indicators` chấm **liên tục** (tỷ lệ `[0,1]×weight` theo khoảng cách chuẩn hoá ATR hoặc vị trí trong vùng chỉ báo) — không phải on/off theo ngưỡng. Lý do: on/off khiến score gần như không đổi giữa các tick trong cùng nến (biến động giá nhỏ hiếm khi đủ để 1 điều kiện on/off đổi trạng thái), trong khi score đã được thiết kế "sống theo tick" để ra quyết định thật (mỗi poll trong cửa sổ theo dõi ghi đè giá nến cuối bằng giá tick, tính lại indicator). Ngoại lệ: `pattern` (nến engulfing) giữ on/off vì chỉ có nghĩa theo nến đã đóng, không có dạng liên tục hợp lý.

Các hằng số chuẩn hoá (bội số ATR, vùng RSI/ADX...) là ước lượng ban đầu dựa theo quy ước ATR đã dùng sẵn trong code — **chưa calibrate bằng backtest thật**, xem `todo.md`.

5 layer còn lại (order_flow/derivatives/cross_market/sentiment) + regime vẫn tính 1 lần đầu mỗi lần cron kích hoạt (không sống theo tick) — dữ liệu gốc của chúng không đổi theo giây, recompute mỗi poll vô nghĩa và tốn rate limit.

## Risk Engine

Module độc lập, có quyền từ chối mọi lệnh — đã có đủ: Position Sizing (ATR + % vốn), Daily Loss Limit, Max Drawdown (tự bật Kill Switch khi vượt — **không tự tắt lại**, cần người kiểm tra), Kill Switch thủ công, Cooldown sau SELL, Max Concurrent Position (kiến trúc hiện tại chỉ giữ 1 vị thế/symbol).

**Cost Gate** — quyền phủ quyết thứ hai, độc lập với các ngưỡng rủi ro trên: từ chối entry khi khoảng cách Take Profit nhỏ hơn `MIN_TP_COST_RATIO × chi phí khứ hồi`. Lý do chốt: trên khung ngắn (ATR nhỏ so với giá), TP sinh từ ATR có thể nhỏ hơn cả chi phí giao dịch — lệnh đi đúng hướng tới tận TP vẫn lỗ, không phải vấn đề tinh chỉnh tham số mà là ràng buộc thiếu. `FEE_PCT`/`SLIPPAGE_PCT` khai một lần ở `config.py`, dùng chung cho cả Cost Gate và Backtest Engine — khai riêng 2 nơi sẽ lệch âm thầm giữa mức lọc và mức thực trừ PnL.

**Chi phí giao dịch hiện tại:** Spot taker trên OKX. **Đã chọn hướng giảm chi phí: chuyển sang OKX Perpetual Swap** (maker) — chưa implement, xem `todo.md`.

## Cron + cửa sổ theo dõi liên tục

Cron kích hoạt theo chu kỳ x phút; mỗi lần kích hoạt mở cửa sổ theo dõi y phút, đọc tick giá thật từ WebSocket (`collector_ws.py`, chạy 24/7) mỗi `MONITOR_POLL_SECONDS`. Trong cửa sổ: giá (SL/TP), vị trí so vùng pullback, và toàn bộ layer Technical đều tính lại mỗi tick (mục "Chấm điểm Technical" trên); 5 layer còn lại + regime chỉ tính 1 lần đầu cửa sổ.

Thiết kế chấp nhận y ≥ x (overlap chủ đích) — dùng lại `state_store.run_lock()` để bỏ qua lần cron chồng, không coi là lỗi treo process.

## Paper Trading — instance đang chạy

Đang chạy 1 instance Paper Trading (`config/paper.env`, DB/log tách riêng khỏi `data/state.db` chính, không đụng nhau) — timeframe/threshold hiện tại đọc trực tiếp `config/paper.env` hoặc dashboard, không hard-code lại số ở đây (dễ lệch khi đổi qua dashboard). Vận hành qua cron `run_paper.sh` + 3 launchd service (`collector-ws-paper`, `dashboard`, `cloudflared`) — chi tiết quản lý: `README.md`.

Timeframe hiện tại được chọn qua nhiều vòng đo kinh tế học chi phí/ATR (khung ngắn có TP theo ATR nhỏ hơn cả chi phí giao dịch nên không thể có lãi kể cả khi chạy đúng kịch bản; khung dài hơn cải thiện rõ) — số liệu cụ thể của các vòng đo đó tính trên công thức Technical CŨ (on/off), đã hết giá trị tham chiếu sau khi đổi sang liên tục (mục trên). Xem `todo.md` nếu cần đo lại.

## Dashboard quản lý — truy cập từ xa

Bot (cron, SQLite state) chạy local, dashboard truy cập từ ngoài qua Cloudflare Tunnel (outbound, không mở port router) — không di dời hạ tầng lên cloud. Vì dashboard có quyền sửa config/cron/kill switch và có URL public (dù ngẫu nhiên), **bắt buộc có xác thực** (mật khẩu + session) — đã implement. Giới hạn đã biết: URL quick tunnel không cố định, đổi mỗi khi `cloudflared` restart; máy local phải bật thì dashboard mới truy cập được. Chi tiết vận hành: `README.md`.

## Logging — Event Sourcing + Snapshot

Log là tài sản quan trọng nhất của hệ thống, không chỉ lưu BUY/SELL/Profit. Thiết kế theo lifecycle `Signal → Risk Check → Entry → Monitoring → Exit → Evaluation → Knowledge`:
- **Raw Event** (`event_log`, không overwrite) — nguồn sự thật, mọi bước đều ghi (`MARKET_TICK`, `FEATURE_UPDATED`, `SIGNAL_GENERATED`, `RISK_REJECTED`, `ENTRY`, `EXIT`...).
- **Snapshot** — payload ENTRY/EXIT chính là Snapshot, đây là thứ AI đọc (không cần đọc toàn bộ Raw Event).
- **Trade Summary** — ghép cặp ENTRY/EXIT theo `trade_id`, dùng cho dashboard.
- **Feature Lineage** — lưu feature được tạo ra thế nào (nguồn, transformation, dùng bởi Strategy Package/experiment nào) — để biết tác động khi đổi/bỏ 1 feature.
- **Load dữ liệu cho LLM theo 4 tầng** (không bao giờ load toàn bộ trade cho LLM đọc): Trade Summary → Trade Snapshot → Related Trade (embedding/RAG) → Knowledge (đọc trước tiên).

Backtest dùng chung pipeline logging này (`Backtest → Trade → Summary → Evaluation → Lesson`) — không có format riêng.

## Strategy Package / Champion–Challenger / Continuous Learning

Chỉ có 1 Trading Framework, model chỉ là 1 Strategy Package (Config, Model Weight, Feature Version, Risk Profile, Exit Rule, Backtest/Paper Result, Model Card) — lưu manifest JSON ở `data/strategy_packages/`, Model Card Markdown ở `knowledge/Models/`. Luôn có Champion (đang chạy) + Challenger (đang thử) qua MLflow alias `@champion`/`@challenger` — Challenger phải vượt Champion đủ lâu trên backtest + paper trading mới được thay, không đổi code khi promote version mới.

Vòng lặp học liên tục: `Trade → Log → LLM Review → Knowledge → Improve Feature → Retrain → Deploy` — LLM không tự sửa model, chỉ phân tích/đề xuất; ML pipeline quyết định có retrain hay không.

## Giới hạn đã biết của Backtest Engine

Backtest Engine hiện tại chấm điểm/kiểm tra SL-TP theo **nến đóng** (không có dữ liệu tick lịch sử để mô phỏng "giữa nến") — không parity với Paper Trading hiện tại (đang sống theo tick thật mỗi poll). Nghĩa là kết quả backtest không dùng để xác nhận lại chính xác hành vi live hiện tại; muốn so sánh có ý nghĩa cần backtest kiểu "paper test" mô phỏng tick-recompute — xem `todo.md`.
