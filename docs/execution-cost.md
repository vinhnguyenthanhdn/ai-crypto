# Chi phí thực thi và ràng buộc pháp lý

File này là SSOT cho giả định chi phí giao dịch, thanh khoản venue và ràng buộc
pháp lý/thuế. Kết quả backtest nằm ở `backtest-results.md`; quyết định kiến trúc
nằm ở `decisions.md`.

Mọi con số dưới đây phải kèm nguồn và ngày đo. Không dùng số nhớ hoặc số từ
trang tổng hợp bên thứ ba khi trang chính thức của venue mâu thuẫn.

## Biểu phí base tier — đo ngày 2026-08-09

Base tier nghĩa là người dùng thường, không VIP, không nắm token sàn, volume
30 ngày không đáng kể. Đây là ràng buộc thực tế của tài khoản nhỏ.

| Venue | Market | Maker | Taker |
|---|---|---:|---:|
| OKX | Spot BTC-USDT | 0,080% | 0,100% |
| OKX | USDT perp | 0,020% | 0,050% |
| Binance | Spot BTCUSDT | 0,100% | 0,100% |
| Binance | USDⓈ-M perp | 0,020% | 0,050% |

Nguồn: OKX fee-table API (`okx.com/v3/users/support/common/fee/fee-table`),
`binance.com/en/fee/schedule`, Binance futures fee FAQ.

### Giảm phí không cần volume

- **BNB**: giảm 25% spot, 10% futures khi trả phí bằng BNB. Đây là đòn bẩy duy
  nhất mà tài khoản nhỏ với tới được.
- **OKB không giảm phí.** OKX ghi rõ OKB không dùng để bù trừ phí giao dịch và
  không ảnh hưởng tier; API trả `showOkb: false` ở mọi tier. Các trang bên thứ ba
  còn ghi mức giảm 25% là thông tin đã lỗi thời.

### VIP tier không khả thi

Bậc VIP 1 gần nhất của cả hai venue đòi khoảng 100.000 USD tài sản hoặc volume
30 ngày hàng triệu USD. Với quy mô tài khoản hiện tại, VIP không nằm trong không
gian lựa chọn và không được dùng làm giả định trong bất kỳ contract nào.

## Chi phí khứ hồi thực tế

| Đường thực thi | Không BNB | Có BNB |
|---|---:|---:|
| BTC spot taker | 0,200% | 0,150% |
| BTC perp taker | 0,100% | 0,090% |
| BTC perp maker | 0,040% | 0,036% |

## Slippage — đo từ order book

4.695 mẫu `ORDER_BOOK_SAMPLE` BTC/USDT, 2026-08-06 → 2026-08-09.

| Percentile | Spread |
|---|---:|
| p1 | 0,0153 bps |
| p50 | 0,0154 bps |
| p99 | 0,0155 bps |
| max | 0,3675 bps |

Sổ lệnh rộng đúng một tick trong toàn bộ mẫu. Half-spread — chi phí thật của một
market order nhỏ — là **0,0077 bps**.

Độ sâu top-20 trung vị: 254.000 USD bid / 268.000 USD ask. Lệnh 500 USD chiếm
0,19% độ sâu, lệnh 5.000 USD chiếm 1,86%; cả hai khớp trọn tại best price, không
đi sâu vào sổ.

**Giá trị dùng cho contract: 0,5–1 bps mỗi chiều**, đã cộng đệm cho latency và
regime biến động. Mức 5 bps chỉ áp cho altcoin hoặc kịch bản stress.

Giới hạn của phép đo: mẫu nằm trong cửa sổ biến động thấp và là snapshot sổ lệnh,
chưa phản ánh latency thật giữa quyết định và fill. Chưa có mẫu ở regime stress.

## Thuế

Luật Thuế TNCN sửa đổi (109/2025/QH15), hiệu lực 2026-07-01: **0,1% trên giá trị
giao dịch gộp mỗi lần bán**, tính trên doanh số chứ không trên lợi nhuận. Không
có VAT trên chuyển nhượng tài sản mã hóa.

Thuế cộng thẳng vào chi phí mỗi vòng lệnh và **trừng phạt chiến lược tần suất
cao** độc lập với kết quả lãi lỗ. Contract phải khai thuế như một dòng chi phí
riêng, không gộp vào fee.

Chưa xác minh: thuế suất này có áp cho phái sinh hay chỉ giao ngay, và cơ chế thu
với giao dịch trên venue nước ngoài nơi không có đơn vị khấu trừ tại nguồn.

## Ràng buộc pháp lý và truy cập

Luật Công nghiệp Công nghệ số hiệu lực 2026-01-01; cấp phép sàn tài sản mã hóa
trong nước bắt đầu từ 2026-01-20 qua Ủy ban Chứng khoán Nhà nước. Điều kiện gồm
vốn điều lệ 10.000 tỷ VND, tối thiểu 65% sở hữu tổ chức và room ngoại 49%; dự
kiến khoảng năm giấy phép ở giai đoạn đầu.

Bộ Tài chính đang soạn quy định hạn chế công dân giao dịch trên venue nước ngoài
chưa được cấp phép, mức phạt đề xuất tới khoảng 1.900 USD với người dùng cá nhân,
canh theo mốc thị trường trong nước vận hành **2026-09-01**.

Trạng thái xác minh: đây là **dự thảo, chưa xác nhận đã chặn ở tầng ISP**. Không
được coi việc truy cập venue nước ngoài là bảo đảm khi lập kế hoạch thực thi.

## Thanh khoản venue

Độ sâu BTC ±2%, tháng 4/2026 (CoinMarketCap Exchange Monthly Report):

| Venue | ±2% depth |
|---|---:|
| Coinbase | 19,5M USD |
| Binance | 17,2M USD |
| Bybit | 14,9M USD |
| OKX | 4,6M USD |

OKX nông hơn Binance khoảng bốn lần. Ở quy mô lệnh hiện tại chênh lệch này chưa
ràng buộc, nhưng nó là một yếu tố khi chọn venue thực thi.

## Hệ quả cho contract

Giả định `round_trip_cost_pct = 0,30` dùng xuyên suốt research **cao gấp khoảng
ba lần** chi phí perp thật. Cấu hình cost trong `config.py` (`FEE_PCT`,
`SLIPPAGE_PCT`) phải được hiệu chỉnh theo bảng trên trước khi dùng kết quả cost
gate hoặc cost stress để reject candidate.

Hiệu chỉnh này **không** cứu các family có gross edge bằng không; xem phần phân
tích gross edge trong `backtest-results.md`. Nó chỉ đổi kết luận với candidate đã
có gross edge dương và từng trượt riêng ở gate cost-stress.

Maker rẻ hơn taker khoảng 5,4 bps khứ hồi, nhưng probe L2 của dự án đo được
markout `-15` đến `-29 bps`. Adverse selection lớn hơn khoản tiết kiệm nhiều lần;
không được chuyển sang giả định maker chỉ vì bảng phí thấp hơn.
