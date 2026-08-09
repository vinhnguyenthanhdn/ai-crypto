# Thử nghiệm đang thực hiện

File này là SSOT cho thử nghiệm và các workstream đang chặn nó. Việc chưa bắt
đầu nằm trong `todo.md`; quyết định ổn định nằm trong `decisions.md`.

## GOAL-PROFITABLE-CRYPTO — Tìm cách giao dịch crypto có lợi nhuận

```yaml
status: ACTIVE_NO_FULL_CHAMPION
primary_objective: portfolio_net_return_after_cost
required_entry_frequency: 5_to_10_independent_risk_episodes_per_week
scope: any_crypto_asset_market_horizon_or_method
selection: train_only
required_gates: [frequency, validation, test, cost_stress, production_parity, paper_lifecycle]
diagnostic_only: [win_rate, raw_ticket_count]
current_round: fresh_forward_paper_composite
profitability_benchmark: btc_spot_vol_scaled_trend
```

Đây là goal duy nhất đang tối ưu. Research chạy theo vòng ngắn: lấy hypothesis
từ nguồn có cơ sở, freeze contract, chọn trên train, mở validation/test, ghi kết
quả rồi reject hoặc chuyển sang parity. Không giới hạn token/phương pháp; lợi
nhuận sau chi phí và trung bình 5–10 risk episode độc lập/tuần là hai gate đồng
thời. Không dùng tranche, leg hoặc re-entry của cùng setup để làm đẹp tần suất;
không dùng holdout để sửa candidate vừa fail.

BTC Spot long/cash volatility-scaled trend là profitability benchmark và
portfolio component đã qua historical parity, nhưng tần suất thấp nên không phải
nghiệm hoàn chỉnh của goal hiện tại. Runtime execution mặc định OFF. Vòng hiện
tại ưu tiên tìm alpha intraday có đủ tần suất; maker L2 là nhánh kế tiếp nếu
directional intraday không qua cost/temporal gate.

### Kế hoạch giải bế tắc theo bằng chứng bên ngoài

Không tiếp tục tuning indicator OHLCV 5m, Donchian, z-score, taker-flow,
funding/OI hoặc model đã fail temporal/cost gate. Hai hypothesis chưa bị kết quả
hiện có bác bỏ được kiểm định theo thứ tự:

1. **Session-conditioned intraday momentum/reversal.** Kiểm định tín hiệu từ
   phần đầu của các phiên có volume hoặc volatility cao để giao dịch phần sau,
   trước hết trên BTC/ETH rồi mở sang universe thanh khoản point-in-time nếu cần
   coverage. Contract phải dùng session cố định theo UTC, signal từ dữ liệu đã
   đóng, next-event fill, tổng cost thật và tối đa một risk episode cho mỗi
   asset/session. Mục tiêu coverage cấp portfolio là xấp xỉ một entry/ngày,
   không search giờ phiên hoặc ngưỡng trên validation/test. Paper *Bitcoin
   intraday time-series momentum* ghi nhận phần nửa giờ đầu của phiên volume/
   volatility cao có khả năng dự báo phần nửa giờ cuối; đây chỉ là hypothesis
   đăng ký trước, không phải bằng chứng strategy của repo sẽ có lãi.
2. **Event-driven maker với L2 + trades.** Nếu nhánh trên fail, mở rộng framework
   BBO hiện có sang order-book event thực: queue ahead, add/cancel/trade, partial
   fill, post-only reject/cancel, inventory, latency và markout sau fill. Adverse
   price move xuyên limit được tính là fill bất lợi; non-adverse fill phải dựa
   trên volume thực sự tiêu thụ queue, không gán xác suất tùy ý. Dùng dữ liệu
   tick trades và L2 chính thức của đúng venue thực thi, chia continuous temporal
   blocks và stress maker fee/rebate về phía bất lợi.

Tần suất được đạt ở cấp portfolio bằng nhiều risk episode độc lập giữa asset,
session hoặc strategy sleeve; correlation và concurrent exposure phải được cap
ở equity ledger. BTC daily trend chỉ được ghép như một sleeve nếu incremental
portfolio test còn đạt profit/drawdown/cost gate; không lấy số rebalance của nó
làm entry count.

Nguồn kiểm chứng, truy cập ngày 2026-08-08:

- Shen, Urquhart & Wang, *Bitcoin intraday time-series momentum*:
  https://doi.org/10.1111/fire.12290
- OKX Historical Market Data: tick trades từ 09/2021 và L2 từ 03/2023:
  https://www.okx.com/historical-data
- OKX order-book contract: snapshot/incremental channel, cadence và sequence:
  https://www.okx.com/docs-v5/trick_en/
- Lalor & Swishchuk, *Market Simulation under Adverse Selection*:
  https://arxiv.org/abs/2409.12721
- Binance Public Data làm nguồn đối chiếu trade-side/aggTrades có checksum:
  https://github.com/binance/binance-public-data

### Kết quả strategy trend dài hạn + sentiment + adaptive SL

Package research `trend_sentiment_adaptive_risk_v1` dùng daily và 4h đã đóng để
chọn LONG/SHORT, 30m/1h làm execution, Fear & Greed align theo publication
timestamp và SL tăng từ base tới maximum theo trend strength. Risk USD giữ cố
định nên SL xa tự giảm capital fraction; TP tăng theo cùng R-multiple. News có
contract neutral khi chưa có archive causal, không backfill tin hiện tại vào
lịch sử.

Pullback/reclaim grid 128 contract bị reject. Nhánh 30m đạt tần suất nhưng mọi
contract từ 5 entry/tuần trở lên đều lỗ; tốt nhất trong nhóm này vẫn
`-25,83%` train. Contract tốt nhất theo profit dùng 1h, EMA20, SL 1,5→4 ATR,
R:R 2 và hold 48h: train `+8,58%`, PF `1,049`, drawdown `13,68%`, nhưng chỉ
`2,94` entry/tuần. Khi mở holdout đã đăng ký trước, validation/test lần lượt
`-8,59% / -8,96%`; stress cost 0,30% làm cả train âm `-17,82%`.

Session-momentum grid 72 contract cũng bị reject ngay train. Frequency chỉ
`0,79–3,16` entry/tuần; một contract dương `+0,31%` nhưng PF `1,011` và chỉ
`0,94` entry/tuần. Không mở validation/test và không tune tiếp family này.

Kết luận: context trend/sentiment và adaptive stop đã được implement causal,
nhưng không tạo alpha taker đủ bền hoặc đủ tần suất trên BTC+ETH. Package không
thay champion và runtime execution vẫn OFF. Vòng kế tiếp giữ context này làm
direction/inventory bias, còn entry/fill chuyển sang historical L2 maker để
giảm cost và đo adverse selection thật.

### Kết quả L2 maker và trend-breakout continuation

OKX official archive ngày 2024-01-15 được dùng làm execution probe, không làm
strategy holdout: BTC-USDT-SWAP L2 400 levels có snapshot rồi delta khoảng
10 ms; public trades được merge theo exchange timestamp. SHA-256 L2 là
`d347eaa933f80b6ae7d3d581fa5cd247a939c69ef3e7aa135350339d59afc68f`, trade
là `1cb1e8c76a04122d3f2b3d4833c9dc4e684d9a44ce51176e10eafb08aee5e822`.
Simulator đặt order sau toàn bộ displayed queue, cancellation không làm queue
tiến lên, chỉ aggressor trade tiêu thụ queue và trade xuyên limit buộc fill;
có partial fill và markout.

Ba signal pullback 1h causal cùng là SELL trong downtrend. Touch maker fill 2/3
nhưng mean markout `-5,30 / -9,69 / -28,93 / -25,33 bps` tại
1m/5m/15m/60m. Passive offset 10 bps vẫn fill 2/3 và cải thiện thành
`+3,76 / -9,22 / -19,51 / -15,06 bps`; queue stress 2x không đổi hai fill vì
giá trade xuyên level. Đây là adverse selection, không phải execution edge;
không tải thêm L2 chỉ để cứu pullback family.

Breakout-continuation giữ daily+4h trend, Fear & Greed causal, adaptive SL và
fixed account risk; trigger chỉ dùng high/low các nến đã đóng. Vòng v1 có 96
contract: 74 đạt 5-10 entry/tuần, bốn dương ở cost 0,07% nhưng không contract
nào dương ở stress 0,14%. Vòng v2 thêm trend-strength và breakout buffer; best
train đạt 5,34 entry/tuần, `+18,37%`, PF `1,057`, DD `19,85%`, nhưng stress
`-7,99%`, PF `0,982`.

Vòng v3 chỉ refine exit/risk trên train và có hai survivor train. Contract được
chọn: 30m breakout lookback 4, buffer 0,1 ATR, daily+4h strength tối thiểu 1,
SL 2→4 ATR, R:R 4, hold 24h, cooldown 3h, sentiment policy `none`. Kết quả:

| Split | Entry/tuần | Base net / PF / DD | Stress net / PF / DD |
|---|---:|---:|---:|
| Train | 5,14 | +43,21% / 1,118 / 14,17% | +12,18% / 1,042 / 16,59% |
| Validation | 9,28 | +19,21% / 1,170 / 10,60% | +7,59% / 1,072 / 11,92% |
| Test | 9,10 | -9,40% / 0,964 / 22,08% | -24,52% / 0,890 / 31,97% |

Test không tham gia selection nên thất bại này đóng candidate. Diagnostic test:
LONG `-8,15%`, SHORT `-1,36%`; phần 2024/2025 lần lượt `-10,93%/-10,36%`,
2026 `+13,47%`. Không retune cùng test. Artifacts:
`trend_breakout_adaptive_risk.json`, `trend_breakout_adaptive_risk_v2.json`,
`trend_breakout_adaptive_risk_v3.json` và
`okx_l2_maker_probe_2024-01-15_grid.json` trong `data/backtests/`.

Vòng hợp lệ tiếp theo là portfolio trend trên universe thanh khoản
point-in-time, để diversification tạo 5-10 risk episode độc lập/tuần thay vì
ép BTC/ETH giao dịch trong chop. Asset membership/liquidity phải causal; chọn
trên block cũ và cần forward block hoặc Paper chưa dùng làm bằng chứng mới.

### Kết quả portfolio trend universe point-in-time

Artifact `data/backtests/multiasset_trend_breakout_portfolio_5y.json` dùng 15
USDT perpetual có archive Binance Public Data 1h + funding trên hai block
2021-2023 và 2023-2026. Membership mỗi giờ chỉ lấy top 8/12 theo trailing
30-day dollar volume đã đóng; funding settlement được tính theo phía LONG/SHORT.
Risk 0,25%/episode, capital cap 25%/position và tối đa 1/2 vị thế đồng thời.

