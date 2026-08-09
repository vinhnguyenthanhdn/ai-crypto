# Việc cần làm & Rủi ro

Mỗi task dùng stable ID để tài liệu/code không hỏng tham chiếu khi đổi thứ tự ưu tiên. File này chỉ giữ task chưa bắt đầu; task đang làm hoặc đang chặn experiment nằm trong `inprogress.md`; quyết định ổn định nằm trong `decisions.md`.

## P0 — Ràng buộc chặn mọi kế hoạch thực thi

### TODO-VENUE-LEGAL — Xác minh kênh thực thi còn dùng được

Quy định hạn chế giao dịch trên venue nước ngoài chưa được cấp phép đang ở dạng
dự thảo, canh mốc thị trường trong nước vận hành `2026-09-01`; xem
`execution-cost.md`. Phải xác minh trạng thái thực thi và khả năng truy cập
trước khi chọn venue và trước khi chạy lại backtest theo giả định venue mới.

Cần xác minh riêng: thuế 0,1% trên giá trị giao dịch gộp có áp cho phái sinh hay
chỉ giao ngay, và cơ chế thu với venue nước ngoài.

Chưa khảo sát: perp DEX và các CEX base tier khác. Tiêu chí đánh giá gồm phí base
tier không điều kiện, độ sâu BTC, khả năng truy cập, dữ liệu lịch sử cho backtest
và rủi ro đối tác.

### TODO-COST-RECALIBRATE — Hiệu chỉnh giả định chi phí

`FEE_PCT` và `SLIPPAGE_PCT` hiện cho round-trip 0,30%, cao gấp khoảng ba lần chi
phí perp thật và slippage cao hơn hai bậc so với đo được; xem `execution-cost.md`.

Sau khi hiệu chỉnh, chạy lại candidate đã có gross edge dương với contract đóng
băng nguyên vẹn, không search lại tham số. Không chạy lại các family có gross edge
bằng không; xem phần gross edge trong `backtest-results.md`.

Thuế phải là dòng chi phí riêng trong contract, không gộp vào fee.

### TODO-RUNTIME-BUGS — Sửa lỗi nhánh runtime

Các lỗi đã xác nhận nằm trong `code-audit.md`. Ưu tiên theo thứ tự:

- ngưỡng BUY bất khả thi về số học và thiếu guard ở `engine.py`/`short_engine.py`;
- deadlock cost gate với TP planner của profile S/R;
- lookahead trong `find_recent_swing_low/high`;
- backtest không truyền `pullback_ok`/`agreement_ratio` nên khác chiến lược live;
- `sharpe_ratio` thực chất là t-statistic, đang được ghi vào MLflow/model card/
  dashboard;
- cap position sizing bỏ qua `LEVERAGE` và báo cáo risk chưa bị cap;
- suy giảm âm thầm về 50,0 của bốn layer, kèm chạy lại `analyze_layer_signal.py`
  sau khi layer phát log cảnh báo thay vì trả hằng số.

### TODO-MULTIASSET-FROZEN — Kiểm định contract đóng băng trên đa tài sản

Chạy contract BTC trend đã đóng băng nguyên xi trên universe thanh khoản
point-in-time, không đổi một tham số nào. Mục đích kép: tạo đủ risk episode để
Paper test có throughput, và falsification — contract chỉ chạy được trên BTC mà
chết trên phần còn lại là dấu hiệu kết quả BTC không bền.

Vì contract không được search lại, phép thử này không thêm gánh nặng multiple
testing. Tương quan chéo cao trong crypto nghĩa là số tài sản không quy đổi
tuyến tính thành số phép cược độc lập; phải báo cáo episode độc lập hiệu dụng
thay vì đếm ticket.

## P0 — Độ đúng runtime và an toàn vị thế

### TODO-HEARTBEAT — Health-check phản ánh process và data thật

Đã bổ sung heartbeat trong tick loop, nhưng cần tách rõ process alive, collector age, market-data age, last successful cycle và last error. Market fetch thất bại không được ghi healthy.

### TODO-SWAP-PARITY — Hoàn thiện luồng Swap

Đã sửa collector dùng execution contract symbol/defaultType và dùng perpetual symbol cho liquidation/derivatives context. Cần test integration thật cho Spot và Swap, xác nhận tick key, funding/OI, margin/liquidation và reconnect.

Không bật Swap trước khi task này và `TODO-BACKTEST-PARITY` hoàn thành. Không mặc định maker fee khi chưa mô phỏng limit-order fill.

## P1 — Độ đúng backtest và dữ liệu

### TODO-REVALIDATE-BACKTESTS — Chạy lại kết quả cũ

