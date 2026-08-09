# Kết quả Backtest

File này là SSOT cho kết quả thử nghiệm đã chạy. Các task còn mở nằm trong
`todo.md`; contract ổn định nằm trong `decisions.md`.

## Dataset chuẩn

- Nguồn: OKX Spot `BTC/USDT`.
- Khoảng dữ liệu: 30 ngày kết thúc ngày 2026-08-07.
- Primary: 8.640 nến 5m; tick proxy: 43.200 nến 1m.
- Execution path: OHLC 1m adverse-first.
- Chi phí: fee và slippage theo manifest/config tại thời điểm chạy.
- Artifact dataset: `data/backtests/btc_usdt_spot_5m_1m_30d.json.gz`.

## S/R V4 Long — baseline

Artifact: `data/backtests/sr_30d_latest.json`.

| Metric | Kết quả |
|---|---:|
| Điểm score timeline | 166.081 |
| Lệnh | 21 |
| Win / Loss | 0 / 21 |
| Win rate | 0% |
| Net PnL | -$39,6522 |
| Net return | -7,9304% |
| Profit factor | 0 |
| Max drawdown | 7,9304% |

Cả 21 lệnh đóng bằng stop loss. SL cách entry trung vị 0,294 ATR (0,0385%);
16/21 lệnh thoát trong 5 phút và 16/21 trong 15 phút. Có 16/21 entry xảy ra
tại bước LOW của OHLC 1m, cho thấy entry chưa chờ bounce/reclaim.

Kết quả này đã chạy lại sau khi replay xử lý tick còn lại trong cùng sub-bar;
7/21 lệnh exit ngay trong primary bar chứa entry và hold trung vị còn 2 phút.

## Fixed-entry SL sensitivity

Artifact: `data/backtests/sr_sl_sweep_fixed_entries.json`.

Contract: giữ nguyên 21 entry và TP của baseline, chỉ replay tối đa 24 giờ sau
entry; tắt SELL_SCORE để cô lập hard-SL. Quét từng 0,1 ATR từ 0,2–50 ATR. Size
tính lại theo risk budget và giới hạn Spot; mỗi trade reset equity $500. Đây là
sensitivity test, không phải portfolio backtest vì các entry có thể chồng nhau.

| SL dưới zone | Win rate | TP / SL / Timeout | Net PnL | Hold trung vị |
|---:|---:|---:|---:|---:|
| 0,2 ATR | 0% | 0 / 21 / 0 | -$41,2281 | 2 phút |
| 1,0 ATR | 14,29% | 3 / 18 / 0 | **-$34,1628** | 18 phút |
| 1,4 ATR | 19,05% | 4 / 17 / 0 | -$34,9989 | 34 phút |
| 5,0 ATR | 33,33% | 5 / 14 / 2 | -$55,2469 | 147 phút |
| 9,6 ATR | 52,38% | 9 / 10 / 2 | -$45,4278 | 457,25 phút |
| 12,2 ATR | **57,14%** | 10 / 7 / 4 | -$37,9651 | 607,25 phút |
| 50 ATR | 57,14% | 10 / 0 / 11 | -$4,7528 | 1.400,75 phút |

Trong dải thực dụng 0,2–10 ATR, 1,0 ATR ít lỗ nhất. Win rate đạt trần 57,14%
lần đầu tại 12,2 ATR nhưng không có mốc nào dương sau chi phí. Mốc cực xa chủ
yếu loại bỏ SL, kéo lệnh gần timeout và giảm size; không phải candidate deploy.

## TP Long làm entry Short

### Negative control — một support target

Artifact: `data/backtests/sr_tp_as_short_entry.json`.

Entry Short đặt tại TP Long; SL trên swing high nguồn + 0,2 ATR; TP quay về
support cũ bằng direct/Fibonacci và phải qua cost/R:R gate. Cả 21/21 plan bị
từ chối vì không có Fibonacci target vừa nằm dưới entry vừa đủ cost/R:R. Không
có trade; đây là kết quả hợp lệ của gate, không phải lỗi runtime.

### Mirror V4 — multi-low target

Artifact: `data/backtests/sr_tp_as_short_entry_v2_multilow.json`.

Contract:

- TP Long trở thành entry Short nếu được chạm trong 24 giờ sau setup Long.
- SL nằm trên swing high nguồn + 0,2 ATR.
- Tại entry Short, tìm confirmed swing low còn hiệu lực theo thứ tự gần đến xa;
  target xa dùng Fibonacci đầu tiên đủ chi phí và R:R tối thiểu 1,5.
- Short replay OHLC 1m theo thứ tự adverse-first `open → high → low → close`;
  exit bằng SL, TP hoặc timeout 24 giờ.

| Gate/Kết quả | Số lượng |
|---|---:|
| Tổng setup Long nguồn | 21 |
| Không chạm entry Short trong 24 giờ | 11 |
| Chạm entry nhưng plan bị cost/R:R reject | 6 |
| Short được mở | 4 |
| TP / SL / Timeout | 1 / 3 / 0 |
| Win rate độc lập | 25% |
| Net PnL độc lập | -$9,0533 |

Khi ép portfolio tối đa một vị thế và cooldown 30 phút: 3 lệnh, 1 win, 2 loss,
win rate 33,33%, net PnL -$5,9127, profit factor 0,336. Mẫu quá nhỏ và expectancy
âm; chiến lược không qua gate để triển khai Paper.

## Event study Support touch và Reclaim — 180 ngày