Grid 16 contract chọn trên 2021-08-07–2023-08-07 có năm survivor. Candidate
train-selected dùng breakout lookback 4h, top-8 liquidity, tối đa một vị thế và
sentiment `none`:

| Split | Entry/tuần | Base net / PF / DD | Stress net / PF / DD |
|---|---:|---:|---:|
| Train | 5,48 | +6,02% / 1,124 / 3,59% | +3,44% / 1,071 / 3,87% |
| Validation | 6,41 | -2,93% / 0,901 / 6,63% | -4,49% / 0,851 / 7,47% |
| Test | 6,13 | -0,008% / 1,003 / 4,80% | -3,36% / 0,944 / 5,33% |

Portfolio đạt frequency và giảm drawdown nhưng validation bác bỏ edge. Không
đổi universe/side/filter sau khi xem validation. Các block lịch sử liên quan đã
được dùng cho research; bằng chứng hợp lệ tiếp theo phải là family độc lập có
contract mới hoặc forward Paper sau 2026-08-08. Goal vẫn
`ACTIVE_NO_FULL_CHAMPION`, runtime execution OFF.

### PAPER CHALLENGER — Composite BTC trend + funding crowding

Tín hiệu funding crowding dùng breakout 1h cùng daily+4h trend, nhưng veto LONG
khi funding z-score dương và veto SHORT khi funding z-score âm. Funding z-score
dùng 21 settlement trước; universe mỗi giờ là top-12 theo trailing 30-day dollar
volume. Tối đa hai vị thế, risk 0,25%/position, SL nới 2→4 ATR theo trend,
R:R 4 và hold tối đa 24h. Hai vị thế cùng signal timestamp được tính là một
risk episode vì cùng regime. Contract hiện tại **không dùng news hoặc sentiment
feed**; chỉ dùng nến 1h/4h/1D đã đóng, ATR, dollar-volume causal và funding
settlement công khai. Funding ở đây là positioning/crowding veto đơn giản,
không phải tín hiệu tin tức.

Composite giữ 50% equity trong BTC Spot trend incumbent và 50% trong funding
crowding sleeve; hai sleeve compound độc lập, không giả định daily rebalance.
Allocation là tỷ trọng incumbent lớn nhất trong grid 25/50/75/90% còn qua train
base/stress PF, net và DD gate.

| Split | Episode/tuần | Base net / PF / DD | Stress net / PF / DD |
|---|---:|---:|---:|
| Train | 8,14 | +7,59% / 1,075 / 14,54% | +3,82% / 1,043 / 16,02% |
| Validation | 8,82 | +41,15% / 1,507 / 10,04% | +38,10% / 1,466 / 10,75% |
| Test | 9,18 | +5,90% / 1,057 / 12,44% | +0,66% / 1,016 / 14,68% |

Phân phối trailing 7 ngày có median `9/9/9` episode trên train/validation/test;
tỷ lệ cửa sổ nằm trong 5–10 là `70,68% / 66,94% / 68,63%`. P95 lần lượt
`12/12/12`, nên average đạt mục tiêu nhưng vẫn theo dõi burst và gap riêng trong
forward thay vì chỉ dựa vào một con số trung bình.

Production core khớp reference trade-for-trade `963/963`, `515/515`,
`1.079/1.079`, zero mismatch. Accelerated Paper SQLite cho funding sleeve có
2.557 ENTRY/EXIT/ledger, 2.557 feature/signal, zero open/orphan/halted day;
equity reference và Paper cùng `$500 → $574,57757358`. BTC sleeve đã có Paper
lifecycle parity riêng.

Package `composite_btc_trend_funding_crowding_v1` được đăng ký trạng thái
`PAPER_CHALLENGER`, live execution OFF. Historical gate đạt nhưng các holdout đã
được quan sát trong quá trình discovery; fresh forward Paper là promotion gate
còn lại, không gọi đây là live Champion. Composite forward observer no-order
được cài bằng launchd label `com.ai-crypto.funding-crowding-forward`, chạy mỗi
giờ ở phút 05 từ runtime cô lập trong
`~/Library/Application Support/ai-crypto-funding-forward` do macOS không cho
background LaunchAgent đọc `Documents`. Cả BTC daily trend và funding hourly
sleeve có DB/state riêng rồi được reconcile thành equity 50/50; lần khởi tạo BTC
giữ CASH để không giả fill tại daily open đã trôi qua.

Lần quan sát đầu `2026-08-08` có collection health pass, exit code 0, stderr
rỗng; 1 giờ funding, 1 daily observation, 0 closed trade và 0 episode. Chạy lặp
cùng signal hour/day không nhân đôi observation. Promotion verifier fail-closed
yêu cầu tối thiểu 28 ngày, coverage theo giờ ≥90%, 30 closed fast trade, 30
independent episode, trung bình 5–10 episode/tuần, composite base và stress đều
net dương/PF >1, DD ≤20%, rolling 7 ngày có median 5–10, ≥50% cửa sổ trong dải,
≤10% cửa sổ zero và funding sleeve flat tại snapshot. Hiện
`promotion_ready=false`; không suy diễn pass/fail lợi nhuận khi chưa có lệnh.
Mỗi observed hour lưu 15 feature rows cùng một common signal timestamp; verifier
từ chối coverage nếu thiếu input lineage dù observation counter vẫn tăng.

Recent 30-day venue diagnostic trên 30 ngày UTC đã đóng đến `2026-08-08` cho
kết quả base/stress composite Binance `-3,06%/-3,59%`, OKX
`-3,93%/-4,47%`. Binance đạt 9,80 episode/tuần; OKX 10,27. Cả BTC và funding
sleeve đều âm, production parity 45/45 closed trade trên mỗi venue. Kết quả này
cảnh báo current regime nhưng là retrospective diagnostic, không thay thế fresh
forward và không được dùng để retune contract.

Vòng 1 long-only bị reject: candidate train chọn lookback 30 ngày/rebalance ba
ngày đạt `+1.573,36%` nhưng max drawdown `52,28%`, validation `-3,76%` và test
`-71,40%`; đây là market beta, không phải edge ổn định. Vòng 2 khóa dollar-neutral
và train drawdown tối đa 15%. Candidate train-only dùng lookback 14 ngày,
volatility-adjusted rank, top-8 theo trailing 30d dollar volume, LONG top-3 và
SHORT bottom-3, rebalance mỗi ngày. Base train / validation / test đạt
`+110,22% / +7,94% / +16,70%`, PF `1,395 / 1,139 / 1,177`, max drawdown
`9,96% / 8,63% / 12,58%`. Stress one-way cost 0,15% còn
`+80,06% / +2,73% / +5,24%`, PF đều trên 1. Artifact:
`data/backtests/cross_sectional_momentum_market_neutral_3y.json`.

Funding từng leg tại settlement timestamp đã được cộng. Sau khi sửa join funding
lệch vài mili-giây về đúng settlement-hour, base train / validation / test là
`+114,09% / +8,27% / +16,39%`; stress là
`+83,37% / +3,05% / +4,96%`, PF đều trên 1.

Candidate bị reject trên block ngoài mẫu 2021-08-07–2023-08-07 với contract đã
freeze, không search lại tham số. Base đạt `+20,86%`, PF `1,084` nhưng max
drawdown `27,46%`; stress one-way cost 0,15% thành `-1,20%`, PF `1,015`, max
drawdown `32,10%`, chỉ 4/8 quý dương. Fixed survivor universe cũng chưa loại
được survivorship bias. Artifact:
`data/backtests/cross_sectional_momentum_external_2021_2023.json`. Không promote
và không dùng block này để cứu candidate; vòng kế tiếp chuyển sang funding/basis
delta-neutral nhằm giảm market beta và turnover.

Funding/basis delta-neutral đã bị reject. Grid 81 contract dùng 50% vốn mua Spot
và 50% làm collateral cho SHORT Perpetual 1x, signal funding trailing chỉ lấy từ
quá khứ, fill next-open, tính funding và basis PnL của cả hai leg. Candidate
train-only dùng lookback 30 ngày, rebalance bảy ngày, top-3 và funding annualized
tối thiểu 10%: train `+5,39%`, PF `6,67`, max drawdown `0,21%`; validation không
có vị thế, test `-0,13%`, stress test `-0,33%`. Regime carry đã biến mất; không
hạ threshold sau khi xem holdout. Artifact: `data/backtests/spot_perp_carry_3y.json`.

Time-series trend đa Swap cũng bị reject ngay train. Grid 72 contract gồm
lookback 14/30/60/90 ngày, rebalance 1/3/7 ngày, top liquidity 8/12/15,
long-short hoặc long-flat, inverse-vol sizing, exact funding và cost; có `0`
candidate đạt đồng thời net dương, PF > 1,1 và max drawdown <=20%. Artifact:
`data/backtests/time_series_trend_3y.json`. Vòng tiếp theo chuyển sang
cointegrated-spread statistical arbitrage; pair/hedge ratio chỉ được chọn trên
train và phải giữ nguyên khi mở validation/test.

Cointegrated-spread statistical arbitrage bị reject ngay train. Formation sáu
tháng tìm được 10 pair có Engle–Granger ADF t-stat dưới -2,86; hedge ratio chỉ
fit trên formation. Grid 480 contract dùng rolling z-score 7/14/30 ngày, stop
divergence, max holding 7/14 ngày, next-open execution, exact funding và yêu cầu
tối thiểu 12 round-trip trong 12 tháng train. Có `0` contract đạt đồng thời net
dương, PF >1,1 và max drawdown <=15%, nên không mở holdout. Artifact:
`data/backtests/cointegrated_spreads_3y.json`. Vòng hiện tại đảo thiết kế factor
momentum về đúng chiều thời gian 2021–2026, chọn trên block cũ và chỉ đánh giá
forward trên block mới; các block đã từng xuất hiện ở nghiên cứu khác không được
gọi là untouched holdout.

Cross-sectional momentum forward 2021–2026 bị reject. Candidate chọn trên
2021–2023 đạt train `+116,28%`, validation base `+5,69%`, nhưng test 2024–2026
`-4,97%`; stress validation/test `-13,33% / -35,76%`. Artifact:
`data/backtests/cross_sectional_forward_5y.json`.

