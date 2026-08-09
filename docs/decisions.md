# Kiến trúc & Quyết định đã chốt

File này chỉ giữ quyết định ổn định và lý do. Việc đang làm nằm ở `inprogress.md`, việc chưa bắt đầu nằm ở `todo.md`; cách vận hành nằm ở `README.md`; giá trị cấu hình cụ thể lấy từ runtime config, không chép lại vào tài liệu.

## Mục tiêu

Mục tiêu tối thượng là **tạo ra lợi nhuận cấp tài khoản sau toàn bộ
fee/slippage/funding**, đồng thời duy trì trung bình **5–10 lần vào lệnh độc lập
mỗi tuần**. Trong các candidate cùng đạt hai điều kiện này, tối đa hóa net return
với cùng vốn, position-sizing contract và giới hạn rủi ro. Cấu hình chỉ được công
nhận khi kết quả được xác nhận đồng thời qua backtest có hành vi giao dịch tương
đương Paper Trading và dữ liệu Paper Trading thật.

Không giới hạn token, sàn, market type, horizon, hướng LONG/SHORT, mô hình hay
phương pháp. Phạm vi được chọn theo khả năng tạo lợi nhuận có thể thực thi và
kiểm chứng; mọi candidate phải ghi rõ universe point-in-time, nguồn giá, loại
lệnh, funding, liquidity và cost contract tương ứng. Một kết quả tốt trên BTC
không có quyền ưu tiên hơn portfolio đa tài sản, market-neutral, basis, maker,
options hoặc phương pháp khác.

Net return/equity curve là objective; tần suất 5–10 lần vào lệnh/tuần, profit
factor, expectancy, max drawdown, tail loss và độ ổn định out-of-sample là gate
bắt buộc. Một lần vào lệnh là một risk episode độc lập; tranche, rebalance, leg
của cùng một spread và re-entry kỹ thuật trong cùng setup không được tính thành
nhiều lần. Gate tần suất áp dụng cho toàn portfolio và phải được báo cáo riêng
trên từng temporal split bằng số trung bình theo bảy ngày cùng phân phối rolling;
không dùng một giai đoạn burst để che các khoảng dài không có lệnh. Win rate và
ticket count chỉ là metric chẩn đoán. Không tăng lợi nhuận giả bằng leverage/risk
budget khác giữa các candidate.

Promotion gate phải stress round-trip cost tối thiểu bằng hai lần cost base của
backtest. Candidate phải còn net return dương, PF > 1 và đạt tần suất mục tiêu
trên mọi validation/test temporal split ở mức stress này; base-cost pass nhưng
stress-cost hoặc frequency gate fail không được thay champion. Một strategy tần
suất thấp có lợi nhuận vẫn được giữ làm portfolio component/benchmark, nhưng
không được gọi là nghiệm hoàn chỉnh của mục tiêu hiện tại.

## Kiến trúc runtime hiện tại

```text
Exchange → Collector → Feature Engine → Rule Engine → Risk Engine
→ Paper Execution State → Event/Trade Log → Report
```

Hệ thống chỉ phát tín hiệu và mô phỏng vị thế; không đặt lệnh thật trên sàn. Runtime hiện tại là Rule Engine thuần, chưa dùng Entry Model để ra quyết định.

Entry Model, RAG và Champion–Challenger model serving là hướng mở rộng, không phải thành phần đang chạy. Xem `TODO-ENTRY-MODEL`, `TODO-RAG` và `TODO-CHALLENGER`.

## Data Memory và Knowledge Memory

- **Data Memory**: SQLite/MLflow/filesystem lưu event, feature snapshot, position, trade, metrics và artifact định lượng.
- **Knowledge Memory**: `knowledge/` lưu Model Card, quyết định, bài học và tri thức con người đọc được.
- Không lưu tick hoặc feature vector hàng loạt vào Knowledge Memory.

OHLCV/order book thô hiện được fetch để tính feature nhưng chưa được lưu đầy đủ như một market database lịch sử. Nếu cần replay toàn bộ nguồn đầu vào phải triển khai riêng, không coi Feature Store hiện tại là bản sao dữ liệu thị trường đầy đủ.

## Nguồn dữ liệu và đúng thị trường

Runtime hiện tại kết nối OKX, nhưng research không khóa sàn. Candidate có thể
dùng Binance hoặc venue khác nếu data, phí, funding, contract specification và
khả năng triển khai được kiểm định trên chính venue đó. Không blend giá/order
flow giữa các sàn vào cùng score nếu strategy không định nghĩa rõ một spread
cross-venue có thể thực thi.