Artifact: `data/backtests/sr_event_study_180d.json`.

Contract không đặt lệnh/TP/SL: lấy first touch của mỗi confirmed two-swing zone,
đo forward return và MAE/MFE 5m/15m/1h/4h/24h. Control là non-touch bar gần nhất
có cùng EMA trend, ATR quintile và 4h UTC bucket, cách event tối thiểu 24 giờ.
Reclaim yêu cầu nến 1m bullish đóng trên zone high + 0,1 ATR trong 15 phút và so
với đúng control ghép cặp của parent touch.

| Sample | N | Mean 1h | Control 1h | Lift 1h | Mean 4h | Lift 4h | Mean 24h | Lift 24h |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Touch | 559 | -0,0907% | +0,0291% | -0,1198% | -0,0648% | -0,0653% | -0,1549% | -0,1181% |
| Touch → reclaim | 393 | -0,0090% | +0,0327% | -0,0418% | -0,0023% | -0,0013% | -0,1024% | -0,0788% |

Touch có forward-mean lift âm ở toàn bộ horizon. Reclaim cải thiện touch nhưng
vẫn kém matched control ở toàn bộ forward mean; lift xác suất MFE vượt hurdle
chỉ dương nhẹ ở 4h/24h và không chuyển thành return. Mean-reversion S/R-only bị
đóng theo gate; không implement reclaim entry hoặc tiếp tục tuning SL/TP.

## Khám phá entry Price Action — 30 ngày

Artifacts:

- `data/backtests/entry_patterns_30d.json`
- `data/backtests/entry_patterns_pre_discovery_150d.json`

Contract: Long Spot, signal chỉ dùng nến 5m đã đóng, fill tại open nến kế tiếp,
tick proxy 1m và không tối ưu TP/SL. So sánh confirmed resistance breakout,
rolling 4h-high breakout, hai biến thể breakout→retest và EMA20
pullback→reclaim. Matched control cùng EMA trend, ATR quintile và 4-hour UTC
bucket. Chi phí khứ hồi là 0,30%; trade hurdle là 0,75%.

30 ngày được chia theo thời gian thành hai nửa. Không rule nào giữ đồng thời
forward return tuyệt đối dương, lift dương và khả năng bù chi phí qua các
horizon. Kết quả 24h toàn bộ mẫu:

| Entry pattern | N | Mean return | Lift vs control | MFE ≥ 0,75% |
|---|---:|---:|---:|---:|
| Rolling 4h-high breakout | 45 | -0,235% | +0,247 điểm % | 51,11% |
| Confirmed resistance breakout | 37 | -0,116% | +0,001 điểm % | 54,05% |
| EMA20 pullback→reclaim | 93 | -0,006% | +0,472 điểm % | 58,06% |
| Confirmed resistance breakout→retest | 15 | +0,465% | +0,496 điểm % | 73,33% |
| Rolling 4h-high breakout→retest | 42 | +0,130% | -0,214 điểm % | 64,29% |

Confirmed resistance breakout→retest là candidate duy nhất có mean 24h dương
ở cả hai nửa 30 ngày, nhưng chỉ có 10/5 mẫu đủ horizon. Kiểm định độc lập trên
150 ngày ngay trước đó cho 92 mẫu: mean return 24h `-0,186%`, lift
`-0,175 điểm %`, 40,22% đóng trên cost 0,30%. Median MFE/MAE lần lượt
`+1,301% / -1,190%`; có chuyển động nhưng hướng và thời điểm exit không ổn định.

Các filter khám phá EMA50>EMA200, ADX≥25, volume≥1×, retest trong 30 phút và tổ
hợp của chúng đều không tạo mean net return dương trên 150 ngày. Kết luận:
price-only 5m không qua gate; không implement entry rule và không đổi Paper.

## Bottom-up discovery từ đáy 5m — 180 ngày

Artifact: `data/backtests/bottom_entry_rules_180d.json`.

Contract: potential bottom causal khi low hiện tại tạo low mới so với ba nến
trước. Local bottom hồi cứu cần thấp hơn ba nến sau; future chỉ dùng tạo label.
Entry tại open nến 5m kế tiếp, SL dưới signal low `0,20 ATR`, TP cách entry bằng
mức lớn hơn giữa `0,75%` và `1,5 × risk`, horizon 24 giờ, chi phí khứ hồi 0,30%.
Dữ liệu chia train/validation/test theo thời gian và purge một horizon ở biên.

Dataset có 15.803 potential bottom, 4.989 local bottom hồi cứu và 1.546 good
bottom chạm TP trước SL. Nếu entry ngay sau potential-bottom bar, kết quả lần
lượt theo train/validation/test:

| Segment | N sau cooldown | Win rate | Mean net return | Profit factor |
|---|---:|---:|---:|---:|
| Train | 2.260 | 12,74% | -0,292% | 0,166 |
| Validation | 553 | 11,39% | -0,297% | 0,147 |
| Test | 557 | 10,77% | -0,276% | 0,149 |

Đã thử cây quyết định nông theo nhóm reversal, trend/momentum,
structure/volatility, feature chuẩn hóa regime và toàn bộ feature; không có leaf
train nào vừa đủ mẫu vừa có expectancy dương với structural SL. Random forest
chọn top 1% đạt 68,82% win/PF 1,30 trên train nhưng giảm còn 45,45%/PF 0,56 ở
validation và 25%/PF 0,25 ở test.