BTC Spot long/cash volatility-scaled trend đã qua research gate trên 9 năm.
Contract train-only: signal `close > SMA50 × 1,01`, signal ở daily close và fill
daily open kế tiếp; exposure bằng target volatility 30%/năm chia realized
volatility 30 ngày, cap 1x, không leverage, không SHORT, không funding. Phí
one-way base 0,12%, stress 0,24%. Train sáu năm / validation một năm / test gần
hai năm đạt base `+563,25% / +77,38% / +14,91%`, PF
`1,391 / 1,591 / 1,101`, max drawdown `33,00% / 13,46% / 21,47%`. Stress còn
`+516,26% / +74,01% / +9,66%`, PF test `1,073`, max drawdown test `23,24%`.
Artifact: `data/backtests/btc_spot_trend_vol_scaled_9y.json`.

Frozen validator khớp tuyệt đối net return/PF/drawdown của cả ba split ở base và
stress; 5/6 cấu hình lân cận cũng dương và PF >1 trên mọi split. Production core
unit test pass 3/3. Accelerated Paper chạy đủ 3.277 ngày qua SQLite với simulated
clock: reference và Paper cùng đưa equity `$500 → $6.759,67` (`+1.251,93%`), max
drawdown `33,00%`; 77 ENTRY, 1.402 REBALANCE, 77 EXIT, 3.277 feature/signal, 77
ledger, không mismatch, position mở hoặc orphan ledger. Artifact:
`data/backtests/btc_spot_trend_frozen_validation_9y.json`,
`data/backtests/btc_spot_trend_paper_9y.json` và DB cùng tên `.db`.

Benchmark buy-and-hold cùng Spot/cost đạt train / validation / test
`+583,56% / +99,80% / +9,57%`, nhưng max drawdown
`83,20% / 26,07% / 52,97%`. Champion hy sinh một phần upside ở train/validation
để giảm drawdown, đồng thời tốt hơn benchmark ở test (`+14,91%`, drawdown
`21,47%`). Đây là Research Champion đã qua historical implementation parity,
không phải cam kết lợi nhuận tương lai; execution vẫn OFF cho tới khi forward
Paper xác nhận data health và order-resize behavior trên venue triển khai.

Diagnostic 30 ngày cuối dataset (`2026-07-08`–`2026-08-07`) không tham gia
selection: base net `-4,32%`, PF `0,554`, max drawdown `7,21%`; stress net
`-5,01%`. Zero-cost vẫn `-3,62%`, còn base execution cost kéo giảm thêm khoảng
0,70 điểm phần trăm. Strategy active 12/30 ngày, vào lại ba lần và bị whipsaw
quanh SMA50; buy-and-hold cùng kỳ base `+1,81%`, max drawdown `5,61%`. Phép đo
mark-to-market và giả đóng vị thế tại cuối cửa sổ để so sánh đồng nhất; signal
cuối `2026-08-06` vẫn LONG với target exposure 1x. Kết quả ngắn hạn này không
được dùng để sửa frozen contract và củng cố quyết định giữ runtime OFF trong
forward observation.

### Kết quả phạm vi Fast Champion cũ

Hai family OHLCV đầu đã bị reject. Trend-aligned z-score trên 15m/30m/1h có
`0/1.296` cấu hình đồng thời đạt lợi nhuận và tối thiểu 2 excursion/tuần, kể cả
với Swap cost. Donchian breakout có candidate train-only 1h đạt khoảng
2 excursion/tuần và train `+21,74%` tại cost 0,14%, nhưng validation chỉ
`+1,72%`, test `-7,38%` (PF 0,495); stress 0,30% âm validation/test. Kết luận:
family bị regime overfit và chưa phải champion. Bước đang chạy là confirmation
causal bằng OI/taker/funding trên dữ liệu derivatives; không hạ frequency/profit
gate và không chọn filter bằng holdout.

Taker-flow confirmation trên toàn bộ cache 9 năm cũng bị reject. Candidate
train-only 1h Donchian 40, taker imbalance mạnh cùng hướng đạt 2,58 excursion/
tuần, train `+27,81%` và validation `+5,25%`, nhưng test `-4,50%` (PF 0,826);
stress cost 0,30% âm validation/test. Taker confirmation không giải quyết regime
shift. Nhánh tiếp theo dùng OI/funding/positioning để nhận diện regime trước entry.

OI/funding/positioning gate, market-neutral z-score, multivariate LightGBM,
calendar seasonality, BTC+ETH portfolio component và liquidation-shock reversal
đều đã bị reject. Derivatives gate tốt nhất đạt train/validation dương nhưng test
`-5,19%`; market-neutral mean reversion có `0/216` train candidate; shock reversal
có `0/648`. Multivariate candidate tốt nhất dùng 1h, TP 2,5 ATR, SL 1,5 ATR,
timeout 24h và contrarian model: base-cost train/test dương nhưng validation
`-5,40%`; tại stress cost cả ba split đều âm. Sửa target từ raw return sang
R-multiple đúng position sizing cũng không qua holdout. BTC component khoảng
1,9 excursion/tuần và ETH component khoảng 0,9 excursion/tuần đều âm test,
nên không được ghép để che loss.

Kết luận hiện tại: directional BTC Swap với round-trip stress `0,30%` và tối
thiểu hai excursion/tuần không có edge được chứng minh trong dữ liệu OHLCV,
taker flow và derivatives hiện có. Workstream tiếp theo chuyển sang maker
microstructure, nơi fee contract phải dựa trên post-only maker fill và adverse
selection thay vì giả định taker/slippage. OKX cung cấp L2 chính thức từ tháng
3/2023 nhưng khoảng 432 MB nén/ngày cho 400 levels; Binance Futures có
bookTicker khoảng 54 MB nén/ngày từ 2023. Chỉ được promote sau backtest bằng
best-bid/ask + trade-through fill bảo thủ, temporal holdout và Paper lifecycle;
không dùng OHLCV để giả fill maker.

Maker BBO discovery đã dùng 1-second Binance Futures bookTicker trên bảy ngày
rải từ 2023-05 đến 2024-03. Grid đầu 256 cấu hình quote gần có `0` train
candidate; best vẫn `-0,93%` và âm cả ba train day. Grid deep-pullback 128 cấu
hình tìm được hypothesis imbalance 0,9 trong 15 giây, limit cách 20 bps, TP 25
bps, SL 30 bps, timeout 60 phút. Sau cap hai fill/ngày, base train / validation /
test là `+1,00% / +0,06% / +0,40%`, nhưng stress validation `-0,24%` và một test
day âm. Mô phỏng post-only timeout thêm 5 phút để tránh taker fee có `0/256`
train candidate vì adverse price risk. Candidate bị reject, không phải champion.

Maker discovery không còn bị chặn bởi thời lượng collector live: OKX cung cấp
tick trades từ 09/2021 và historical L2 từ 03/2023, đủ để xây temporal blocks
đại diện cho queue/fill research. Việc còn thiếu là pipeline ingest/reconstruct
book theo sequence và simulator fill/adverse-selection, không phải nguồn dữ liệu.
Collector live vẫn cần cho forward parity nhưng không phải prerequisite của
offline falsification. Nhánh này chạy sau session-conditioned intraday; nếu thay
fee/frequency contract phải ghi quyết định mới trước khi chạy, không hồi tố để
cứu candidate.

## SUPERSEDED-SLOW-PULLBACK-PROFIT — Champion trước đây

```yaml
status: SUPERSEDED_BY_BTC_SPOT_VOL_SCALED_TREND
runtime_strategy_changed: false
symbol: BTC/USDT
signal_timeframe: 4h_from_closed_5m
zscore_lookback_bars: 60
entry_z_abs: 2.0
trend_filter: close_vs_EMA180
entry_execution: next_4h_open
max_tranches_per_excursion: 5
capital_fraction_per_tranche: 0.20_of_account_equity_cap
stop_loss_atr: 5.0
exit_z_abs: 0.5
round_trip_cost_pct_per_ticket: 0.30
production_core: src/engine/staggered_pullback.py
runtime_execution_flag: STAGGERED_PULLBACK_ENABLED=false
primary_objective: risk_normalized_portfolio_net_return
secondary_metrics: [profit_factor, expectancy, max_drawdown, tail_loss]
diagnostic_only: [win_rate, ticket_count, rolling_frequency]
```

Portfolio-profit Champion trước đây: grid 720 cấu hình được chọn hoàn
toàn trên train sau khi yêu cầu ít nhất 4/6 cửa sổ 365 ngày có lãi và coverage
tối thiểu 5 excursion/năm. Phép tính dùng equity compounding, tổng risk
1%/excursion và capital cap `1/max_tranches` cho mỗi tranche. Candidate train-only
hiện tại là lookback 60 bar, entry `|z|=2,0`, exit `|z|=0,5`, SL 5 ATR và tối đa
5 tranche. Kết quả train / validation / test lần lượt là net portfolio
`+4,55% / +0,34% / +1,50%`, PF `2,62 / 1,32 / 3,19`, max drawdown
`1,45% / 1,04% / 0,63%`. Cost stress 0,60% vẫn dương cả ba split; ngay cả
0,80% vẫn dương. LONG/SHORT breakdown và neighborhood check không phát hiện
contract lệch đơn điểm. Contract đã freeze ở research nhưng runtime vẫn OFF.
Artifact authoritative:
`data/backtests/staggered_portfolio_profit_champion_9y.json`.

Grid mở rộng 13.125 cấu hình chỉ dùng train để chọn challenger lookback 45,
EMA120, entry `|z|=1,0`, exit `|z|=1,0`, SL 3 ATR, 3 tranche. Challenger đạt
train / validation / test `+15,10% / +2,70% / +0,52%` ở cost 0,30%, nhưng test
PF chỉ 1,047 và chuyển âm `-0,20%` ngay tại cost 0,40%, `-1,64%` tại cost 0,60%.
Challenger bị reject bởi cost gate và không thay champion. Artifact chẩn đoán:
`staggered_portfolio_optimization_9y.json` cùng
`staggered_portfolio_expanded_challenger_stress_9y.json`.

Baseline frequency cũ dùng cùng dataset chia train sáu năm, validation một năm
và test gần hai năm: train /
validation / test đạt `10,40 / 11,67 / 10,51` ticket/30 ngày, mean net/ticket
`+0,707% / +0,592% / +0,309%`, PF `1,34 / 1,38 / 1,23` sau cost 0,30%.
Kết quả này là baseline để tối ưu lại, không còn là selection objective.

Đây là scale-in chứ không phải 10–20 setup độc lập: chỉ có khoảng
`1,73 / 1,97 / 2,04` z-score excursion mỗi 30 ngày. Cap mười tranche áp dụng
cho toàn bộ excursion và chỉ reset sau khi z vượt exit; không cho re-entry vô
hạn sau SL. Frozen validator xác nhận dataset hash, next-open fill, prior-bar
signal, tranche/concurrency cap và toàn bộ metrics. Stress cost còn dương cả ba
segment tới 0,60%, nhưng test chỉ sát hòa vốn ở mức này. Với sizing tổng risk
setup 1% chia mười tranche, max additive drawdown xấp xỉ
`-5,46% / -1,61% / -1,23%`; return risk-normalized test chỉ `+0,20%` trong gần
hai năm. Số ticket danh nghĩa không phải risk episode độc lập và lợi nhuận
portfolio quá mỏng, nên candidate không đạt frequency/profit gate hiện tại.

