# Research: Technical Signal có edge thật không? (khung 5m, BTC/USDT)

Ghi lại quá trình điều tra nguyên nhân win rate bất thường thấp (5.79%) khi backtest chiến lược Long ở `docs/tasks.md` (Phase 5, AI Review Backtest), cùng các thử nghiệm tiếp theo. Không phải task đã chốt — đây là nhật ký nghiên cứu, kết luận cuối ở cuối file.

## 1. Vấn đề ban đầu

Backtest 30 ngày BTC/USDT 5m (ngưỡng BUY hạ xuống 55 để có đủ lệnh test, ngưỡng live thật là 70) cho win rate 5.79% trên 121 lệnh — thấp hơn nhiều so với kỳ vọng tối thiểu ~40-50% ngay cả khi "mua random".

## 2. Giả thuyết 1: exit bị check quá sớm (bug logic) — **đã sửa, không phải nguyên nhân chính**

**Phát hiện:** vòng lặp backtest vào lệnh ở bar `i+1`, nhưng ngay vòng lặp kế tiếp đã kiểm tra điều kiện thoát bằng chính indicator của bar `i+1` đó — tức chưa cho lệnh thời gian phát triển. 88% lệnh (Stop loss + Volume + RSI) thoát trong 1-2 bar (5-10 phút).

**Fix đã áp dụng (giữ lại):**
- `config.MIN_HOLD_MINUTES` (mặc định 15 phút) — Stop Loss/Take Profit vẫn kiểm tra ngay từ bar đầu, nhưng rule thoát theo momentum (MACD/RSI/Volume/EMA) chỉ áp dụng sau khi giữ lệnh đủ thời gian này.
- `decision.decide_exit(..., min_hold_satisfied=...)`, `decision.decide_short_exit(..., min_hold_satisfied=...)`.
- Backtest Engine (`engine.py`, `short_engine.py`) tính `min_hold_bars` từ `MIN_HOLD_MINUTES`/timeframe, so với `i - entry_idx`.
- Live (`run.py`, `_min_hold_satisfied()`) tính theo thời gian thực từ `entry_time`.

**Kết quả sau fix:** win rate gần như không đổi (5.79% → 5.17%). Cơ chế thay đổi (Stop Loss hit tăng 41→53, vì lệnh có thời gian đi ngược xa hơn trước khi bị đá), nhưng tổng thể không cải thiện — chứng tỏ đây **không phải nguyên nhân chính**, dù fix vẫn đúng về mặt thiết kế nên giữ lại.

## 3. Giả thuyết 2: "mua đúng đỉnh cục bộ" (lagging indicator)

Phân tích 116 lệnh, bỏ phí để cô lập vấn đề:

| Kiểm tra | Kết quả |
|---|---|
| Win rate thô (trước phí) | 14.65% |
| Return trung bình "thô" (đã trừ slippage, chưa trừ fee) | -0.132% — xem mục 6.2, gross thật (bỏ cả slippage) là -0.033% |
| % lệnh entry xảy ra SAU khi giá đã tăng trong 100 phút trước | **93.1%** |
| % lệnh entry rơi vào regime STRONG_TREND | 87% |
| t-test (return thô khác 0?) | t=-8.15, p<0.0001 |

→ Tín hiệu Long (EMA stack, MACD cross, Supertrend, VWAP breakout) đều là **lagging/trend-confirmation indicator**. Trên khung 5m (nhiễu, đảo chiều nhanh), lúc các tín hiệu đồng thuận thường là lúc trend đã gần kiệt sức → vào lệnh xong mean-revert ngay.

**Kiểm chứng out-of-sample** (chạy lại trên 30 ngày độc lập, 60-90 ngày trước, không chồng lấn):

| | Mẫu gần đây | Mẫu cũ (độc lập) |
|---|---|---|
| Win rate thô | 14.65% | 25.64% |
| Return trung bình thô | -0.132% | -0.115% |
| Win rate sau phí | 5.17% | 7.69% |

Pattern lặp lại ở giai đoạn độc lập — không phải may rủi của riêng 1 mẫu 30 ngày.

## 4. Câu hỏi phản biện: "đảo thành Short có ăn ~90% không?"

**Trả lời:** về mặt số học có (85.34%), nhưng đây **không phải bằng chứng độc lập** — chỉ là phép tính đối xứng trên CÙNG 116 lệnh (nếu Long thua X%, đảo dấu chính X% đó tự động thắng 100-X%). Không chứng minh gì thêm.

