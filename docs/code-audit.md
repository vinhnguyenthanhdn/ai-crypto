# Kiểm định engine và lỗi đã xác nhận

File này là SSOT cho mức độ tin cậy của từng nhánh code và các lỗi đã xác nhận.
Task cần làm nằm ở `todo.md`; kết quả đo nằm ở `backtest-results.md`.

Nguyên tắc: kết quả backtest chỉ được dùng để reject hoặc promote khi engine tạo
ra nó nằm trong nhánh đã kiểm định ở phần dưới.

## Hai nhánh code có độ tin cậy khác nhau

Repo chứa hai đường thực thi độc lập, và chúng **không** cùng mức tin cậy.

**Nhánh research** — `scripts/discover_*.py`, `scripts/analyze_*.py` và replay
riêng của từng contract. Đây là nơi tạo ra toàn bộ artifact trong
`data/backtests/`. Đã kiểm định độc lập và **đạt**.

**Nhánh runtime** — `src/run.py`, `src/backtest/engine.py`,
`src/backtest/paper_engine.py`, `src/indicators/technical.py`. Đây là Rule Engine
và profile S/R đang chạy Paper. Có nhiều lỗi xác nhận; xem phần dưới.

## Kết quả kiểm định nhánh research

Kiểm định bằng cách tính lại từ dữ liệu thô bằng code độc lập, không dùng engine
của repo.

| Hạng mục | Kết quả |
|---|---|
| Cache 9 năm đối chiếu `api.binance.com` | high/low/volume sai số quan hệ 0,000e+00 trên 3.277 ngày |
| Nến thiếu | 1.703/943.728 (0,18%), khớp các lần downtime thật của venue |
| Cost trên lệnh hòa | `-0,29985%` so với khai báo 0,30% — tính đúng một lần |
| Tái lập BTC vol-scaled trend | khớp 6/6 ô split × cost tới 4 chữ số thập phân |
| Tái lập staggered pullback | khớp 480/480 điểm grid |
| Phép thử nhạy lookahead | cấy lookahead làm train nhảy từ 563% lên 4.201% |

Kết luận: dữ liệu không hỏng, cost không bị tính hai lần, contract không có
lookahead. **Các kết quả reject của nhánh research là đúng và không do lỗi code.**

Ghi chú phạm vi: khoảng 90 script research dùng `net = gross - cost` thay vì
`compute_trade_accounting`. Số học đúng, nhưng giả định notional cố định, không
compounding và slippage phẳng theo phần trăm. Accounting primitive chung phủ
nhánh live/paper, **không** phủ nhánh tạo artifact.

## Lỗi đã xác nhận — nhánh runtime

Xếp theo mức ảnh hưởng tới kết luận.

### Decision Engine không thể đạt ngưỡng BUY

`src/engine/decision.py:18` cộng weighted 50 cho layer thiếu thay vì chuẩn hóa
lại. Với `config.WEIGHTS` và bốn layer bị cố định ở `NEUTRAL_SCORE` trong
backtest, tổng điểm nằm trong `[31,0 ; 69,0]` trong khi `BUY_SCORE_THRESHOLD`
là 70,0. **BUY bất khả thi về số học.**

`paper_engine.py` có guard phát hiện việc này; `engine.py` và `short_engine.py`
không có, nên chúng trả `n_trades: 0` sạch sẽ — không phân biệt được giữa
"backtest bất khả thi về cấu trúc" và "không có cơ hội".

### Cost gate và TP planner mâu thuẫn nhau

`src/engine/support_resistance.py:467-488`. Target được coi là "gần" khi cách
entry tối đa `SR_FAR_RESISTANCE_ATR = 3,0`, nhưng để vượt sàn chi phí cần khoảng
5,2 ATR ở volatility quan sát được. Mọi target đủ xa để có lãi đều bị phân loại
"xa" và chuyển sang nhánh Fibonacci, nơi TP bị cap lại xuống dưới sàn chi phí.

Deadlock xảy ra khi `atr/price < 0,25%`. Giá trị quan sát: 0,144% → 0,033% →
0,019%. Đây là nguyên nhân của 92 `BUY_CANDIDATE` / 92 `RISK_REJECTED` / 0 lệnh.
Các rejection là kết quả hợp lệ của gate, nhưng **gate gần như không bao giờ
qua được trên BTC khung 5m**.

### Lookahead trong swing finder

`src/indicators/technical.py:127-131` và `:143-147`. Guard dùng `n = len(df)`
thay vì `end`, nên cửa sổ xác nhận bên phải bao gồm cả bar `end` — là nến đang
hình thành ở live và ngữ cảnh giữa bar ở `paper_engine.py:364-365`. Docstring
khẳng định ngược lại.

`support_resistance.py:71-72` có guard đúng. Hai bộ dò swing trong cùng repo với
hai bảo đảm nhân quả khác nhau.

### Backtest và live là hai chiến lược khác nhau

- `engine.py:108-110`: SL/TP chỉ kiểm tra với `close`, không với high/low. Stop
  trong engine này không phải stop.