Production core hiện đã freeze theo portfolio-profit champion, hỗ trợ LONG/SHORT,
feature causal, next-open entry, ATR stop, exit z-score, cap tranche theo toàn
excursion và position sizing tổng risk 1%/excursion. Artifact
`staggered_portfolio_runtime_parity_9y.json` xác nhận OHLC/ATR/EMA cùng toàn bộ
88/18/30 trade khớp reference từng trường, không có mismatch; regression core
pass 6/6. Flag execution mặc định OFF. Chưa nối order lifecycle live vì candidate
có SHORT trong khi Paper đang Spot và exit accounting hiện hành mặc định LONG;
phải hoàn tất Swap/two-sided accounting parity trước, không được bỏ SHORT hoặc
đổi fill contract để bật sớm. Runtime fail-closed nếu flag bị bật trước gate này,
không silently fallback về strategy cũ.

Accelerated Paper replay đã chạy ba năm gần nhất (`2023-08-07` đến
`2026-08-07`) qua SQLite lifecycle với simulated clock. Unit lifecycle pass,
E2E dữ liệu thật khớp `9/9` trade; long run khớp production reference `48/48`,
không mismatch và không orphan ledger. DB có 6.576 feature snapshot, 48 ENTRY,
48 EXIT, 48 equity-ledger row, 96 signal và không còn position mở. Equity $500
thành $509,13: net `+1,826%`, PF `2,030`, win rate chẩn đoán 75%, max drawdown
`1,044%`; 15 excursion độc lập, LONG/SHORT cùng 24 ticket. Artifact và DB:
`data/backtests/staggered_paper_replay_3y.json` và
`data/backtests/staggered_paper_replay_3y.db`. Đây là historical implementation
parity; flag runtime vẫn OFF cho tới khi live Swap integration và forward health
observation pass.

## SUPERSEDED-SLOW-PULLBACK-FORWARD — Candidate research cũ

```yaml
status: SUPERSEDED_BY_SLOW_PULLBACK_PROFIT
runtime_strategy_changed: false
symbol: BTC/USDT
signal_timeframe: 4h_from_closed_5m
entry: trend_aligned_zscore_pullback
zscore_lookback_bars: 30
entry_z_abs: 2.0
trend_filter: close_vs_EMA180
stop_loss_atr: 8.0
exit: zscore_returns_to_zero
round_trip_cost_pct: 0.30
```

Rule từng đạt expectancy dương và PF > 1 trên train 4 năm, validation 1 năm và
test 1 năm. Nó chỉ còn là kết quả lịch sử trong `backtest-results.md`; không còn
là candidate forward sau khi portfolio-profit champion phía trên đã pass gate.

## TODO-ENTRY-MICROSTRUCTURE-DATA — Thu thập confirmation data

```yaml
status: COLLECTING
started_at_utc: 2026-08-07T10:55:32Z
sample_interval_seconds: 30
runtime_strategy_changed: false
minimum_observation: 2_weeks
```

OHLCV-only đã không tạo entry rule ổn định sau chi phí. Collector hiện ghi
append-only `ORDER_FLOW_SAMPLE` gồm taker buy/sell volume và CVD, cùng
`ORDER_BOOK_SAMPLE` gồm top-20 imbalance, best bid/ask, mid và spread. Gap WS
tiếp tục được đánh dấu để loại khỏi training. Dữ liệu dùng kiểm định xem order
flow có xác nhận được bottom/CHOCH hay không; không tham gia quyết định Paper
trước khi đủ coverage và qua temporal holdout.

Historical proxy đã hoàn tất trên 180 ngày Binance spot và USD-M futures:
51.840 nến 5m mỗi market, event coverage 100%. Taker imbalance, absorption,
volume confirmation và divergence spot/futures đều không qua gate ở cả ba
segment. Số liệu nằm trong `backtest-results.md`. Workstream vẫn `COLLECTING`
vì historical kline không có L2 order-book imbalance; bước offline kế tiếp dùng
funding, open interest và long/short positioning, còn L2 live phải đủ hai tuần.

Funding/OI/positioning historical, wide TP/SL/horizon, ATR trailing, EMA
crossover và LightGBM fixed-close 24h đã kiểm định xong nhưng đều fail temporal
gate. Chi tiết và artifact nằm trong `backtest-results.md`. Nhánh offline hiện
đã chạy thêm triple-barrier classification, per-side calibration, causal regime,
ETH leadership và pullback limit; tất cả vẫn fail test. Candidate frozen gần
nhất là TP 2%/SL 1,5%, return-24h regime, rolling 7 ngày percentile 50. Bước kế
tiếp kiểm định contract này trên lịch sử cũ hơn; runtime/Paper vẫn không đổi.

Frozen triple-barrier contract đã bị bác bỏ trên ba block lịch sử cũ và bằng label 1m chính
xác hơn. L2 collector đang chạy; tại `2026-08-07T11:35:38Z` mới có 82
`ORDER_BOOK_SAMPLE` và 78 `ORDER_FLOW_SAMPLE`, khoảng 40 phút dữ liệu, chưa đủ
minimum observation hai tuần. Không được train/promote trên sample này.

## TODO-CHOCH-ENTRY-STUDY — Swing Low → bullish structure break

### Trạng thái và contract

```yaml
status: DONE_REJECTED
scope: OFFLINE_ENTRY_STUDY_ONLY
runtime_change_allowed: false
timeframe: 5m
swing_window_bars: 3
breakout_buffer_atr: 0.10
breakout_window_minutes: 240
retest_window_minutes: 240
retest_touch_buffer_atr: 0.15
retest_confirm_buffer_atr: 0.05
stop_loss: confirmed_swing_low - 0.20_ATR_at_low
take_profit: max(0.75_pct, 1.5_x_entry_to_SL_risk)
maximum_hold_minutes: 1440
```

Sau khi swing low được xác nhận causal, lấy minor confirmed swing high gần nhất
trước đáy. CHOCH/BOS chỉ xảy ra khi nến 5m đóng trên high đó + `0,10 ATR` trong
4 giờ. So sánh entry tại open nến kế tiếp với nhánh chờ retest level đã phá và
bullish close. Outcome dùng tick proxy 1m adverse-first, cost 0,30%, cooldown
một giờ và ba segment train/validation/test có purge 24 giờ.

Nếu hai nhánh cơ sở không qua gate, chỉ tiếp tục với filter cấu trúc được suy ra
từ discovery và phải giữ nguyên trên validation/test. Không đổi Paper trước khi
có rule đủ mẫu, mean net dương, profit factor trên 1 và không phụ thuộc một
segment duy nhất.

Kết quả: direct, volume-confirmed, retest, context 1h/4h, filter logic, cây nông,
random forest và Fibonacci limit 0,382/0,5/0,618 đều âm ngoài mẫu. Liquidity
sweep 1h/4h/24h và thư viện 11 trigger technical cũng không có edge; đóng nhánh
OHLCV-only. Số liệu nằm trong `backtest-results.md`.

## TODO-BOTTOM-UP-ENTRY-DISCOVERY — Suy luận rule từ đáy tốt

### Trạng thái và contract

```yaml
status: DONE_REJECTED
scope: OFFLINE_DISCOVERY_ONLY
runtime_change_allowed: false
side: LONG_SPOT
signal_timeframe: 5m_closed
feature_source: 5m_candles_only
fill: next_5m_open
maximum_horizon_minutes: 1440
stop_loss_price: signal_low - 0.20_ATR
take_profit_distance_pct: max(0.75, 1.5_x_structural_SL_distance)
round_trip_cost_pct: 0.30
```

Potential bottom tại runtime chỉ dùng thông tin quá khứ: low hiện tại tạo low mới
so với ba nến trước. Label hồi cứu `good_bottom` yêu cầu low đó vẫn là local low
trong ba nến sau và, nếu fill ở open nến kế tiếp, đường giá 1m chạm TP trước SL
trong 24 giờ. Ba nến tương lai chỉ tạo label offline, không được đưa vào feature
hay rule runtime.

Suy luận ngược bằng các cây quyết định nông trên nhiều nhóm feature 5m: reversal,
trend/momentum, structure/volatility và tập hợp đầy đủ. Feature chỉ gồm indicator
và rolling state có sẵn lúc nến đóng. Dữ liệu chia theo thời gian thành train,
validation và test; purge tối thiểu một horizon ở biên. Rule được biểu diễn bằng
đường điều kiện của leaf, không dùng vị trí đáy tương lai.

Rule chỉ được đóng băng thành candidate khi có đủ mẫu, mean net return dương,
profit factor trên 1 và không phụ thuộc một segment duy nhất. Chỉ candidate đã
đóng băng mới được đưa sang walk-forward/holdout dài hơn; nếu không có rule qua
gate thì giữ no-trade và không đổi Paper.

Kết quả 180 ngày: 15.803 potential bottom, 4.989 local bottom hồi cứu và 1.546
good bottom theo structural SL/TP. Cây nông không tìm được leaf dương ổn định;
random forest overfit train và âm trên validation/test. Chờ xác nhận swing low
5–40 phút cải thiện win rate nhưng mọi cửa sổ vẫn có expectancy âm ở cả ba
segment. Không có rule 5m-only qua gate, không thay đổi runtime. Chi tiết nằm
trong `backtest-results.md`.

## TODO-BREAKOUT-RETEST-STUDY — Khám phá entry Long theo giá

### Trạng thái

```yaml
status: DONE_REJECTED
scope: OFFLINE_EVENT_STUDY_ONLY
dataset: BTC/USDT Spot 5m + tick proxy 1m, last 30 days
runtime_change_allowed: false
```

Mục tiêu là tìm candidate entry từ hành vi giá, không tối ưu TP/SL và không đổi
Paper runtime trong bước này. Mọi tín hiệu chỉ dùng nến đã đóng; entry giả định
ở open nến 5m kế tiếp. So sánh bốn họ rule đã đăng ký trước: confirmed resistance
breakout, rolling-high breakout, breakout→retest và EMA20 pullback→reclaim.