**Test đúng cách: xây chiến lược Short RIÊNG** (bộ điều kiện bearish riêng — `technical.score_short_from_indicators`, `decision.decide_short_entry/exit`, `risk.compute_short_position_plan`, `backtest/short_engine.py` — entry/exit timing hoàn toàn khác Long, không phải đảo dấu):

| | Long (mẫu mới) | Long (mẫu cũ) | Short (mẫu mới) | Short (mẫu cũ) |
|---|---|---|---|---|
| Số lệnh | 116 | 78 | 101 | 150 |
| Win rate thô | 14.65% | 25.64% | 25.74% | 34.67% |
| Return TB thô | -0.132% | -0.115% | -0.145% | -0.051% |
| Win rate sau phí | 5.17% | 7.69% | 13.86% | 16.0% |
| Total return sau phí | -32.05% | — | -29.48% | -31.47% |

**Kết luận quan trọng:** Short **cũng thua** ở cả 2 giai đoạn (dù đỡ tệ hơn Long). Đây là bằng chứng thật (2 chiến lược độc lập, 2 giai đoạn độc lập) rằng **kết quả âm không đến từ việc chọn sai hướng giao dịch** — cả 2 hướng đều thua. Mức độ edge thật của từng hướng được đo riêng ở mục 6.2 (kết quả: ≈ 0, không phải âm rõ rệt).

## 5. Giả thuyết 3: Stop Loss quá hẹp — **đã loại**

Test lại với `ATR_STOP_MULTIPLIER` từ 1.5 → 3.0 (nới gấp đôi, R:R giữ nguyên 1.5):

| | SL 1.5x ATR | SL 3.0x ATR |
|---|---|---|
| Long — win rate | 5.17% | 5.5% |
| Long — số lệnh chạm SL | 53 | 19 |
| Long — win rate thô | 14.65% | 14.68% (không đổi) |
| Short — win rate | 13.86% | 14.29% |

Số lệnh chạm SL giảm mạnh (53→19) nhưng các lệnh đó chỉ chuyển sang thoát bằng cửa khác (momentum-decay) và vẫn thua — win rate thô gần như không đổi. **Stop Loss chưa bao giờ là nút thắt**, chỉ là nơi thua lỗ hiện ra, không phải nguyên nhân.

`ATR_STOP_MULTIPLIER`/`RISK_REWARD_RATIO` đã chuyển ra `config.py`/`.env` (trước đó hard-code) để tiện thử các mức khác sau này.

## 5b. Giả thuyết 4: do đặc thù dữ liệu OKX — **đã loại**

Chạy lại đúng backtest (Long + Short, cùng 30 ngày, threshold 55/45) trên dữ liệu **Binance** (`market.get_binance_exchange()`) thay vì OKX, để loại khả năng vấn đề nằm ở feed/cách tính OHLCV riêng của 1 sàn:

| | OKX | Binance (cùng giai đoạn) |
|---|---|---|
| Long — win rate thô (trước phí) | 14.65% | 19.01% |
| Long — return TB thô | -0.132% | -0.132% |
| Long — win rate sau phí | 5.17% | 6.61% |
| Long — total return | -32.05% | -33.17% |
| Short — win rate thô | 25.74% | 24.47% |
| Short — return TB thô | -0.145% | -0.125% |
| Short — win rate sau phí | 13.86% | 13.83% |
| Short — total return | -29.48% | -26.39% |

Số liệu gần như trùng khớp giữa 2 sàn độc lập (return trung bình thô của Long giống tới 3 chữ số thập phân giữa OKX và Binance). **Loại bỏ khả năng do đặc thù dữ liệu của riêng 1 sàn** — vấn đề nằm ở chính bộ tín hiệu Technical.

## 6. Chẩn đoán tách nguồn (`scripts/diagnose_backtest.py`) — xác định nguyên nhân thật

Các giả thuyết trên đều dùng chung một chỉ số tổng hợp (win rate sau phí), nên không phân biệt được lỗ đến từ tín hiệu, từ rule thoát, hay từ chi phí. Script chẩn đoán tách rời 4 phép đo độc lập trên cùng một tập dữ liệu cache (BTC/USDT 5m, 8640 bar, threshold 55/45):

- **A. Edge thuần của tín hiệu** — forward return kể từ giá fill, không exit rule, không chi phí, so với baseline toàn bộ bar.
- **B. Ảnh hưởng exit rule** — cùng tập entry, chạy với đủ rule / chỉ SL-TP / thoát cố định sau N bar.
- **C. Ảnh hưởng chi phí** — mỗi lệnh log 3 mức PnL: gross (không phí, không slippage) / sau slippage / sau đủ phí.
- **D. Baseline entry ngẫu nhiên** — cùng số lệnh, cùng exit rule, entry random.