- `engine.py:139-142`: không truyền `pullback_ok`, mặc định `True`. Live có áp
  filter này.
- `engine.py:100`, `paper_engine.py:66-70`: không truyền `agreement_ratio`, mặc
  định 1,0 trong khi live truyền 1/3–1. Điểm technical của backtest cao hơn live
  tới 20 điểm trên thang 100.
- `paper_engine.py:174` + `:325`: thứ tự tick long là `(open, low, high, close)`
  và entry lấy tick khớp đầu tiên, nên entry long luôn fill tại đáy sub-bar.
  "Adverse-first" là bảo thủ cho exit nhưng lạc quan cho entry.

### Chỉ số thống kê sai tên

`engine.py:255-259` và `:207` ghi `mean/std × √n` dưới tên `sharpe_ratio`. Đây là
t-statistic; nó tăng theo √n nên chạy trên nhiều dữ liệu hơn tự động cho số cao
hơn. Giá trị này được ghi vào MLflow, model card và dashboard.

### Position sizing bị cap ngầm

`src/engine/risk.py:94` cap notional ở `account_equity_usd`, bỏ qua
`config.LEVERAGE`. Với stop nhỏ trên khung ngắn, cap này luôn ràng buộc và làm
risk thực tế nhỏ hơn risk dự định 2–7 lần. `risk_amount_usd` báo cáo giá trị
*chưa* bị cap. Mọi absolute return, PnL USD và max drawdown từ nhánh runtime bị
hiểu thấp tương ứng; các chỉ số bất biến theo tỷ lệ như PF và win rate không bị
ảnh hưởng.

### Suy giảm âm thầm về giá trị trung tính

Bốn layer chiếm 62% trọng số trả về 50,0 khi nguồn dữ liệu lỗi, không phân biệt
được với thị trường thật sự trung tính và không phát log cảnh báo:
`derivatives.py:21,:47-48`, `orderflow.py:25-27,:36-38`,
`crossmarket_score.py:15-17`, `sentiment_score.py:8-9`.

`scripts/analyze_layer_signal.py:53-54` là phân tích lẽ ra phát hiện việc này,
nhưng nó tương quan layer score với PnL trên các layer có phương sai bằng không,
nên luôn báo "không có edge" bất kể sự thật. Các kết luận về derivatives,
sentiment và cross-market có thể đã được tính trên hằng số.

### Regime veto là dead code

`src/engine/regime.py:22` dùng ngưỡng `atr_pct >= 4`. Trên khung 5m, ATR14/close
của BTC vào khoảng 0,1–0,3%, nên nhánh `HIGH_VOLATILITY` không bao giờ xảy ra và
gate ở `decision.py:54` không bao giờ chạy. Layer regime suy biến thành hàm bậc
thang ba giá trị theo ADX.

### Lỗi khác

| Vị trí | Cơ chế |
|---|---|
| `run.py:283` | `KeyError: 'tp_distance_pct'`; sáu nhánh early-return của `compute_position_plan` không đi qua `_plan_common` nên không sinh key này |
| `engine.py:99` | vòng lặp kết thúc ở `n-1`, vị thế còn mở cuối dataset không được đóng và không được ghi nhận |
| `state_store.py:689` | `get_events` mặc định `ORDER BY id ASC LIMIT`; báo cáo sẽ kẹt ở nhóm event cũ nhất khi log vượt limit |
| `state_store.py:226-233` | `get_conn()` chạy `executescript` schema và năm hàm migration ở **mỗi** lần gọi |
| `staggered_pullback.py:23` vs `config.py:149-150` | hai cost model song song, chỉ trùng nhau ở default spot và lệch hai lần dưới `MARKET_TYPE=swap` |
| `analyze_choch_entry.py:46-53` | `_path_arrays` cố định thứ tự OHLC theo phía LONG, được 15 script dùng lại gồm cả contract có SHORT |
| `ml/entry_model.py:83-85` | không purge giữa train/test dù label nhìn trước 12 bar |
| `ml/entry_model.py:30-34` | dùng mức giá tuyệt đối làm feature trong classifier chia theo thời gian |

## Gánh nặng multiple testing

Tổng cấu hình đã quét, cộng từ trường `grid_size`/`grid` trong artifact:
**29.373**. Grid lớn nhất: `staggered_portfolio_optimization_9y` 13.125;
`fast_champion_*` 1.296 mỗi cái.

Không có deflated Sharpe, White reality check, Bonferroni/FDR hoặc PBO ở bất kỳ
đâu trong `src/` và `scripts/`. Hiệu chỉnh duy nhất là chia train/validation/test
thủ công, và cùng một biên `2023-08-07 / 2024-08-07` được nhiều script dùng lại
nên các split đó không còn là out-of-sample cho bất kỳ script nào trong nhóm.

Hệ quả: một kết quả test PF quanh 1,0–1,1 nằm trong dải nhiễu chọn lọc và không
được coi là bằng chứng edge.