Đánh giá tại 15m/1h/4h/24h bằng forward return, MAE/MFE, xác suất vượt chi phí
và matched control cùng EMA trend, ATR quintile, 4-hour UTC bucket. Dữ liệu chia
theo thời gian thành 15 ngày discovery và 15 ngày validation; kết quả 30 ngày
chỉ dùng chọn candidate. Chỉ candidate có lift cùng dấu trên validation mới được
đưa sang phép xác nhận 180 ngày. Không implement trade rule nếu chưa qua bước đó.

Breakdown-retest Short chỉ được test offline và không chạy Paper trước khi
`TODO-SWAP-PARITY` hoàn thành.

Kết quả: không họ rule nào có forward return tuyệt đối đủ bù chi phí 0,30% một
cách ổn định giữa discovery, validation và 150 ngày lịch sử chưa dùng để khám
phá. Breakout→retest confirmed resistance là mẫu duy nhất dương ở horizon 24h
trong 30 ngày, nhưng đảo thành âm trên 92 mẫu của 150 ngày trước đó. Các filter
EMA dài hạn, ADX, volume và tốc độ retest cũng không tạo expectancy sau chi phí
dương trên lịch sử. Không implement rule và không đổi Paper runtime. Số liệu đầy
đủ nằm trong `backtest-results.md`; hướng tiếp theo là
`TODO-ENTRY-MICROSTRUCTURE-DATA`.

## EXP-SR-SCORE-V4 — Support/Resistance-only scoring

### Trạng thái và flag

```yaml
experiment_id: EXP-SR-SCORE-V4
status: OFFLINE_REJECTED_PAPER_OBSERVATION_ACTIVE
active: true
score_version: sr_score_v3_partial_hyperbolic
tp_policy_version: tp_multi_high_fib_v2
code_change_allowed: true_for_requested_partial_score_change
requested_scoring_profile: support_resistance_only
requested_flag: SCORING_PROFILE=support_resistance_only
```

Runtime và paper backtest đã đọc `SCORING_PROFILE`; profile
`support_resistance_only` chỉ dùng Support/Resistance để tạo decision score.
Gate offline V1 đã bác bỏ candidate. V2 thêm partial exponential; ngày
2026-08-07 user yêu cầu V3 hyperbolic để score phản ứng rõ hơn khi giá còn cách
support 1–3 ATR. Event study V4 trên 180 ngày đã bác bỏ support touch và
touch→reclaim như entry standalone. Paper hiện chỉ là observation do user
override, không phải candidate promotion.

Presentation contract của Paper mode active:

```yaml
support_resistance: {weight: 100, score: BUY_SUPPORT hoặc SELL_SUPPORT_BREAKDOWN}
technical: {weight: 0, score: 0}
order_flow: {weight: 0, score: 0}
derivatives: {weight: 0, score: 0}
cross_market: {weight: 0, score: 0}
sentiment: {weight: 0, score: 0}
regime: {weight: 0, score: 0}
```

Raw data các lớp vẫn được lưu trong Feature Store nhưng không tham gia Total
score. Technical breakdown trên dashboard cũng hiển thị toàn bộ bằng 0.

### Kết luận offline V4

Event study causal 180 ngày có 559 first support touch, 393 touch→reclaim và
matched control theo EMA trend, ATR quintile, UTC bucket. Forward-mean lift của
cả touch và reclaim đều âm tại 5m/15m/1h/4h/24h. Contract và số liệu nằm trong
`backtest-results.md`; nhánh mean-reversion standalone đã đóng, không implement
reclaim entry. Runtime Paper đang hoạt động chỉ để observation theo user
override; entry research kế tiếp nằm ở
`TODO-ENTRY-MICROSTRUCTURE-DATA`. Final
verification: Python compile, JSON artifact contract, scoring 20/20, S/R 24/24
và `git diff --check` đều pass.

Mỗi `MARKET_TICK` ghi thêm `sr_monitor`:

- Giá hiện tại, ATR 5m, score side và effective score.
- Support/resistance zone low-high, độ rộng và quan hệ ABOVE/INSIDE/BELOW.
- Khoảng cách tới zone theo USD và ATR.
- Touch count, ATR lúc hình thành, giá/thời gian các swing tạo zone.
- Support zone đóng băng tại entry và breakdown score nếu đang có vị thế.
- `support_status`, số touch/required, `buy_eligible` và lý do hard-gate.

Dashboard có card **Support / Resistance hiện tại**, đọc event mới nhất qua API
`/api/support-resistance`; khi thiếu zone phải hiển thị rõ “Chưa có zone hợp lệ”.
Diagnostics vẫn hiển thị khi không có active zone: toàn bộ confirmed swings,
các cặp gần điều kiện nhất, spread/allowed spread, invalidation level, extreme
close và lý do `NOT_INDEPENDENT`, `TOO_WIDE` hoặc `BROKEN`.

Khi runtime đã hỗ trợ profile này, việc chạy thử chỉ được đổi flag/config, không
sửa công thức giữa các lần đo.

### Workstream chặn offline backtest

Làm theo thứ tự dưới đây.

#### TODO-HORIZON-CONTRACT — Triển khai horizon đã chốt

**Trạng thái: DONE — regression boundary đã pass.**

Contract là không có minimum hold và maximum hold 24 giờ. SL/TP, risk gate hoặc
tín hiệu exit hợp lệ được phép đóng lệnh ngay; vị thế còn mở ở mốc 24 giờ phải
đóng bằng timeout exit có reason riêng.

Runtime/config đã bỏ gate `MIN_HOLD_MINUTES`, dùng `MAX_HOLD_MINUTES=1440` và
primitive elapsed-time chung cho live, bar-close backtest, paper backtest, Short
và diagnostic engine. Config cũ có `MIN_HOLD_MINUTES` không còn tác động. Compile
toàn bộ source/scripts và 20/20 scoring regression đã pass.

#### TODO-PNL-LEDGER — PnL/risk theo size và equity

**Trạng thái: DONE — full parity/accounting regression pass.**

Đã bổ sung accounting primitive chung tính fill hai chiều, fee theo notional,
slippage, funding, PnL USD và return-on-equity. Live ghi `equity_ledger` idempotent
theo trade ID; daily loss và max drawdown đọc equity thật. Risk sizing dùng current
equity; các backtest engine không còn giả định mỗi trade dùng toàn equity.

Đã verify migration DB tạm, duplicate trade không ghi ledger hai lần, equity/PnL
reconcile và drawdown phản ứng với trade lỗ; compile và 20/20 scoring test pass.

#### TODO-BACKTEST-PARITY — Đồng nhất trade behavior

**Trạng thái: DONE cho tick lifecycle; scheduled-window coverage vẫn là policy khác.**

Backtest phải dùng chung entry, exit/timeout theo thời gian thật, cost, fill,
position sizing và accounting primitives với Paper Trading. Cần xử lý activation
window, refresh candle, multi-position và mark-to-market cuối kỳ; trước đó kết
quả chỉ mang tính chẩn đoán.

Paper replay hiện dùng chung decision/risk/accounting/S/R primitives với live,
cooldown theo timestamp thật, fill proxy `subbar_ohlc_adverse_first` và đóng vị
thế cuối dataset bằng `END_OF_DATA_MARK_TO_MARKET`. Experiment contract cố định
một vị thế đồng thời ở cả live/replay; behavior nhiều vị thế của Champion không
thuộc profile này. Fixture end-to-end live primitives == paper replay đã pass.
Activation/refresh của process thật được xử lý riêng trong workstream Paper bên
dưới, không còn là sai lệch của offline replay liên tục.

Ngày 2026-08-07 đã sửa GAP post-entry: nếu entry xảy ra ở open/low của sub-bar
1m, replay tiếp tục xử lý high/close cùng toàn bộ tick còn lại trong primary bar,
không nhảy sang bar kế tiếp. Regression cùng-sub-bar entry→TP pass; S/R suite
đạt 24/24. Offline continuous và Paper scheduled-window vẫn là hai coverage
policy khác nhau và phải được ghi rõ khi so sánh kết quả.

#### TODO-SR-SCORE-EXPERIMENT — Implement profile thử nghiệm

**Trạng thái: DONE.**

Đã implement zone clustering, causal swing confirmation, proximity/zone-quality
score, SL theo đáy thấp hơn, TP direct/Fibonacci và profile flag dùng chung ở
live/paper backtest.

#### TODO-SR-REGRESSION — Test invariant của experiment

**Trạng thái: DONE — 13/13 deterministic regression pass.**

`scripts/test_sr_scoring.py` kiểm tra causal confirmation, boundary ATR,
independent touch/quality/proximity, SELL chỉ sau support breakdown, SL lower-low,
direct/Fibonacci TP, reject target, horizon, USD accounting và parity fixture
end-to-end. Compile cùng scoring regression cũ 20/20 đều pass ngày 2026-08-06.

#### TODO-FEATURE-LINEAGE-VERIFY — Manifest và lineage tái lập được

**Trạng thái: DONE.**

Kiểm thử migration/schema và ghi scoring profile, toàn bộ tham số, candle policy,
timeframe, source symbol, cost/fill assumption cùng engine/strategy version vào
manifest và Feature Lineage.

Schema round-trip pass. Artifact dùng JSON strict, dataset hash, manifest hash,
engine/feature/strategy version, source, candle/fill/cost policy và toàn bộ tham
số S/R; validator xác nhận không NaN/Infinity và PnL reconcile theo từng trade.

#### TODO-EDGE-WALK-FORWARD — Chạy phép thử

**Trạng thái: DONE — REJECTED offline; Paper observation được user override.**

Chỉ chạy sau các task trên. Dùng nhiều regime, walk-forward không chồng lấp và
confidence interval; tối ưu net portfolio return sau chi phí với cùng sizing,
đồng thời giữ drawdown trong giới hạn. Win rate chỉ dùng chẩn đoán. Không kết
luận từ 4–12 trade hoặc chọn tham số trên test set.

Kết quả cố định 2026-08-06, Long Spot BTC/USDT 5m, tick proxy 1m, 180 ngày,
4 trading window không chồng lấp, 51,735 primary bars và 258,675 tick bars:

```yaml
dataset_hash_primary: c1662954c02e3ce2a4dc9e02e45a517dfdc105f73b303f9c3df7529936132dc9
dataset_hash_tick: 7bee6dd04e19c0b4959cfbe49becd0fb714d2d611ff464ccc9609705a1486977
baseline: {trades: 0, net_pnl_usd: 0}
one_swing_negative_control: {trades: 0, reason: max_score_50_below_threshold_70}
two_swing_candidate:
  trades: 44
  wins: 5
  losses: 39
  win_rate_pct: 11.36
  win_rate_ci95_pct: [4.9527, 23.9794]
  break_even_win_rate_pct: 35.738
  net_pnl_usd: -65.2907
  total_return_pct: -13.0581
  max_drawdown_pct: 13.7515
  profitable_segments: 1/4
  exits: {STOP_LOSS: 39, TAKE_PROFIT_FIB: 5}
three_swing_sensitivity: {trades: 0}
champion_reference: {trades: 0, reason: max_score_69_below_threshold_70}
```