### 6.1. Chi phí khứ hồi lớn hơn cả khoảng cách Take Profit

| Đại lượng | Giá trị |
|---|---|
| Chi phí khứ hồi (fee 0.1%×2 + slippage 0.05%×2) | **0.30%** |
| Biến động thân nến 5m (median) | 0.044% |
| ATR (median, % giá) | 0.11% |
| Khoảng cách TP tới entry (median) = 2.25×ATR | **0.248%** |
| Khoảng cách SL tới entry (median) = 1.5×ATR | 0.166% |
| % lệnh Long có khoảng cách TP **nhỏ hơn** chi phí | **66.7%** |
| % lệnh Long có khoảng cách SL nhỏ hơn chi phí | 90.6% |

Đây là nguyên nhân trực tiếp: với 2/3 số lệnh, kịch bản đẹp nhất — giá chạy đúng tới Take Profit — **vẫn lỗ sau chi phí**. Kiểm chứng trên chính các lệnh chạm TP: 8 lệnh Long chạm TP có PnL net trung bình -0.003%, trong đó 4 lệnh lỗ. Ví dụ một lệnh chạm TP: giá chạy +0.275% gross, trừ 0.30% chi phí → -0.025%.

Nguồn gốc: `risk.compute_position_plan` đặt SL/TP thuần theo ATR (`ATR_STOP_MULTIPLIER`, `RISK_REWARD_RATIO`) mà **không có ràng buộc sàn nào so với chi phí giao dịch**. Trên khung 5m BTC, ATR ≈ 0.11% giá — biên lợi nhuận mục tiêu nhỏ hơn chi phí. Cùng công thức đó trên khung/tài sản biến động cao hơn sẽ không lộ vấn đề, nên lỗi không nằm ở công thức mà ở việc thiếu điều kiện tối thiểu `TP_distance >> cost`.

### 6.2. Edge của tín hiệu ≈ 0, không phải âm

Forward return từ điểm fill, **không** exit rule, **không** chi phí:

| Horizon (bar) | Long tín hiệu | Long baseline | t | Short tín hiệu | Short baseline | t |
|---|---|---|---|---|---|---|
| 3 | -0.022% | +0.000% | -2.11 | +0.002% | -0.000% | 0.14 |
| 12 | -0.026% | +0.001% | -1.37 | +0.010% | -0.001% | 0.47 |
| 48 | -0.046% | +0.003% | -1.48 | +0.042% | -0.003% | 1.07 |

Long lệch âm nhẹ (t ≈ -1.4 đến -2.1, biên có ý nghĩa), Short không lệch (t ≈ 0). Cả hai đều rất nhỏ so với chi phí 0.30% — độ lớn edge chỉ bằng ~1/10 chi phí.

Đối chiếu với baseline entry ngẫu nhiên (cùng exit rule, cùng số lệnh): gross trung bình -0.006% (Long) / +0.002% (Short), so với tín hiệu -0.033% / -0.036%. **Tín hiệu không tách khỏi entry ngẫu nhiên một cách có ý nghĩa.**

Điều này **sửa lại kết luận trước đó** rằng "return thô đã âm trước phí (-0.132%)": con số -0.132% đó tính từ `pnl_pct` cộng lại phí, nhưng slippage 0.05%×2 vẫn còn nằm trong giá entry/exit. Gross thật (bỏ cả phí lẫn slippage) là **-0.033%** — gần bằng 0, không phải một edge âm rõ rệt.

### 6.3. Exit rule không phải nút thắt; win rate tăng theo thời gian giữ lệnh là hiệu ứng số học

| Biến thể exit | Hold TB (bar) | Gross TB | Win rate gross | Win rate net | Total net |
|---|---|---|---|---|---|
| Đủ rule | 3.96 | -0.033% | 33.3% | 5.1% | -32.3% |
| Chỉ SL/TP | 13.07 | -0.066% | 18.7% | 14.3% | -28.4% |
| Cố định 12 bar | 13 | -0.007% | 40.0% | 15.6% | -24.2% |
| Cố định 48 bar | 49 | +0.009% | 40.7% | 22.2% | -14.7% |