Các kết quả 5m/15m/30m/1h, walk-forward 1h, Spot/Swap, Short và structural SL/TP là provisional vì được tạo trước khi xử lý toàn bộ parity/cost GAP.

Sau khi sửa engine: ghi kèm commit, config manifest, market type, fee/fill assumption, dataset range và engine version. Không dùng số cũ để promote cấu hình.

Post-entry same-sub-bar replay đã sửa và có regression. Còn phải quyết định
backtest continuous hay replay đúng scheduled monitoring windows; không gọi hai
coverage khác nhau là parity.

### TODO-SIX-LAYERS — Xác minh sáu layer thật

Derivatives từng giữ 50.0 kéo dài vì execution Spot dùng sai source symbol. Code đã đổi sang perpetual context symbol; cần xác nhận funding/OI thật sự thay đổi, timestamp đủ mới và lineage đúng.

Chỉ bắt đầu đánh giá tương quan layer-score với outcome khi có đủ trade đóng và không có layer bị neutral do lỗi data.

### TODO-ORDERFLOW-QUALITY — Theo dõi WS CVD

Đo gap, freshness, tỷ lệ fallback REST, phân phối CVD và tương quan với outcome trong ít nhất 2–4 tuần trước khi đổi weight Order Flow.

Order Flow hiện mới dùng snapshot bid/ask depth và trade-side CVD. Cần phân biệt
**book imbalance tĩnh** với **order-flow imbalance theo event** (add/cancel/fill),
lưu chuỗi nhiều mức giá cùng timestamp/sequence và đo khả năng dự báo return sau
chi phí ở các horizon entry 1/5/15 phút và outcome giữ lệnh dài hơn tới 24 giờ.
Không coi tương quan đồng thời với mid-price là bằng chứng tín hiệu có thể giao dịch.

### TODO-EVENT-PAGINATION — Lấy event mới nhất đúng cách

`get_events()` dùng `ORDER BY id ASC LIMIT`; dashboard sẽ kẹt ở 10.000 event đầu tiên. Cần query DESC cho latest, đảo lại khi cần chronological order và thêm index `(type,id)`/`(trade_id,id)`.

## P1 — Dashboard và vận hành

### TODO-DASHBOARD-VALIDATION — Validate config và bảo mật

Ràng buộc TIMEFRAME phải thuộc `MTF_TIMEFRAMES`, số phải hữu hạn/dương, giới hạn poll/window hợp lệ, escape giá trị khi ghi config. Bổ sung rate limit login, CSRF protection và session lifetime phù hợp nếu mở remote.

### TODO-AI-CACHE-ATOMIC — Chống gọi LLM trùng thật sự

AI report cache hiện là chuỗi get/generate/set best-effort, không atomic. Nếu cần chống race, dùng reservation/owner token có expiry; không mô tả TTL cache như distributed lock.

### TODO-CLOUDFLARE — Remote dashboard

Chưa cài. Chỉ triển khai khi được yêu cầu; ưu tiên named tunnel/domain cố định và hoàn tất hardening dashboard trước.

### TODO-DEPENDENCY-LOCK — Môi trường tái tạo được

`requirements.txt` chưa pin version và chưa khai Python version. Bổ sung lockfile/constraints và CI kiểm tra dependency.

### TODO-REGRESSION-SUITE — Test các invariant quan trọng

Hiện có compile check và 20 scoring tests. Phần regression đang chặn S/R experiment
nằm trong `inprogress.md`; task này giữ phần còn lại như health-check, dashboard,
Spot/Swap symbol và các invariant không thuộc experiment.

## P2 — Chiến lược và ML

### TODO-STAGGERED-PULLBACK-FORWARD — Xác nhận lợi nhuận portfolio ngoài mẫu

Research champion offline chín năm dùng slow pullback 4h, z=±2,0 với lookback
60, EMA180, tối đa năm tranche mỗi excursion, SL 5 ATR và exit z=±0,5. Candidate
đã qua portfolio-profit gate, cost stress 0,60%, frozen validator và
production-core trade parity. Trước khi Paper phải:

- đóng băng contract và quan sát một forward window chưa dùng để chọn rule;
- hoàn tất Swap/two-sided accounting parity vì frozen candidate gồm cả SHORT;
- xác nhận excursion state qua restart process live; historical accelerated
  replay đã persist state, concurrent exposure và portfolio drawdown qua DB;
- phân biệt dashboard/report `ticket count` với `independent excursion count`;
- không promote nếu forward net return/expectancy/PF fail hoặc drawdown vượt gate.

### TODO-SLOW-PULLBACK-LIVE-PARITY — Đưa champion 4h qua Paper gate