Candidate không đạt net PnL hoặc stability gate. Không tuning
trên holdout. User sau đó yêu cầu bật riêng Paper observation; việc này không đảo
ngược kết luận REJECTED và hệ thống vẫn không đặt lệnh thật trên sàn.
Artifacts: `/tmp/sr_{baseline,1swing,2swing,3swing,champion}_180d_wf4.json`.

Không cần hoàn thành `TODO-REVALIDATE-BACKTESTS` trước experiment này; chỉ cần
engine parity, sau đó tạo kết quả mới bằng profile mới.

### Workstream chặn Paper test thời gian thật

#### TODO-CONFIG-SSOT — Hợp nhất runtime config

**Trạng thái: DONE.**

Dashboard đang ghi `config/paper.env`, còn launchd truyền EnvironmentVariables
riêng. Hoàn thành khi Rule Engine, collector và dashboard đọc cùng nguồn, hiển
thị effective config và thể hiện rõ thay đổi nào cần restart. Khi đó bật profile
chỉ bằng flag mới có ý nghĩa.

Rule Engine/collector nhận duy nhất `RUNTIME_ENV_PATH=config/paper.env`; runtime
file override biến launchd/shell cũ. Dashboard đọc/ghi đúng file này, có profile
selector, trả effective source và báo cần restart. Hai plist đã bỏ bản sao config
và `plutil -lint` pass.

#### TODO-RUN-LOCK — Làm run lock atomic

**Trạng thái: DONE.**

`run_lock()` hiện SELECT rồi UPSERT và stale sau 12 giờ, ngắn hơn maximum hold
24 giờ. Hoàn thành khi acquire atomic, có owner token/heartbeat và release chỉ
xóa đúng owner; process giữ vị thế không bị process khác coi là stale.

Acquire dùng SQLite `BEGIN IMMEDIATE`, lease có owner token/heartbeat, refresh
trong market loop và release chỉ xóa đúng owner. Acquire cũng reclaim ngay lease
của PID đã chết sau force-restart, không chờ stale timeout. Regression cạnh tranh
lock và dead-owner pass.

#### TODO-MARKET-FRESHNESS — Không dùng snapshot cũ

**Trạng thái: DONE.**

Khi collector stale, runtime có thể fallback giá REST lấy đầu window; OHLCV/layer
không refresh khi window kéo dài. Hoàn thành khi proximity, reclaim, SL và TP chỉ
dùng dữ liệu có timestamp/age hợp lệ, hard-fail hoặc fetch mới khi stale.

Mỗi poll dùng WS tick chỉ khi age hợp lệ; nếu stale/missing thì fetch REST ticker
ngay và hard-fail nếu REST timestamp cũng stale. `MARKET_TICK` ghi source,
timestamp và age. OHLCV/layer được fetch lại ở đầu mỗi cycle kể cả khi còn vị thế.

#### TODO-MONITOR-CADENCE — Loại bỏ khoảng trống activation

**Trạng thái: DONE trong code/plist; profile S/R không được activate.**

Runtime đã quan sát gap `MARKET_TICK` khoảng 307–316 giây. Hoàn thành khi daemon
hoặc lịch refresh độc lập tạo coverage đủ để không bỏ lỡ touch, reclaim, SL và TP.

Workstream coverage liên tục trước đây đã dùng `RUN_CONTINUOUS=true`/`KeepAlive`.
Quyết định mới của user thay thế bằng scheduled window: `RUN_SCHEDULED=true`,
`RUN_CONTINUOUS=false`; scheduler nhẹ neo start-to-start, activation/window/poll
cấu hình độc lập. Khoảng trống giữa các window là hành vi chủ đích của Paper
test, không còn được mô tả là daemon coverage liên tục.

### Verification vận hành cuối

Ngày 2026-08-06 đã reload service khi DB Paper không có vị thế mở:

```yaml
rule_engine: {state: running, config_source: config/paper.env}
collector_ws: {state: running, config_source: config/paper.env}
dashboard: {state: running}
effective_profile: support_resistance_only
run_continuous: false
activation_interval_minutes: 60
monitor_window_minutes: 5
monitor_poll_seconds: 5
support_resistance_profile_active: true
latest_tick_source: collector_ws
run_lock_heartbeat: healthy
```

Runtime smoke bằng DB tạm đã ghi ba tick fresh, lineage đủ contract và giải phóng
lock sạch. Final verification: `git diff --check`, plist lint, compile, scoring
20/20, S/R/runtime regression 16/16 và artifact validator đều pass. Sau user
override, live event xác nhận S/R weight 100; sáu layer cũ và toàn bộ Technical
breakdown bằng 0. Structured S/R event, dashboard API và JavaScript syntax đều
được verify sau khi restart Rule Engine/Dashboard.

V2 partial-score được deploy khi Paper DB không có vị thế mở. Compile, scoring
chung `20/20`, S/R regression `18/18` và `git diff --check` đều pass. Sau
restart, event thật ghi `support_status=SINGLE_SWING_CANDIDATE`,
`required_swings=2`, `buy_eligible=false`, reason `NEED_MORE_SWINGS`; BUY score
biến thiên dương (đã quan sát `0.14`, `0.10`, `0.08`) thay vì 0. Dashboard API
trả đủ các field này. Rule Engine và Dashboard đều ở trạng thái running.

Dashboard đã bỏ scheduler cron legacy và đọc trực tiếp launchd label
`com.ai-crypto.paper`. Theo quyết định mới, card Runtime cấu hình độc lập ba giá
trị: activation interval, monitor window và poll cadence; lưu sẽ đặt
`RUN_SCHEDULED=true`, `RUN_CONTINUOUS=false`, giữ scheduler nhẹ bằng launchd
`KeepAlive` và reload service. Service hiển thị `monitoring` trong cửa sổ,
`waiting` giữa hai lần kích hoạt. Log viewer đọc `run_paper_launchd.log`.

V3 hyperbolic proximity đã pass S/R regression `19/19` và được reload khi Paper
DB không có vị thế mở. Với support `64590.8`, ATR khoảng `87.27`, mapping kiểm
tra đổi từ vùng `0.01–0.04` của V2 thành `4.43–5.24` khi giá đi từ `64779.4`
về `64747.2`. Tick thật đầu tiên sau deploy ghi BUY observation score `5.52`,
`SINGLE_SWING_CANDIDATE`, `buy_eligible=false`, `NEED_MORE_SWINGS`.

Ngày 2026-08-07 đã xử lý hai BUY candidate đêm trước bị mất: `96.46` và `73.34`
đều có support hợp lệ nhưng thiếu resistance hai-touch; nhánh reject sau đó
crash vì đọc cứng `tp_distance_pct`. Runtime giờ ghi `BUY_CANDIDATE`, fallback
`SINGLE_SWING_TARGET`, chỉ ghi `SIGNAL_GENERATED=BUY` sau khi position plan qua
gate, và mọi early reject có `reject_gate` an toàn. Scheduler được đổi sang
Python start-to-start (`60/50/5`), retry fetch lỗi trong cùng slot, launchd chỉ
KeepAlive scheduler nhẹ. S/R regression `22/22`, scoring chung `20/20`, compile,
JavaScript syntax và plist lint đều pass. Smoke live sau reload có monitoring
lock/heartbeat khỏe và lineage trả `resistance_status`.

Counterfactual hai candidate cũ với single-high fallback vẫn bị cost gate từ
chối: target gần nhất không đạt `MIN_TP_COST_RATIO=2.5` trên giả định round-trip
cost Spot hiện tại. Vì vậy thay đổi này tăng số candidate đi tới position-plan
và loại crash, nhưng không được mô tả sai là chắc chắn tạo entry nếu target sau
phí chưa đủ lợi nhuận.

V4 multi-high TP planner đã pass regression `23/23` và được deploy khi không có
vị thế mở. Live smoke ghi `EXP-SR-SCORE-V4`, policy
`tp_multi_high_fib_v2`, monitoring heartbeat khỏe và tìm được năm target high
còn hiệu lực theo thứ tự `64457.0, 64475.0, 64476.9, 64479.0, 64549.9`.
Dashboard hiển thị danh sách này trong card S/R. Target xa vẫn phải đủ cost/R:R;
planner không dùng high cũ đã bị close phá chỉ để ép tạo entry.

Dashboard có timeline `Score + Giá` ngay dưới title, đọc từ `MARKET_TICK`, chọn
chính xác mốc ngày/giờ hoặc preset 1 giờ, 6 giờ, 24 giờ và 7 ngày. Mặc định là
cửa sổ 1 giờ trượt và chart refresh theo poll tick 5 giây; khoảng custom qua nút
Áp dụng được giữ cố định. API giới hạn range 31 ngày,
dùng index `(event type, timestamp)` và giảm mẫu theo min/max score của từng
bucket để giữ spike; tooltip hiển thị action, score side, trạng thái S/R và BUY
eligibility. Biểu đồ có hai trục Score/Giá cùng đường threshold BUY/WATCH.
Verification ngày 2026-08-07: API trả đúng range, reject range đảo chiều, giữ
min/max trên 2.780 tick mẫu; compile, JavaScript syntax, scoring `20/20`, S/R
regression `23/23` và `git diff --check` đều pass. Dashboard đã reload, Rule
Engine giữ nguyên process đang chạy.

Timeline hỗ trợ nguồn riêng `Backtest 30 ngày`; không trộn artifact lịch sử vào
DB Paper live. Kết quả, contract và artifact của baseline cùng các sensitivity
test được quản lý tại `backtest-results.md`. Baseline không qua Gate 3 và không
phải bằng chứng triển khai Champion; artifact latest được phép ghi đè khi chạy
lại và dataset OHLCV nén được cache để tái lập cùng nguồn.

Nguồn được chọn trên timeline đồng thời điều khiển `Thống kê lệnh`, equity curve
và `Lịch sử lệnh BUY/SELL`. Backtest map đủ entry/exit, net PnL, SL, TP và exit
reason từ artifact; equity curve chuẩn hóa accounting equity thật về base 100.
Chuyển lại `Paper live` trả cả ba phần về DB Paper.

Card lịch sử lệnh nằm trước Support/Resistance. Mỗi dòng dùng outcome `WIN`,
`LOSE`, `EVEN` hoặc `OPEN`, đồng thời hiển thị cả PnL phần trăm và net PnL USD;
Paper Trade Summary đọc `pnl_usd` trực tiếp từ EXIT event giống backtest.