Giá dùng cho entry/exit phải đến từ đúng sàn và đúng market type thực thi. Spot dùng symbol Spot; Swap dùng unified contract symbol tương ứng. Derivatives có thể dùng perpetual contract như một nguồn context riêng cho chiến lược Spot, nhưng phải ghi rõ source symbol trong Feature Lineage.

Basis-risk gate Binance đã được implement nhưng mặc định tắt. Việc bật gate là thay đổi hành vi runtime và cần xác nhận riêng; xem `TODO-BASIS-GATE`.

## Decision Engine

`config.WEIGHTS` là SSOT của trọng số layer. Không chép bảng trọng số sang tài liệu.

Các layer hiện có:

- Technical.
- Order Flow.
- Derivatives.
- Cross-market.
- Sentiment.
- Market Regime.

Thiếu dữ liệu không được âm thầm diễn giải là “đã có sáu layer thật”. Chất lượng và độ mới của từng nguồn phải được đo trước khi dùng kết quả Paper Trading để hiệu chỉnh trọng số; xem `TODO-SIX-LAYERS`.

`MTF_TIMEFRAMES` hiện chỉ chiết giảm một phần Technical score theo mức đồng thuận xu hướng, chưa phải confluence đa khung đầy đủ.

## Technical score

Technical score gồm tám thành phần. Bảy thành phần chấm liên tục; candlestick pattern chấm on/off. Các hằng số chuẩn hóa hiện là giá trị khởi đầu, chưa được coi là đã calibrate.

`scripts/test_scoring.py` là regression test của công thức, nhưng việc test nhánh công thức không chứng minh tín hiệu có edge trên thị trường.

## Risk Engine

Risk Engine có các gate Position Sizing, Daily Loss, Drawdown/Kill Switch, Cooldown, Max Concurrent Positions, Cost Gate và kiểm tra liquidation cho Swap.

Các gate này chỉ được coi là hoàn thiện khi PnL/risk được tính theo quy mô vị thế và equity thật. Việc bổ sung equity ledger nằm ở `TODO-PNL-LEDGER`.

Cost Gate dùng cùng giả định fee/slippage với backtest xác nhận. Không mặc định maker fee nếu chưa có mô hình order type và fill tương ứng.

Giá trị fee/slippage/thuế và ràng buộc venue là SSOT của `execution-cost.md`; không chép số sang tài liệu khác. Mức tin cậy của từng nhánh engine tạo ra kết quả là SSOT của `code-audit.md`; kết quả chỉ được dùng để reject/promote khi engine sinh ra nó nằm trong nhánh đã kiểm định.

## Horizon và vòng đời lệnh

Không có thời gian giữ tối thiểu. SL/TP, risk gate hoặc tín hiệu exit hợp lệ có
thể đóng lệnh ngay sau entry. Thời gian giữ tối đa là 24 giờ; vị thế còn mở khi
hết hạn phải được đóng bằng timeout exit thay vì trở thành vị thế vô thời hạn.

Feature và label được đánh giá theo nhiều horizon từ phút tới 24 giờ. Không gọi
chiến lược là scalping 1–5 phút theo nghĩa bắt buộc mọi lệnh phải đóng trong năm
phút; thời gian giữ thực tế do điều kiện exit quyết định trong giới hạn trên.

## Position state

`position_state` lưu nhiều vị thế theo `trade_id`; mặc định runtime vẫn có thể giới hạn một vị thế. Nhiều vị thế trên cùng symbol phải tính tổng risk và tổng capital exposure, không chỉ giới hạn số row.

## Scheduler và monitoring

Chọn launchd làm cơ chế vận hành trên macOS; cron không thuộc luồng chính.

Rule Engine Paper chạy theo scheduled monitoring window. launchd `KeepAlive`
chỉ giữ một scheduler Python nhẹ; scheduler neo mốc **start-to-start** theo
`ACTIVATION_INTERVAL_MINUTES`, chỉ giữ owner run lock và poll giá trong
`MONITOR_WINDOW_MINUTES`, rồi ngủ tới mốc kế tiếp. `MONITOR_POLL_SECONDS` là
cadence bên trong cửa sổ. Slot bị trễ quá interval được bỏ qua, không catch-up
dồn. Đây là quyết định thay cho việc dùng `StartInterval`, vì quan sát thực tế
cho thấy duration của window bị cộng vào khoảng cách giữa hai activation.