Thử entry sau khi swing low được xác nhận bởi 1/2/3/5/8 nến. Cửa sổ tốt nhất
trên train là 8 nến (40 phút), nhưng train/validation/test chỉ đạt lần lượt
27,16% / 31,30% / 27,79%; mean net return `-0,332% / -0,272% / -0,253%`.
Random forest chọn lọc confirmed bottom đạt 97,62% trên 42 train events nhưng
chỉ còn 20% trên 5 validation events và 2 events ở test: overfit và thiếu
coverage.

Kết luận: OHLCV 5m đơn khung chưa đủ suy ra rule nhận diện đáy có thể giao dịch
sau chi phí và structural SL. Không promote threshold/model, không đổi Paper;
tiếp tục quét điều kiện trên cùng dataset sẽ là data mining.

## CHOCH, liquidity sweep và trigger library — 180 ngày

Artifacts:

- `data/backtests/choch_entry_180d.json`
- `data/backtests/choch_fib_limit_entry_180d.json`
- `data/backtests/liquidity_sweep_entry_180d.json`
- `data/backtests/entry_rule_library_180d.json`

Confirmed swing low→CHOCH direct có 881/218/216 events theo
train/validation/test, win rate `32,92% / 33,49% / 31,02%`, mean net
`-0,325% / -0,285% / -0,251%`. Retest, volume, context EMA 1h/4h, filter logic,
cây nông và random forest không giữ expectancy dương ngoài mẫu. RF direct đạt
88,33% train nhưng chỉ 30% validation và 50% test với 10/8 events: overfit.

BUY LIMIT Fibonacci sau CHOCH không cải thiện. Mức 0,382 tốt nhất trong ba mức
nhưng win rate chỉ `28,01% / 33,01% / 25,38%`, mean net vẫn âm ở cả ba segment;
limit sâu hơn tăng tỷ lệ fill khi giá tiếp tục giảm.

Liquidity sweep/reclaim đáy rolling 1h/4h/24h đều âm. Nhánh 24h tốt nhất trên
train đạt 38,78% nhưng giảm còn 31,58% validation và 17,39% test. Volume filter
không khắc phục.

Thư viện fixed-barrier `TP +0,75% / SL -0,50%` đã thử RSI30/40 reclaim, EMA20/50
cross, MACD cross, Supertrend flip, VWAP reclaim, bullish engulfing,
volume-wick reversal, squeeze breakout, trend pullback và three-bar reversal,
kèm context 1h/4h. Không tổ hợp nào có mean net dương đồng thời trên ba segment.
Đóng đúng 24h không SL cũng không tạo rule dương ổn định sau chi phí.

Kết luận: nguyên nhân không chỉ là SL; OHLCV không cho directional edge đủ lớn
so với cost 0,30%. Nghiên cứu chuyển sang microstructure append-only, không tiếp
tục tuning cùng dataset và không đổi Paper strategy.

## Historical taker-flow spot/futures — 180 ngày

Artifacts:

- `data/backtests/binance_btcusdt_spot_5m_flow_180d.json.gz`
- `data/backtests/binance_btcusdt_um_5m_flow_180d.json.gz`
- `data/backtests/historical_taker_flow_ablation_180d.json`
- `data/backtests/historical_um_taker_flow_ablation_180d.json`
- `data/backtests/spot_futures_flow_divergence_180d.json`

Đã tải 51.840 nến 5m cho mỗi market từ Binance Public Data, kiểm checksum từng
archive và align causal candle vừa đóng với toàn bộ event CHOCH, Fibonacci,
liquidity sweep và technical trigger. Coverage event đạt 100%.

Các gate đã thử gồm imbalance hiện tại/3/12 nến, flow đổi từ bán sang mua, buy
flow mạnh, volume confirmation, sell absorption, recovery, đồng thuận spot và
futures, spot/futures lead, divergence, basis discount và high-volume joint
flow. Không tổ hợp nào đạt đồng thời mean net dương, profit factor trên 1 và tối
thiểu 20 mẫu ở cả train/validation/test.

Kết quả gần nhất với hòa vốn là bullish engulfing + spot sell-absorption có
volume: train/validation/test có `143 / 41 / 42` mẫu, win rate
`44,76% / 51,22% / 54,76%`, nhưng mean net vẫn
`-0,241% / -0,160% / -0,093%` và profit factor dưới 1. CHOCH retest + spot dẫn
buy-flow đạt `101 / 40 / 24` mẫu, mean net
`-0,238% / -0,139% / -0,162%`.

Kết luận: taker-flow 5m đơn lẻ hoặc divergence spot/futures chưa bù được cost và
không được promote. Bước kế tiếp là thêm context derivatives độc lập gồm funding,
open interest và long/short positioning; order-book L2 live vẫn tiếp tục thu để
kiểm định sau khi đủ coverage.

## Derivatives context, wide horizon và multivariate model — 180 ngày

Artifacts:

- `data/backtests/binance_btcusdt_derivatives_context_180d.json.gz`
- `data/backtests/derivatives_context_ablation_180d.json`
- `data/backtests/wide_horizon_entries_180d.json`
- `data/backtests/trailing_breakout_entries_180d.json`
- `data/backtests/ma_crossover_entries_180d.json`
- `data/backtests/multivariate_entry_model_180d.json`

Funding/OI/positioning gồm 522 kỳ funding và 51.838 futures metrics 5m, lấy từ
192 archive có kiểm checksum. Các gate funding, OI change, taker ratio,
top-trader và global long/short đều không qua. Candidate tốt nhất là EMA20/50
cross + OI tăng ba nến nhưng train/validation/test vẫn lần lượt
`-0,239% / -0,127% / -0,188%`, PF `0,456 / 0,656 / 0,539`.