`TODO-HEARTBEAT` nên hoàn thành trước Paper test không giám sát nhưng không làm
thay đổi trực tiếp kết quả offline.

### Không chặn experiment Long Spot

`TODO-SWAP-PARITY`, `TODO-SIX-LAYERS`, `TODO-ORDERFLOW-QUALITY`,
`TODO-MTF-CONFLUENCE`, `TODO-SLOW-CONTEXT`, `TODO-DERIVATIVES-FEATURES`,
`TODO-RAG`, `TODO-ENTRY-MODEL` và `TODO-CHALLENGER` không phải prerequisite.
Các layer ngoài Support/Resistance có contribution bằng 0 trong test cô lập.

### Mục tiêu

Đo edge độc lập của vùng tạo bởi các previous confirmed swing low/high:

- Khi chưa có vị thế: vùng swing low tạo BUY score.
- Khi đang giữ Long Spot: phá xuống dưới vùng swing low của lệnh tạo SELL/close
  score; chạm/trùng đáy không tạo SELL.
- Vùng swing high chỉ dùng để chọn TP direct/Fibonacci, không tạo SELL score.
- SELL trong thử nghiệm này không đồng nghĩa mở Short.
- Các layer khác vẫn có thể được log để phân tích nhưng đóng góp vào decision
  score phải bằng 0.

Nếu feature độc lập có edge sau chi phí, bước sau mới thử dùng nó như một thành
phần cộng dồn trong ensemble hiện tại.

### Số swing cần để tạo vùng

Candidate chính dùng **hai previous confirmed swing low** nằm trong cùng một vùng.
Hai đáy trước tạo support; lần current price quay lại vùng là lần test tiếp theo
để cân nhắc BUY. Hai previous confirmed swing high tương tự chỉ tạo resistance
zone phục vụ tính TP.

Không cộng vô hạn theo số đáy:

- 1 đáy trước: chỉ là support candidate, không đủ tự kích hoạt BUY ở profile này.
- 2 đáy trước cùng vùng: đủ xác lập support cho candidate chính.
- 3 đáy trước: vẫn hợp lệ nhưng không mặc định mạnh hơn tuyến tính.
- Từ 4 lần test trở lên: phải coi là candidate weakening support; không tiếp tục
  cộng điểm vì mỗi lần chạm có thể hấp thụ bớt resting liquidity.

Các swing chỉ được gom thành một zone khi khoảng cách lớn nhất giữa chúng không
vượt `zone_width_atr × ATR`. Hai swing phải là hai lần test độc lập: confirmation
window không chồng nhau và giữa chúng không có close phá support quá break buffer.
Không gom các đáy thuộc hai mức giá khác nhau chỉ để tăng touch count.

Quy tắc mặc định cho lần test đầu:

```text
ATR_form  = median(ATR14 tại thời điểm hai swing được tạo)
zone_low  = min(swing_low_1, swing_low_2)
zone_high = max(swing_low_1, swing_low_2)

Hai đáy cùng zone khi:
abs(swing_low_1 - swing_low_2) <= 0.25 × ATR_form
```

Ví dụ ATR tại vùng là 100 USD thì hai đáy được phép lệch tối đa 25 USD. ATR được
đóng băng lúc hình thành zone để zone không tự đổi lịch sử khi volatility hiện
tại thay đổi.

Trong walk-forward phải giữ ba nhánh `1/2/3 previous swings` để kiểm chứng, nhưng
candidate mặc định trước khi xem test set là 2. Không tối ưu số touch trên test set.

### Score V3 — partial hyperbolic, luôn có observation score

Score chỉ dùng feature thuộc nhóm Support/Resistance: proximity và chất lượng
zone theo touch count. Chưa cộng MA, RSI, volume, order flow, wick, tuổi vùng hay
timeframe strength; nếu thêm các yếu tố đó thì không còn là test cô lập này.

Không dùng đúng một mức giá tuyệt đối hoặc bucket USD cố định. Khoảng cách phải
chuẩn hóa bằng ATR để dùng được khi giá BTC và volatility thay đổi:

```text
distance_to_support_atr = distance(current_price, support_zone) / ATR
breakdown_below_support_atr = (support_zone_low - current_price) / ATR

buy_score  = 100 × proximity(distance_to_support_atr)    × zone_quality(low_zone)
sell_score = breakdown_score(breakdown_below_support_atr)
```

`proximity()` đạt 1 bên trong vùng và giảm theo hàm hyperbolic từ biên ra ngoài,
không bị cắt cứng hoặc ép sát 0 quá nhanh. BUY observation score có floor `0.01`, vì vậy dashboard luôn
có giá trị S/R khác 0 kể cả khi chưa tìm được support. Floor chỉ dùng để quan
sát, không làm candidate đủ điều kiện BUY. SELL không
dùng proximity tới swing high: chạm hoặc trùng đáy vẫn bằng 0, chỉ mức phá xuống
dưới support mới tạo score. Zone width, break buffer, touch policy,
`zone_quality()` và threshold là tham số của experiment manifest. Một swing không
được phép đạt BUY threshold chỉ nhờ đứng đúng giá; hai swing hợp lệ có thể đạt
threshold khi current price đủ gần zone.

Proximity phía BUY được tính như sau:

```text
Nếu zone_low <= current_price <= zone_high:
    proximity = 1

Nếu current_price nằm ngoài zone:
    distance = khoảng cách tới biên gần nhất
    x = distance / (0.30 × ATR_current)
    proximity = 1 / (1 + sensitivity × x)

Nếu current_price < zone_low:
    vẫn tính observation score, nhưng buy_eligible = false cho tới khi reclaim
```

`sensitivity` được hiệu chỉnh để zone hai touch (`zone_quality = 1`) đạt đúng score 70
tại khoảng cách `0.09 × ATR_current`. BUY chỉ có thể xảy
ra khi giá nằm trong zone hoặc cao hơn `zone_high` tối đa mức này.
Ví dụ ATR hiện tại 100 USD thì khoảng mua sớm phía trên zone tối đa khoảng 9 USD.

Không BUY khi giá còn nằm dưới `zone_low`. Wick có thể quét xuống tối đa
`0.15 × ATR_current` rồi reclaim; đây là fake-break candidate. Một nến đóng dưới
`zone_low - 0.20 × ATR_current` làm zone mất hiệu lực.

SELL score của vị thế Long dùng support zone đã đóng băng tại entry:

```text
current_price >= zone_low - 0.15 × ATR_current  => SELL score = 0
0.15 < breakdown_atr < 0.20                    => tăng tuyến tính 0..100
breakdown_atr >= 0.20                           => SELL score = 100
```

Với threshold 70, SELL bắt đầu hợp lệ tại mức phá khoảng `0.185 ATR_current`
dưới `zone_low`. Nhờ vậy chạm đúng đáy không thoát lệnh và fake-break nhỏ không
bị nhầm là breakdown, nhưng SELL có thể phản ứng trước/đồng thời vùng SL cấu trúc.

`distance(price, zone)` bằng 0 khi giá nằm trong zone và là khoảng cách tới biên
gần nhất khi giá nằm ngoài; không tùy ý chọn một đáy/đỉnh làm mốc. Bộ mặc định
đăng ký trước cho lần test đầu:

```yaml
decision_threshold: 70
score_floor: 0.01
buy_threshold_distance_atr: 0.09
same_zone_max_spread_atr: 0.25
approach_width_atr: 0.30
fake_break_wick_atr: 0.15
invalidation_close_atr: 0.20
zone_quality:
  1_previous_swing: 0.50
  2_previous_swings: 1.00
  3_previous_swings: 0.90
  4_or_more_previous_swings: 0.70
```

Với threshold 70, một đáy trước có score tối đa 50 nên không thể tự kích hoạt
BUY; hai đáy trước là cấu hình đầu tiên có thể đủ điểm. Mapping này chỉ áp dụng
cho profile cô lập 0–100, không phải số điểm sẽ cộng thẳng vào Champion sau này.

Nếu chưa có cặp hai đáy hợp lệ, runtime fallback sang previous confirmed swing
low gần nhất và gắn `support_status=SINGLE_SWING_CANDIDATE`. Candidate này có
partial score trong `(0, 50]` nhưng `buy_eligible=false`; Decision Engine hard-gate
`NEED_MORE_SWINGS`, kể cả khi threshold được cấu hình thấp hơn 50. Nếu hoàn toàn
không có swing hợp lệ, score bằng floor `0.01` và hard-gate `NO_SUPPORT`.

### Xác định swing và chống look-ahead

- Chỉ dùng swing đã có đủ nến bên phải để xác nhận tại thời điểm quyết định.
- Previous swing phải nằm trước timestamp của entry/exit.
- Dùng cùng `window`, `lookback`, candle-close policy và ATR source trong live và
  backtest.
- Giá xuyên quá break buffer bên dưới swing low tạo SELL breakdown và làm support
  mất hiệu lực sau candle-close confirmation. Swing high không tạo SELL score.

Code hiện có `find_recent_swing_low/high()` có thể làm primitive ban đầu, nhưng
phải kiểm thử causal alignment trước khi dùng làm feature score.

### Vòng đời lệnh

Tuân thủ Horizon Contract:

- Không có minimum hold.
- SL/TP hoặc SELL score hợp lệ có thể đóng lệnh ngay.
- Vị thế còn mở sau 24 giờ đóng bằng timeout exit.

### Stop Loss theo swing low thấp hơn

Với support zone tạo bởi hai previous confirmed swing low:

```text
zone_low = min(swing_low_1, swing_low_2)
SL = zone_low - 0.20 × ATR_entry
```

SL dùng đáy **thấp hơn** trong hai đáy và đặt thấp hơn một buffer, không đặt đúng
đáy vì wick/fake breakdown có thể quét qua mức đó. `ATR_entry` và giá SL được
đóng băng tại entry; không tự nới SL ra xa nếu volatility tăng sau khi vào lệnh.

Nếu khoảng cách `entry - SL` làm position size không hợp lệ, vượt risk budget,
hoặc SL xung đột liquidation/cost gate thì bỏ entry, không kéo SL lại gần cho vừa.

Quy tắc Short sau này áp dụng đối xứng với swing high cao hơn; không thuộc test
Long Spot hiện tại.

### Take Profit theo swing high và Fibonacci

Resistance zone ưu tiên tối thiểu hai previous confirmed swing high
cùng vùng (`spread <= 0.25 × ATR_form`). Biên gần entry là:

```text
direct_tp = resistance_zone_low
distance_to_high_atr = (direct_tp - entry_price) / ATR_entry
```