Candidate BTC slow trend-pullback đã qua production-core parity và accelerated
Paper SQLite lifecycle ba năm nhưng chưa được phép chạy live Paper. Còn phải nối
đúng OKX Swap market data/order semantics, xác nhận restart giữa excursion và
two-sided accounting trên process scheduler thật; đối chiếu với artifact
`data/backtests/staggered_paper_replay_3y.json`.

Sau parity phải nối position sizing/risk cap hiện có vào equity/exposure ledger
thật, rồi chạy forward observation và dashboard diagnostics. Không bật bằng
cách tái sử dụng score S/R hoặc threshold 70; đây là strategy contract độc lập.

### TODO-MTF-CONFLUENCE — Confluence đa khung

Tạm dừng cho tới khi có candidate nền sau `TODO-REVALIDATE-BACKTESTS`. Khi làm phải log feature/score theo từng timeframe và đo tổ hợp thật.

MTF hiện chỉ giảm phần Trend/MACD theo tỷ lệ đồng thuận EMA20/EMA50 trên các
khung, chưa có vai trò Context → Setup → Execution. Thử nghiệm 1h/4h làm context,
15m làm setup và 1m/5m làm execution, nhưng không gán cứng mỗi indicator cho một
timeframe và không cho mọi timeframe quyền biểu quyết ngang nhau.

Cần quy định rõ dùng nến đã đóng hay nến đang hình thành, align timestamp causal,
history length riêng theo timeframe và log feature từng khung. Daily/Weekly/
Monthly chỉ là candidate regime/risk filter cho chiến lược rất ngắn, không phải
entry trigger. Chỉ nhận cấu hình khi ablation/walk-forward sau chi phí chứng minh
tốt hơn baseline single-timeframe.

### TODO-SLOW-CONTEXT — Kiểm định dữ liệu chậm theo đúng horizon

On-chain, sentiment và cross-market/macro có cadence chậm hơn tín hiệu execution.
Đánh giá riêng từng nguồn theo vai trò regime/veto/risk filter; không cộng thẳng
vào entry score phút chỉ vì nguồn đó có ý nghĩa ở horizon giờ/ngày.

- On-chain: bắt đầu từ exchange/stablecoin flows có timestamp và độ trễ rõ; SOPR,
  MVRV, NVT, active addresses, whale/ETF flow chỉ thêm khi có source/version và
  ablation out-of-sample.
- Sentiment: Fear & Greed hiện là nguồn duy nhất và đang được dùng contrarian;
  cần kiểm tra publication lag, reverse causality và incremental edge. Reddit/X/
  news là candidate, không mặc định Reddit tốt hoặc news xấu.
- Macro: xây event calendar/gate cho CPI, FOMC và lao động; ưu tiên giảm size hoặc
  tránh entry quanh cửa sổ biến động/spread cao thay vì dùng dữ liệu ngày làm
  trigger BUY/SELL tức thời.

### TODO-DERIVATIVES-FEATURES — Mở rộng derivatives có kiểm định

Funding và OI hiện được lấy theo snapshot giữa các activation; logic funding
flip/OI direction là heuristic chưa calibrate. Cần lưu timestamp/history và đo
delta theo cửa sổ cố định. Basis, liquidation events/clusters, long-short ratio
và futures/spot order imbalance là candidate; chỉ thêm nguồn có lineage,
freshness và backtest/replay tương ứng.

### TODO-BASIS-GATE — Quyết định bật basis-risk gate

Gate đã implement và mặc định tắt. Xác minh lại phân phối divergence theo đúng Spot/Swap execution source trước khi bật.

### TODO-RAG — Related Trade qua embedding/RAG

Pending. Cần thiết kế index, embedding version, retrieval evaluation và giới hạn dữ liệu đưa cho LLM. Không mô tả RAG như thành phần hiện có trước khi task hoàn thành.

### TODO-ENTRY-MODEL — Entry Model serving

Runtime hiện không dùng Entry Model. Nếu triển khai, cần normalize feature, purge/embargo quanh temporal split, label sau chi phí và inference contract/version rõ ràng.

### TODO-CHALLENGER — Champion–Challenger

Chưa ưu tiên. Champion hiện là `rule_engine_v1`; tooling MLflow alias chưa đồng nghĩa model đang serving. Chỉ mở task khi Entry Model thật sự tham gia runtime.

## Rủi ro vận hành cần nhớ

- Kill Switch tự bật khi vượt drawdown và không tự tắt; cần người kiểm tra.
- API key phải read-only, không cấp trade/withdraw.
- Thiếu collector hoặc tick stale không được coi fallback snapshot cũ là monitoring hợp lệ.
- Không bật Swap hoặc promote cấu hình dựa trên kết quả backtest provisional.