Đã mở TP `1–5%`, SL `0,75–3%`, horizon `1/3/7 ngày` cho breakout, momentum,
pullback, RSI/Bollinger mean-reversion và hai phía LONG/SHORT. Parameter chỉ
được chọn trên train. Không candidate nào qua holdout. RSI20 reclaim đạt
`+0,547%`, PF 1,48 trên train nhưng validation/test là
`-0,500% / -0,861%` và thiếu mẫu. Fixed TP không được promote.

ATR chandelier `4–20 ATR`, max hold đến 14 ngày và EMA crossover/opposite-exit
cũng âm. SHORT Donchian-12 + downtrend + trailing 20 ATR dương train
`+0,132%`, PF 1,15 nhưng validation/test âm `-0,383% / -0,287%`.

LightGBM 24h dùng feature stationary từ OHLCV, spot/futures taker-flow,
funding/OI/positioning và rolling percentile causal. Model tĩnh adaptive LONG/
SHORT đạt mean net `+0,733% / +0,141% / -0,256%`, PF
`2,60 / 1,21 / 0,63` trên train-eval/validation/test. Walk-forward retrain làm
validation/test thành `-0,137% / -0,418%`. Vì test fail, không promote model.
Target fixed-close 24h được đóng; vòng kế tiếp dùng triple-barrier target để
khớp trực tiếp TP-before-SL.

## Triple-barrier model và pullback limit — 180 ngày

Artifacts:

- `data/backtests/triple_barrier_entry_model_180d.json`
- `data/backtests/model_pullback_limit_entry_180d.json`
- `data/backtests/binance_ethusdt_spot_5m_flow_180d.json.gz`
- `data/backtests/binance_ethusdt_um_5m_flow_180d.json.gz`

Classifier dùng label TP-before-SL adverse-first, side LONG/SHORT, feature
stationary và rolling threshold causal. Candidate gần gate nhất là regime theo
return 24h, TP 2%, SL 1,5%, rolling 7 ngày percentile 50: có `26 / 27 / 27`
lệnh và mean net `+0,259% / +0,303% / -0,080%`, PF
`1,44 / 1,54 / 0,86`. Test vẫn âm sau cost 0,30%, nên reject.

Raw LONG/SHORT probability bị lệch scale và chọn 25 SHORT/1 LONG trong test dù
BTC tăng 4,49%. Đã thử per-side calibration, regime direction theo return
24h/7 ngày và EMA50–200, static cùng walk-forward retrain. Không variant nào
qua cả ba segment. ETH spot/futures relative return, basis và taker-flow cũng
không khắc phục test.

Pullback limit thấp/cao hơn signal close `0,25–1%`, chờ `60–240 phút`, TP 3%,
SL 2% được mô phỏng cả thời gian pending và lifecycle sau fill. Train chọn limit
0,5%, chờ 60 phút, EMA50–200 regime: `25 / 18 / 14` fills, mean net
`+0,733% / -1,218% / -0,243%`, PF `2,42 / 0,12 / 0,71`. Entry fill tốt hơn
nhưng directional edge vẫn không ổn định; không promote.

Kết luận 180 ngày: chưa tìm được entry đạt gate. Candidate triple-barrier đã đủ
cụ thể để đem sang lịch sử cũ hơn theo frozen contract; mở rộng dữ liệu từ đây
là falsification trên regime độc lập, không phải tiếp tục tìm rule mù.

## Frozen candidate trên lịch sử cũ và label 1m

Artifacts:

- `data/backtests/binance_btcusdt_spot_5m_flow_2y.json.gz`
- `data/backtests/binance_btcusdt_um_5m_flow_2y.json.gz`
- `data/backtests/binance_btcusdt_derivatives_context_2y.json.gz`
- `data/backtests/frozen_entry_block_1.json`
- `data/backtests/frozen_entry_block_2.json`
- `data/backtests/frozen_entry_block_3.json`
- `data/backtests/frozen_entry_1m_labels_180d.json`

Dữ liệu 2 năm có 210.240 nến/flow mỗi market, 2.172 funding records và 210.168
metrics snapshots. Frozen contract TP 2%, SL 1,5%, return-24h regime, rolling
7 ngày percentile 50 được chạy trên ba block 90 ngày cũ, không re-select
parameter. Không block nào qua gate; mean net static theo từng train/validation/
test block lần lượt là:

- block 1: candidate train-selected khác; frozen family không tạo pass và test
  tốt nhất vẫn `-0,913%` ở raw selection;
- block 2: `-0,132% / -0,431% / -0,025%`;
- block 3: `-0,184% / -0,017% / -0,174%`.

Rerun frozen contract trên đường giá 1m của 180 ngày cho kết quả
`+0,351% / -0,096% / -0,308%`, PF `1,75 / 0,86 / 0,58`, mỗi segment 27 lệnh.
Vì label chính xác hơn làm holdout xấu hơn, kết quả gần hòa vốn trên nến 5m là
ambiguity chứ không phải edge. Frozen candidate bị reject hoàn toàn.

Kết luận tại thời điểm hoàn tất nhánh này: không có entry
price/flow/derivatives/model ngắn hạn nào đạt gate. Nguồn feature chưa có
historical proxy là L2 order-book; collector live tiếp tục chạy độc lập.

## PASS — Slow trend pullback 4h trên BTC, dữ liệu 6 năm

Artifacts:

- `data/backtests/binance_btcusdt_spot_5m_flow_6y.json.gz`
- `data/backtests/slow_mean_reversion_entry_6y.json`
- `data/backtests/slow_pullback_stress_6y.json`

Sau khi các family phút thất bại, nghiên cứu chuyển sang setup chậm hơn nhưng
vẫn dựng causal từ nến 5m. Grid chỉ được chọn trên bốn năm đầu; validation và
test mỗi phần một năm. Dataset từ `2020-08-07` đến `2026-08-06`, gồm 630.725
nến 5m; SHA-256 cache:
`8f390445c5e68386b085765ad8bf5938a86b504b1915d04fc1c991d38173451a`.

Contract train-selected:

- aggregate 48 nến 5m đã đóng thành một nến 4h hoàn chỉnh;
- rolling mean/std 30 nến 4h và `z = (close - mean) / std`;
- EMA180 và ATR14 Wilder trên close/OHLC 4h;
- khi flat: LONG nếu `close >= EMA180` và `z <= -2`; SHORT nếu
  `close < EMA180` và `z >= +2`;
- fill tại open nến 4h tiếp theo;
- SL LONG `entry - 8 × ATR`, SL SHORT `entry + 8 × ATR`;
- thoát tại open nến kế tiếp sau khi z quay về/phá 0;
- một vị thế tại một thời điểm, round-trip cost 0,30%.

Kết quả authoritative:

| Segment | Khoảng thời gian | N | Win rate | Mean net/trade | PF | Sum net |
|---|---|---:|---:|---:|---:|---:|
| Train | 2020-08-07 → 2024-08-06 | 62 | 67,74% | +0,472% | 1,330 | +29,266% |
| Validation | 2024-08-06 → 2025-08-06 | 18 | 72,22% | +1,405% | 5,398 | +25,281% |
| Test | 2025-08-06 → 2026-08-06 | 15 | 73,33% | +0,100% | 1,072 | +1,502% |

Rule đạt gate đã chốt: expectancy dương và PF > 1 ở cả ba segment, số mẫu tối
thiểu 8 mỗi segment. Các stop lân cận 3/5 ATR và nhánh không stop cũng dương ở
cả ba segment, nên entry edge không chỉ tồn tại tại đúng một stop value. Stop 5
ATR đặc biệt đạt mean `+0,448% / +1,193% / +0,524%`, PF
`1,30 / 3,25 / 1,54`.

Stress/caveat:

- train-selected stop 8 ATR chỉ còn gần hòa vốn test ở cost 0,40% và âm từ
  cost 0,50%; stop 5 ATR vẫn dương cả ba tới cost 0,70%;
- theo calendar year, stop 8 ATR có hai năm hơi âm; split aggregate sáu năm vẫn
  pass nhưng edge không đều từng năm;
- max additive drawdown train là `-26,78%`; đây là return không position-size/
  compounding, không phải portfolio backtest;
- ETH validation độc lập không pass, nên scope chỉ là BTC;
- trạng thái là `RESEARCH_PASS`, chưa đủ quyền bật Paper. Cần implementation
  parity, position sizing, forward observation và cost/fill monitoring trước
  khi cân nhắc promotion.

## RESEARCH CHAMPION — Staggered pullback theo lợi nhuận portfolio

Contract frozen: 4h từ nến 5m đã đóng, z-score lookback 60, EMA180 direction,
entry `|z|=2,0` tại open nến kế tiếp, exit `|z|=0,5`, SL 5 ATR, tối đa năm
tranche/excursion và cost 0,30%/ticket. Sizing dùng tổng risk 1%/excursion,
capital cap 20% equity/tranche và equity compounding.

| Segment | Tickets | Excursions | Net portfolio | PF | Max DD |
|---|---:|---:|---:|---:|---:|
| Train | 88 | 30 | +4,550% | 2,624 | 1,454% |
| Validation | 18 | 5 | +0,336% | 1,316 | 1,038% |
| Test | 30 | 10 | +1,497% | 3,194 | 0,634% |

Ở cost stress 0,60%, net train/validation/test còn
`+3,935% / +0,189% / +1,172%`; PF `2,364 / 1,171 / 2,566`. Frozen validator
và production reference parity đều pass, trade-for-trade `88/88`, `18/18`,
`30/30`. Artifacts authoritative:

- `data/backtests/staggered_portfolio_profit_champion_9y.json`
- `data/backtests/staggered_portfolio_runtime_parity_9y.json`
- `data/backtests/staggered_portfolio_candidate_stress_9y.json`

Expanded-grid challenger (13.125 configs, selection train-only) đạt train
`+15,10%` nhưng test chỉ `+0,52%`, PF 1,047 ở base cost và âm từ cost 0,40%; bị
reject bởi cost gate. Runtime flag vẫn OFF vì chưa có Paper Swap/two-sided
lifecycle parity và forward observation.

### Accelerated Paper SQLite replay — ba năm

Khoảng `2023-08-07`–`2026-08-07` được chạy liên tục qua cùng production signal,
position sizing, accounting và `state_store` SQLite lifecycle; chỉ market clock
được thay bằng simulated clock. Unit lifecycle pass và E2E dữ liệu thật khớp
`9/9` trade trước khi chạy dài.

Long run xử lý 6.576 nến 4h, khớp production reference `48/48` trade, không có
mismatch/orphan ledger/risk rejection và kết thúc với zero open position. Có 15
excursion, 8 stop exit và 40 mean exit. Equity $500 → $509,13, net `+1,826%`, PF
`2,030`, max drawdown `1,044%`; LONG và SHORT cùng 24 ticket, đóng góp lần lượt
`+$4,25` và `+$4,88`. Win rate 75% chỉ là diagnostic.