`config/paper.env` là runtime config SSOT dùng chung cho Rule Engine, collector
và dashboard; thay đổi có hiệu lực sau khi restart process.

## Logging và Feature Lineage

Live/Paper Trading dùng `event_log`, `feature_snapshot` và `trade_id` để truy vết lifecycle.

Feature snapshot phải lưu:

- Nguồn exchange/market/symbol/timeframe.
- Phiên bản transformation.
- Strategy package sử dụng feature.

Feature Lineage đã được bổ sung và kiểm thử schema round-trip; snapshot/manifest
ghi source, transformation, strategy package, profile, engine và execution policy.

Related Trade qua embedding/RAG chưa implement; xem `TODO-RAG`.

## Backtest

Backtest không dùng chung database/event log với live. Đây là chủ ý để dữ liệu thử nghiệm không trộn với runtime log.

Tuy nhiên, backtest dùng để xác nhận chiến lược phải có cùng trade behavior với Paper Trading: entry gate, exit/timeout theo thời gian thực, cost, fill assumption, position sizing và accounting. Nếu chưa đạt parity thì kết quả chỉ mang tính chẩn đoán, không dùng để promote cấu hình.

`engine.py` là engine bar-close dùng quét nhanh. `paper_engine.py` là engine hướng tới parity ở mức tick-proxy lịch sử, nhưng vẫn còn GAP trong `TODO-BACKTEST-PARITY`.

Staggered-pullback có accelerated Paper mode riêng trong cùng `paper_engine`:
market clock được thay bằng clock lịch sử, còn position state, feature snapshot,
signal/event log, equity ledger, sizing và accounting vẫn đi qua SQLite lifecycle
production. Replay luôn dùng DB riêng và phải từ chối DB runtime thật. Đây là
implementation-parity test; không thay thế forward market observation.

Các kết quả timeframe, Spot/Swap, Short và structural SL/TP đã đo trước khi đóng các GAP parity được xem là **provisional** và phải chạy lại; xem `TODO-REVALIDATE-BACKTESTS`.

## Strategy package và Champion

Champion hiện tại là strategy rule-based `rule_engine_v1`. Runtime chưa load model từ MLflow.

`composite_btc_trend_funding_crowding_v1` là Paper Challenger đã qua historical,
cost-stress, production parity và accelerated SQLite lifecycle. Allocation 50%
BTC Spot trend + 50% funding-crowding; live execution luôn OFF. Không đổi runtime
Champion alias trước khi fresh forward Paper qua gate. Contract không dùng news:
direction lấy từ nến 4h/1D đã đóng, entry từ breakout 1h, SL thích nghi theo độ
mạnh trend, còn funding z-score chỉ làm crowding veto. Fresh forward chạy bằng
LaunchAgent no-order riêng mỗi giờ; promotion cần tối thiểu 30 closed trade và
30 independent risk episode cùng health/parity hợp lệ.
Forward promotion không dựa riêng vào sample count: phải có ít nhất 28 ngày và
90% hourly coverage, tần suất trung bình 5–10 episode/tuần, composite net dương
và PF >1 ở base lẫn doubled-cost stress, DD không quá 20%. BTC daily và funding
hourly giữ state/equity độc lập; verifier reconcile đúng allocation 50/50 và
fail-closed nếu artifact parity/lifecycle, status, DB hoặc live-off invariant lệch.
Rolling 7 ngày phải có median 5–10 episode, ít nhất 50% cửa sổ nằm trong dải và
không quá 10% cửa sổ bằng zero; average đạt nhưng distribution fail thì không
được promote.
Mỗi funding signal hour phải có đúng một input-snapshot event và đủ feature
lineage cho toàn bộ universe; giờ no-trade thiếu input không được tính là coverage.

Code MLflow, Entry Model và alias Champion/Challenger là tooling chuẩn bị, không
được mô tả là deployment đang chạy; trạng thái Paper Challenger không đồng nghĩa
với deployment live.

## Dashboard và remote access

Dashboard hiện phục vụ local. Cloudflare Tunnel chưa thuộc runtime đang cài; xem `TODO-CLOUDFLARE`.

Dashboard không được quản lý cron vì cron đã bị loại khỏi kiến trúc. Việc sửa config phải tác động tới effective runtime config và có cơ chế restart/reload rõ ràng; xem `TODO-DASHBOARD-LAUNCHD` và `TODO-CONFIG-SSOT`.