Giữ lệnh lâu hơn làm win rate net tăng 5.1% → 22.2% trong khi gross gần như không đổi (≈0). Không phải do chiến lược tốt lên: giữ lâu hơn thì độ lệch chuẩn của return gross lớn hơn, nên tỷ lệ lệnh vượt được ngưỡng chi phí cố định 0.30% tăng theo. Tổng return vẫn âm ở mọi biến thể vì kỳ vọng gross vẫn ≈ 0.

Đây cũng là lời giải cho câu hỏi "sao không phải 50/50": win rate được đo **sau** khi trừ 0.30% chi phí, trong khi thời gian giữ lệnh trung bình chỉ ~20 phút — quãng thời gian BTC chỉ dịch chuyển cỡ 0.04-0.15%. Ngưỡng thắng nằm ở đuôi phân phối, nên tỷ lệ thắng thấp một chiều là kết quả tất yếu, đúng cho **cả Long lẫn Short** — không phải dấu hiệu chọn sai hướng.

## 7. Kết luận tổng hợp

**Đã loại trừ được (không phải nguyên nhân):**
- Bug thoát lệnh quá sớm (có ảnh hưởng nhỏ, đã fix, giữ lại vì đúng thiết kế).
- May rủi của riêng 1 giai đoạn 30 ngày (đã kiểm chứng out-of-sample).
- Chọn sai hướng giao dịch (Short độc lập cũng thua, và edge Short đo riêng ≈ 0).
- Stop Loss quá hẹp (nới gấp đôi không đổi kết quả).
- Đặc thù dữ liệu của riêng 1 sàn (tái hiện gần như y hệt trên Binance).
- Exit rule cắt lệnh sớm (thay bằng thoát cố định 48 bar: gross vẫn ≈ 0).

**Nguyên nhân thật — hai tầng:**

1. **Tầng trực tiếp (quyết định con số win rate):** mục tiêu lợi nhuận mỗi lệnh nhỏ hơn chi phí giao dịch. TP median 0.248% vs chi phí 0.30%; 2/3 số lệnh không thể có lãi kể cả khi chạy đúng kịch bản. Đây là lỗi thiết kế Risk Engine — thiếu ràng buộc giữa khoảng cách TP/SL và chi phí — chứ không phải đặc tính thị trường.
2. **Tầng nền (quyết định việc có edge hay không):** bộ 8 tín hiệu Technical là lagging/trend-confirmation indicator, trên khung 5m cho edge ≈ 0 (không phân biệt được với entry ngẫu nhiên). Sửa tầng 1 sẽ đưa kết quả về ~hoà vốn trừ chi phí, **không** thành có lãi — cần edge thật ở tầng 2.

## 8. Cost gate — đã triển khai

`config.MIN_TP_COST_RATIO` (mặc định 2.5): Risk Engine từ chối entry khi khoảng cách Take Profit nhỏ hơn `k ×` chi phí khứ hồi. `compute_position_plan`/`compute_short_position_plan` trả thêm `tp_distance_pct`, `round_trip_cost_pct`, `edge_viable`, `skip_reason`; Backtest Engine và `run.py` cùng bỏ qua entry khi `edge_viable=False` và đếm/log số lệnh bị chặn.

Chi phí giao dịch chuyển vào `config.FEE_PCT`/`config.SLIPPAGE_PCT` — trước đó hard-code riêng trong Backtest Engine, nếu để 2 nơi thì gate sẽ lọc theo mức chi phí khác với mức thực trừ vào PnL.

### 8.1. Tỷ lệ TP/chi phí quyết định khung thời gian nào dùng được

| Khung | ATR median (% giá) | TP distance median | TP / chi phí | Số lệnh còn lại sau gate k=2.5 |
|---|---|---|---|---|
| 5m | 0.111% | 0.248% | **0.83×** | 0 / 296 |
| 15m | 0.250% | 0.563% | 1.88× | 18 / 82 (Long) |
| 1h | 0.557% | 1.252% | **4.17×** | 63 / 80 (Long) |

Trên 5m, gate lọc sạch 100% lệnh — không phải gate quá chặt mà vì **không có lệnh 5m nào thoả mãn được điều kiện tối thiểu**. Đây là kết luận đóng cho khung 5m với bộ chi phí hiện tại: mọi cấu hình entry/exit đều nằm dưới ngưỡng hoà vốn.

### 8.2. Win rate phục hồi theo khung thời gian — xác nhận cơ chế ở mục 6.3

Cùng một chiến lược, chỉ đổi khung (Long, không gate):

| Khung | Win rate net | Gross TB | Total net |
|---|---|---|---|
| 5m | 5.1% | -0.033% | -32.3% |
| 15m | 17.1% | -0.007% | -22.3% |
| 1h | 31.0% | **+0.077%** | -14.9% |