- `data/backtests/staggered_paper_replay_3y.json`
- `data/backtests/staggered_paper_replay_3y.db`

## BASELINE — Staggered slow pullback đạt ticket-frequency nhưng lợi nhuận mỏng

Artifacts:

- `data/backtests/frequent_pullback_entry_6y.json`
- `data/backtests/staggered_slow_pullback_6y.json`
- `data/backtests/binance_btcusdt_spot_5m_flow_pre2020_holdout.json.gz`
- `data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz`
- `data/backtests/staggered_slow_pullback_9y.json`
- `data/backtests/staggered_slow_pullback_9y_frozen_validation.json`
- `data/backtests/staggered_slow_pullback_runtime_parity.json`

Family pullback nhanh 1h/2h/4h không đạt đồng thời performance và frequency.
Candidate sáu năm z=±1/năm tranche ban đầu pass split nhưng bị block external
2017–2020 bác bỏ: frequency `10,83` nhưng mean `-0,835%`, PF `0,690`. Dataset
sau đó được ghép thành 942.025 nến 5m từ `2017-08-17` đến `2026-08-06`, SHA-256
`b7c5fccec736651883e347436ba8b5594ac9bb34a82c037bb7d7f0c3e5fbcfb6`.

Candidate chín năm dùng rolling z-score 60 nến 4h, EMA180 direction, entry
z=±0,75 tại open nến sau, tối đa mười fill trong một excursion, SL 5 ATR, exit
sau khi z vượt ±0,25, market fill và cost 0,30% cho từng ticket. Frequency và
performance là hard gate trên train sáu năm. Selection lấy plateau đạt ít nhất
95% mean train tốt nhất, sau đó chọn ATR stop nhỏ nhất; điều này chọn 5 ATR thay
vì 8 ATR dù mean chỉ thấp hơn 0,008 điểm %. Validation một năm và test gần hai
năm không tham gia xếp hạng.

| Segment | Tickets | Avg/30d | Median rolling 30d | Windows 10–20 | Mean net/ticket | PF |
|---|---:|---:|---:|---:|---:|---:|
| Train | 759 | 10,40 | 10 | 50,90% | +0,707% | 1,335 |
| Validation | 142 | 11,67 | 10 | 55,95% | +0,592% | 1,380 |
| Test | 253 | 10,51 | 10 | 56,13% | +0,309% | 1,232 |

Frozen validator rerun khớp artifact, mọi entry dùng signal nến trước/fill open
nến sau, và cap thực tế không vượt mười fill/vị thế. Performance còn dương tới
round-trip cost 0,60%, nhưng test chỉ còn mean `+0,009%`, PF `1,006` tại stress
này. Coverage thực chỉ khoảng `1,73 / 1,97 / 2,04` excursion độc lập/30 ngày.
Với risk setup 1% chia mười tranche, max additive drawdown khoảng
`-5,46% / -1,61% / -1,23%`; additive return tương ứng
`+1,63% / +1,30% / +0,20%`. Ticket-frequency danh nghĩa không đạt gate hiện tại
vì chỉ có khoảng hai excursion độc lập mỗi tháng; economics cấp portfolio cũng
quá mỏng để promote Paper.

Production core `src/engine/staggered_pullback.py` được implement độc lập với
research reference. Full replay chín năm xác nhận feature parity và trade-for-
trade parity: train `759/759`, validation `142/142`, test `253/253`, không lệch
side, timestamp, fill, stop, exit reason, excursion ID hoặc net return. Core có
position sizing 1% risk/excursion chia mười tranche và cap 10% vốn/tranche.
Execution flag mặc định OFF; kết quả này là offline implementation parity,
không chứng minh Spot/Swap live-order parity.

## REJECTED — Trend/sentiment adaptive SL trên BTC+ETH

Artifacts:

- `data/backtests/trend_sentiment_adaptive_risk.json`
- `data/backtests/trend_session_adaptive_risk.json`
- `data/backtests/alternative_fng_history.json`

Contract dùng daily + 4h closed context để chọn LONG/SHORT, 30m/1h execution,
Fear & Greed lịch sử align causal và SL nới theo trend strength trong khi giữ
risk USD cố định. Dataset BTC bắt đầu 2018-02 theo coverage sentiment; ETH tham
gia point-in-time từ 2022-08. Cost round-trip base/stress là `0,14% / 0,30%`.

Pullback/reclaim grid có 128 contract. Sáu contract train dương nhưng không
contract nào đồng thời đạt PF >1,05 và 5–10 entry/tuần. Contract train tốt nhất
đạt `+8,58%`, PF `1,049`, max DD `13,68%`, `2,94` entry/tuần; validation/test
base là `-8,59% / -8,96%`, stress train/validation/test là
`-17,82% / -18,05% / -25,51%`.

Session high-volume momentum grid có 72 contract và không có train candidate.
Frequency nằm trong `0,79–3,16` entry/tuần. Contract duy nhất dương chỉ đạt
`+0,31%`, PF `1,011`, `0,94` entry/tuần. Hai family bị đóng; không promote hoặc
dùng holdout để sửa tham số.

## REJECTED — L2 maker probe và trend-breakout adaptive risk

Artifacts:

- `data/backtests/okx_l2_maker_probe_2024-01-15_q1.json`
- `data/backtests/okx_l2_maker_probe_2024-01-15_grid.json`
- `data/backtests/trend_breakout_adaptive_risk.json`
- `data/backtests/trend_breakout_adaptive_risk_v2.json`
- `data/backtests/trend_breakout_adaptive_risk_v3.json`

Execution probe dùng OKX BTC-USDT-SWAP L2 400-level và public trades cùng ngày.
Order đứng sau displayed queue; cancel không được tính là queue consumption,
trade-through là adverse fill. Ba SELL signal 1h trong downtrend cho touch-maker
fill 2/3 nhưng markout mean 15m/60m `-28,93/-25,33 bps`. Offset 10 bps vẫn
fill 2/3 và markout `-19,51/-15,06 bps`; chưa có execution edge.

Breakout v1 96 contract và v2 60 contract không có train candidate qua base +
stress. V3 search 72 exit/risk geometry trên signal v2 best train; hai contract
qua train gate. Candidate train-selected dùng 30m, breakout lookback 4, buffer
0,1 ATR, minimum daily+4h trend strength 1, adaptive SL 2→4 ATR, R:R 4, hold
24h và cooldown 3h.

| Segment | Entry/tuần | Net base | PF base | DD base | Net stress | PF stress |
|---|---:|---:|---:|---:|---:|---:|
| Train | 5,14 | +43,21% | 1,118 | 14,17% | +12,18% | 1,042 |
| Validation | 9,28 | +19,21% | 1,170 | 10,60% | +7,59% | 1,072 |
| Test | 9,10 | -9,40% | 0,964 | 22,08% | -24,52% | 0,890 |

Test 2024-08-07 trở đi chưa dùng trong selection và bác bỏ candidate. LONG test
compounded `-8,15%`, SHORT `-1,36%`; 2024/2025 âm, 2026 dương nhưng không đủ
cứu toàn split. Không promote và không dùng test này để tune lại.

## REJECTED — Multi-asset trend-breakout portfolio

Artifact: `data/backtests/multiasset_trend_breakout_portfolio_5y.json`.

Universe gồm 15 Binance USDT perpetual với 1h kline và exact funding settlement.
Mỗi timestamp chọn top-N theo trailing dollar volume causal; một position là một
risk episode, không đếm funding/leg. Grid 16 contract chọn train 2021-2023 có
năm survivor. Candidate dùng top-8, max concurrent 1, risk 0,25%, 1h breakout
lookback 4 và sentiment `none`.

| Segment | Episode/tuần | Net base | PF base | DD base | Net stress | PF stress |
|---|---:|---:|---:|---:|---:|---:|
| Train | 5,48 | +6,02% | 1,124 | 3,59% | +3,44% | 1,071 |
| Validation | 6,41 | -2,93% | 0,901 | 6,63% | -4,49% | 0,851 |
| Test | 6,13 | -0,008% | 1,003 | 4,80% | -3,36% | 0,944 |

Frequency và risk gate đạt nhưng validation profit/cost gate fail. Candidate bị
reject, không promote.

## HISTORICAL PASS — Composite BTC trend + funding crowding

Artifacts:

- `data/backtests/multiasset_funding_crowding_5y.json`
- `data/backtests/composite_btc_trend_funding_crowding_5y.json`
- `data/backtests/funding_crowding_runtime_parity_5y.json`
- `data/backtests/funding_crowding_paper_5y.json`
- `data/backtests/funding_crowding_paper_5y.db`

Funding crowding sleeve chọn train-only đạt train/validation dương ở base và
stress nhưng standalone test âm. Ghép 50/50 với frozen BTC Spot trend tạo
composite không rebalance ngầm; episode là unique fast-sleeve signal timestamp.

| Segment | Episode/tuần | Net base | PF base | DD base | Net stress | PF stress |
|---|---:|---:|---:|---:|---:|---:|
| Train | 8,14 | +7,59% | 1,075 | 14,54% | +3,82% | 1,043 |
| Validation | 8,82 | +41,15% | 1,507 | 10,04% | +38,10% | 1,466 |
| Test | 9,18 | +5,90% | 1,057 | 12,44% | +0,66% | 1,016 |

Median trailing 7 ngày là 9 episode ở cả ba split. Tỷ lệ cửa sổ trailing nằm
trong 5–10 episode là `70,68% / 66,94% / 68,63%`; P95 đều là 12. Artifact lưu
cả non-overlapping và daily-trailing distribution để không che burst/gap bằng
average toàn split.

Production parity khớp 2.557/2.557 trades, zero mismatch. Accelerated Paper
SQLite khớp equity reference, 2.557 lifecycle hoàn chỉnh và zero orphan/open
position. Trạng thái là `PAPER_CHALLENGER`; historical holdout đã được quan sát
trong discovery nên cần fresh forward Paper trước promotion. Contract không dùng
news/sentiment feed. Composite fresh forward no-order bắt đầu ngày 2026-08-08:
BTC và funding sleeve có state riêng, equity được reconcile 50/50; scheduler
health-check pass, 1 observed hour, 1 daily observation, 0 closed trade, 0
episode. Promotion còn yêu cầu ≥28 ngày, hourly coverage ≥90%, 30 closed trade,
30 independent episode, frequency 5–10/tuần, net/PF base và stress dương, DD
≤20%; rolling median phải trong 5–10, ít nhất 50% cửa sổ trong dải và tối đa 10%
cửa sổ zero. Live execution OFF.

## DIAGNOSTIC — Frozen composite trên 30 ngày gần nhất, Binance và OKX