Nếu `0 < distance_to_high_atr <= 3.0`, dùng `direct_tp`: chốt lời tại biên dưới
của resistance zone để không giả định giá chắc chắn chạm đúng đỉnh cao hơn.

Nếu chưa có zone hai đỉnh, một confirmed swing high còn hiệu lực được dùng làm
`SINGLE_SWING_TARGET` chỉ để dựng TP. Fallback này không cộng BUY score và vẫn
phải qua cost/minimum R:R gate; không có high hợp lệ thì reject bằng
`missing_resistance`, không được crash monitoring window.

TP planner không dừng ở resistance gần nhất. Nó gom mọi confirmed swing high
chưa bị close phá quá invalidation buffer, loại target dưới entry và duyệt theo
giá từ gần tới xa. Target gần không đủ cost/R:R thì tiếp tục thử high xa hơn;
với high xa hơn `3 ATR`, chọn Fibonacci level thấp nhất thỏa đồng thời cost và
minimum R:R. Lineage ghi `tp_policy_version=tp_multi_high_fib_v2`, plan/log ghi
số candidate đã xét và rank của resistance được chọn.

Nếu `distance_to_high_atr > 3.0`, coi swing high quá xa và tạo các target trung
gian theo Fibonacci trên đoạn từ đáy thấp hơn của support tới biên gần của
resistance:

```text
fib_low  = support_zone_low
fib_high = resistance_zone_low
fib_price(r) = fib_low + r × (fib_high - fib_low)
fib_levels = [0.382, 0.500, 0.618, 0.786]
```

Chọn **mức Fibonacci thấp nhất nằm trên entry** đồng thời thỏa cả hai điều kiện:

```text
fib_price - entry_price >= minimum_reward_after_cost
(fib_price - entry_price) / (entry_price - SL) >= minimum_risk_reward
```

`minimum_reward_after_cost` phải dùng cùng fee/slippage/funding assumption với
Cost Gate; `minimum_risk_reward` lấy từ experiment manifest. Mức target phải được
so sánh theo expected net contribution trong backtest portfolio, không mặc định
target gần nhất chỉ để tăng win rate. Nếu không có Fibonacci level nào hợp lệ
thì bỏ entry; không fallback sang một TP tùy ý.

Ví dụ với `support_zone_low=64,080`, `entry=64,100`, `ATR_entry=100` và
`resistance_zone_low=64,600`:

```text
SL = 64,080 - 0.20 × 100 = 64,060
Resistance cách entry 5 ATR → dùng Fibonacci
Fib 0.382 = 64,278.64
Fib 0.500 = 64,340.00
Fib 0.618 = 64,401.36
Fib 0.786 = 64,488.72
```

Chọn mức đầu tiên vượt cost floor và R:R floor. TP được ghi reason
`TAKE_PROFIT_DIRECT_HIGH` hoặc `TAKE_PROFIT_FIB`; đây là risk exit độc lập với
`SELL_SCORE` breakdown, giúp đánh giá riêng chất lượng target và tín hiệu phá đáy.

Bộ mặc định đăng ký trước cho lần test đầu:

```yaml
sl_buffer_atr: 0.20
far_resistance_atr: 3.00
fib_levels: [0.382, 0.500, 0.618, 0.786]
minimum_risk_reward: 1.50
```

### Thiết kế phép thử

So sánh tối thiểu ba nhánh trên cùng dữ liệu, cost và execution assumptions:

1. No-trade baseline.
2. `support_resistance_only`.
3. Champion hiện tại, chỉ để tham chiếu; không trộn score.

Chạy walk-forward không chồng lấp và báo cáo riêng theo timeframe/regime. Không
chọn tham số trên test set.

### Quy trình test sau khi implement

Các tên flag/CLI dưới đây là contract đầu ra của implementation. Nếu command chưa
được hỗ trợ thì `TODO-SR-SCORE-EXPERIMENT` chưa hoàn thành.

#### Gate 1 — Static và regression deterministic

```bash
PYTHONPYCACHEPREFIX=/tmp/ai-crypto-pycache \
  .venv/bin/python -m compileall -q src scripts
.venv/bin/python scripts/test_scoring.py
.venv/bin/python scripts/test_sr_scoring.py
```

`test_sr_scoring.py` phải dùng fixture tổng hợp, không gọi network/DB thật, và
test ít nhất:

- Swing chưa được dùng trước khi đủ right-side confirmation bars.
- Hai swing lệch đúng `0.25 ATR` cùng zone; lớn hơn biên này không cùng zone.
- Một previous swing không thể đạt threshold; hai swing có thể đạt.
- Giá trong zone, cách biên trên đúng `0.09 ATR`, vượt biên, nằm dưới support,
  fake-break/reclaim và candle-close invalidation.
- Chạm/trùng đáy và fake-break tới `0.15 ATR` không tạo SELL; SELL đạt threshold
  khi phá khoảng `0.185 ATR` và đạt 100 tại `0.20 ATR` dưới support.
- SL dùng swing low thấp hơn và buffer `0.20 ATR_entry` đóng băng.
- Direct TP khi resistance không quá `3 ATR`; từng mức Fibonacci và nhánh không
  có target thỏa cost/R:R.
- Không minimum hold; timeout đúng 1,440 phút (24 giờ).
- Cùng fixture cho live primitives và paper engine tạo cùng decision, entry,
  SL/TP, exit reason và accounting.

Gate đạt khi tất cả test pass, không NaN/inf và không có timestamp tương lai trong
zone/feature lineage.

#### Gate 2 — Smoke backtest

Dùng Long Spot, timeframe chính 5m và tick proxy 1m trước:

```bash
SCORING_PROFILE=support_resistance_only \
  .venv/bin/python scripts/run_paper_backtest.py \
  --symbol BTC/USDT --market-type spot \
  --timeframe 5m --tick-timeframe 1m \
  --days 30 --walk-forward 2 \
  --out /tmp/sr_smoke.json --no-mlflow
```

Smoke chỉ kiểm tra pipeline chạy hết và output đúng contract, chưa dùng để kết
luận edge. Kiểm tra JSON có experiment/profile/version, dataset range/hash,
tham số zone/SL/TP, fee/slippage, trade list và exit reason; tổng PnL phải reconcile
được từ từng trade.

#### Gate 3 — Offline walk-forward chính

Đóng băng dataset và manifest trước khi chạy. Không dùng nến cuối đang hình thành,
không thay tham số sau khi đã xem holdout.

```bash
SCORING_PROFILE=support_resistance_only \
  .venv/bin/python scripts/run_paper_backtest.py \
  --symbol BTC/USDT --market-type spot \
  --timeframe 5m --tick-timeframe 1m \
  --days 180 --walk-forward 4 \
  --out /tmp/sr_5m_180d_wf4.json --no-mlflow
```

Chạy cùng dataset/manifest cho các nhánh:

1. `1 previous swing` — negative-control kỳ vọng không tự BUY.
2. `2 previous swings` — candidate đã đăng ký trước.
3. `3 previous swings` — sensitivity test.
4. Champion hiện tại — benchmark riêng, không trộn score.

Nếu cần tuning, chỉ dùng các segment train/validation và phải chạy một holdout
chưa từng xem bằng dataset range riêng. CLI phải hỗ trợ `--since/--until` hoặc
dataset snapshot cố định; chỉ chia một lần rồi xem cả bốn segment không được gọi
là holdout sau khi đã chỉnh tham số từ kết quả đó.

Kết quả được xem là có bằng chứng ban đầu khi đồng thời:

- Ít nhất 100 trade tổng và ít nhất 20 trade mỗi segment; thiếu mẫu là
  `INCONCLUSIVE`, không phải PASS.
- Net PnL sau toàn bộ chi phí dương, profit factor lớn hơn 1 và max drawdown không
  vượt giới hạn hệ thống.
- Ít nhất 3/4 segment có net PnL dương.
- Win rate phải được báo kèm confidence interval và break-even win rate để chẩn
  đoán payoff, nhưng không thay thế net portfolio return objective.
- Không có trade vi phạm causal swing, horizon, risk budget hoặc reconciliation.

Nếu 180 ngày chưa đủ 100 trade thì mở rộng dataset lên 365 ngày, không hạ tiêu
chuẩn số mẫu chỉ để có kết luận.

#### Gate 4 — Paper test bằng state riêng

Chỉ chạy sau khi các Paper blocker phía trên hoàn thành. Không dùng DB Champion:

```bash
SCORING_PROFILE=support_resistance_only \
STRATEGY_LABEL=SR_PAPER \
DB_PATH=data/state_sr_paper.db \
LOG_PATH=logs/sr_paper.log \
  .venv/bin/python -m src.run
```

Effective config phải hiển thị đúng profile và mọi event/feature/trade phải có
experiment ID cùng manifest hash. Kiểm tra thủ công ít nhất năm trade đầu trên
chart: hai swing tạo zone, confirmation timestamp, ATR, score, entry, SL, TP và
exit reason phải tái dựng được từ log.

Paper test chạy tới khi có ít nhất 30 trade đóng; số mẫu thấp hơn chỉ dùng kiểm
tra vận hành. So sánh distribution score, entry distance, MAE/MFE, hold time,
timeout, win rate và net PnL với offline. Sai khác lớn phải được giải thích bằng
tick/fill/data regime trước khi đưa feature vào Champion.

### Quyết định sau test

- Offline không qua Gate 3: giữ Champion, đóng hoặc thiết kế lại experiment.
- Offline qua nhưng Paper lệch mạnh: sửa parity/data, không chỉnh threshold để
  che sai khác.
- Cả hai đạt: mở experiment mới `Champion + S/R feature` để đo incremental edge;
  không tự đưa toàn bộ S/R score 0–100 vào trọng số Champion.

Metrics bắt buộc:

- Số lệnh và confidence interval của win rate.
- Net PnL sau fee/slippage/funding, profit factor và max drawdown.
- MAE/MFE, thời gian giữ và tỷ lệ timeout.
- Khoảng cách entry/exit tới swing theo ATR.
- Số previous swings/touch count và độ phân tán của zone theo ATR.
- Tỷ lệ support/resistance bị phá sau khi phát tín hiệu.

### Điều kiện hoàn thành

- Live và backtest parity đã đủ để so sánh.
- Flag/profile có hiệu lực và được ghi vào Feature Lineage/experiment manifest.
- Test chứng minh kết quả out-of-sample trên mẫu đủ lớn; không dùng điểm đánh giá chủ quan như 6.5/10 hoặc 9/10 làm bằng chứng.
- Nếu không có incremental edge sau chi phí, đóng experiment và không đưa feature
  vào Champion.