Win rate tăng 5% → 31% mà không đổi một dòng logic tín hiệu nào. Xác nhận win rate thấp bất thường là hệ quả của tỷ lệ chi phí/biến động, không phải chất lượng tín hiệu.

Khung 1h + gate k=2.5: 63 lệnh, win rate 33.3%, gross **dương** (+0.10%) — lần đầu tiên tín hiệu có kỳ vọng gross dương. Nhưng total return vẫn -12.07% vì gross +0.10% vẫn nhỏ hơn chi phí 0.30%.

### 8.3. Hệ quả cần biết khi vận hành

- Với `TIMEFRAME=5m`, bot sẽ **không vào lệnh nào** (mọi tín hiệu bị `RISK_REJECTED` bởi cost gate). Đây là hành vi đúng theo thiết kế — từ chối các setup không thể có lãi — nhưng nghĩa là muốn bot hoạt động phải chuyển sang khung dài hơn.
- Short trên 1h có gross âm rõ (-0.13%, không phải ≈0 như 5m) — bộ tín hiệu bearish không dùng được ở khung này.

## 9. Kết luận và hướng xử lý theo thứ tự ưu tiên

1. ~~Thêm ràng buộc chi phí vào Risk Engine~~ — **đã làm** (mục 8). Kết quả: loại bỏ toàn bộ lệnh 5m không thể lãi, đưa total return từ -32% về -1.5% (vì gần như không giao dịch).
2. **Chuyển sang khung 1h** — điều kiện cần để gate không chặn hết lệnh, và là khung duy nhất cho gross dương (Long). Cần chốt `TIMEFRAME` mới rồi chạy lại toàn bộ calibrate ngưỡng BUY trên khung đó.
3. **Thu hẹp chi phí giao dịch**: gross tốt nhất hiện tại (+0.10%/lệnh trên 1h) vẫn chỉ bằng 1/3 chi phí 0.30%. Dùng maker order thay taker (fee 0.1% → ~0.02%) đưa chi phí khứ hồi về ~0.14% — riêng thay đổi này đã đủ lật dấu kỳ vọng trên 1h. Đây là đòn bẩy lớn hơn mọi cải thiện tín hiệu ở quy mô hiện tại.
4. Thêm điều kiện pullback trước entry (mua khi giá hồi trong trend, không mua ngay lúc breakout xác nhận).
5. Test với đầy đủ 6 lớp tín hiệu thật (cần tích luỹ Feature Store qua thời gian chạy `run.py`) thay vì chỉ Technical+Regime.

## Code đã tạo trong quá trình nghiên cứu

- `src/backtest/short_engine.py` — Backtest Engine cho Short (thử nghiệm, **chưa dùng trong Rule Engine live**).
- `src/indicators/technical.py` — `score_short_from_indicators()`, `_detect_bearish_pattern()`.
- `src/engine/decision.py` — `decide_short_entry()`, `decide_short_exit()`.
- `src/engine/risk.py` — `compute_short_position_plan()`, `compute_short_pnl_pct()`.
- `src/engine/decision.py` — `decide_exit()`/`decide_short_exit()` nhận thêm `min_hold_satisfied`.
- `src/config.py` — `MIN_HOLD_MINUTES`, `ATR_STOP_MULTIPLIER`, `RISK_REWARD_RATIO` (tách khỏi hard-code).
- `scripts/run_short_backtest.py` — CLI chạy backtest Short.
- `src/config.py` — `FEE_PCT`, `SLIPPAGE_PCT`, `MIN_TP_COST_RATIO` (cost gate, mục 8).
- `src/engine/risk.py` — `round_trip_cost_pct()`, plan trả thêm `edge_viable`/`skip_reason`/`tp_distance_pct`.
- `src/backtest/engine.py`, `src/backtest/short_engine.py`, `src/run.py` — áp cost gate, đếm/log `n_skipped_cost_gate`.
- `scripts/diagnose_backtest.py` — công cụ chẩn đoán tách nguồn thua lỗ (mục 6) + quét ngưỡng `MIN_TP_COST_RATIO` (mục E): edge thuần của tín hiệu / ảnh hưởng exit rule / ảnh hưởng chi phí / baseline ngẫu nhiên. Log chi tiết từng lệnh (MFE/MAE theo ATR, khoảng cách TP/SL, 3 mức PnL, score, regime) ra `data/diagnostics/*_trades.csv`; cache OHLCV để các kịch bản so sánh trên cùng tập dữ liệu.