Artifacts:

- `data/backtests/recent_composite_venues_30d.json`
- `data/backtests/recent_composite_venues_30d_input.json.gz`

Cửa sổ gồm 30 ngày UTC đã đóng `2026-07-09`–`2026-08-08`, start-flat; 100 ngày
đầu vào được dùng làm warm-up nhưng không tính PnL. Mỗi sàn có đủ 15 perpetual,
tối thiểu 2.400 nến 1h/asset, 120 funding settlement/asset và 100 daily BTC Spot.
Vị thế còn mở được mark tại final close và tính exit cost. Cost giữ nguyên theo
package (`fast 0,07%/0,14%` round trip; BTC turnover `0,12%/0,24%`), không thay
bằng fee tier tài khoản của từng sàn.

| Venue | Base composite / PF / DD | Stress composite / PF / DD | Episode/tuần |
|---|---:|---:|---:|
| Binance | -3,06% / 0,528 / 3,73% | -3,59% / 0,482 / 4,14% | 9,80 |
| OKX | -3,93% / 0,426 / 4,32% | -4,47% / 0,385 / 4,63% | 10,27 |

Binance base: BTC sleeve `-4,83%`, funding sleeve `-1,30%`; 46 positions, 42
episode, win rate 41,30%. OKX base: BTC sleeve `-4,79%`, funding sleeve `-3,08%`;
47 positions, 44 episode, win rate 29,79%. Median hold đều 24h; phần lớn exit là
TIMEOUT (`34/46` Binance, `30/47` OKX). Funding contribution rất nhỏ và dương,
không cứu được price loss. Production core khớp trade-for-trade 45/45 closed
trade trên cả hai venue; phần chênh còn lại là 1/2 vị thế WINDOW_END_MARK.

Kết luận: frequency gần mục tiêu nhưng 30 ngày gần nhất không có edge trên cả
hai sàn; OKX còn vượt nhẹ trần frequency. Đây là recent retrospective diagnostic,
không phải fresh-forward promotion evidence và không dùng để retune package.

## Gross edge của toàn bộ family đã reject

Phép đo này trả lời câu hỏi liệu các kết quả reject có phải do giả định chi phí
quá cao hay không. `gross = mean_net_return_pct + cost` là lợi nhuận khi **miễn
phí hoàn toàn**.

| Family | n cấu hình | Gross p50 | Gross p90 | % gross > 0 |
|---|---:|---:|---:|---:|
| Historical taker flow ablation | 936 | +0,005% | +0,107% | 52,9% |
| Derivatives context ablation | 1.081 | +0,001% | +0,108% | 50,3% |
| Bottom entry rules | 112 | +0,005% | +0,064% | 57,1% |
| Liquidity sweep | 132 | -0,013% | +0,200% | 36,4% |
| CHOCH entry | 98 | -0,021% | +0,155% | 41,8% |
| Trailing breakout | 864 | -0,058% | +0,096% | 29,7% |
| Wide horizon | 9.390 | -0,040% | +0,251% | 39,0% |
| Triple barrier model | 906 | +0,046% | +0,530% | 56,6% |
| Multivariate entry model | 135 | +0,096% | +1,254% | 60,7% |
| **Tổng hợp** | **13.654** | **-0,021%** | +0,250% | **41,8%** |

Trung vị gross bằng không và tỷ lệ dương 41,8% là phân phối của nhiễu quanh
không. Percentile cao là max của mẫu lớn, không phải edge.

Kiểm chứng độc lập từ 942.025 nến 5m thô, tính forward return trước chi phí:

| Tín hiệu | n | Gross forward return @36 bar |
|---|---:|---:|
| EMA cross-up | 13.234 | +0,018% |
| Momentum 1h > +1% | 48.042 | -0,028% |
| Breakout 24h high | 19.376 | +0,051% |
| Taker imbalance > 0,3 | 103.045 | +0,011% |
| Toàn bộ nến (drift nền) | 942.025 | +0,019% |

Edge của các tín hiệu khung phút gần như trùng với drift nền của tài sản.

**Kết luận: hạ chi phí xuống bất kỳ mức nào, kể cả bằng không, không cứu được
các family này.** Hiệu chỉnh cost chỉ đổi kết luận với candidate đã có gross edge
dương thật.

## Gross edge của candidate còn hiệu lực

Staggered slow pullback, split test: gross `+0,609%/ticket`, đã xác nhận độc lập
trên 480/480 điểm grid.

| Round-trip cost | Net/ticket | Cost stress 2× |
|---:|---:|---:|
| 0,300% (giả định cũ) | +0,309% | +0,009% |
| 0,150% (spot taker thật) | +0,459% | +0,309% |
| 0,090% (perp taker thật) | +0,519% | +0,429% |
| 0,190% (perp taker + thuế 0,1%) | +0,419% | +0,229% |

Candidate này từng trượt riêng ở gate cost-stress, nhưng mức stress 0,60% là hai
lần một base cost cao gấp ba lần thực tế. Với chi phí thật, contract qua gate ở
cả base lẫn stress. Giá trị chi phí lấy từ `execution-cost.md`.

Cảnh báo mẫu: 253 ticket của split test gom lại chỉ còn 41 excursion độc lập,
t-stat `0,57`. Toàn bộ edge tập trung vào 2019–2022; 2017–2018, 2023–2024 và
2026 tới nay đều âm. Chạy lại với cost đúng là điều kiện cần, không phải điều
kiện đủ để promote.
