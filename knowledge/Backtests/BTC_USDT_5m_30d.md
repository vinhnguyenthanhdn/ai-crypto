# Backtest Review: BTC_USDT_5m_30d

_2026-08-04T17:00:57.516242+00:00_

## Summary
Chiến lược backtest trên BTC/USDT khung 5m cho kết quả rất tiêu cực: total return -31.926%, drawdown tối đa cũng 31.926% (gần như không phục hồi), Sharpe ratio -21.953 (âm sâu bất thường), win rate chỉ 5.79% trên 121 lệnh. Đây là một hệ thống thua lỗ có hệ thống, không phải nhiễu ngẫu nhiên.

## Lesson Learned
- Với win rate 5.79%/121 lệnh, chỉ khoảng 7 lệnh thắng — mẫu 20 lệnh đầu cho thấy 19/20 lệnh lỗ, chỉ 1 lệnh chạm take profit (pnl +0.108%).
- Đa số lệnh thua có pnl_pct dao động -0.04% đến -0.66%, nằm sát mức fee+slippage (0.001+0.0005 = 0.15% hai chiều ≈ 0.3%/round-trip), cho thấy tín hiệu vào lệnh không có edge thật, phần lớn thua chỉ nhích hơn chi phí giao dịch một chút.
- Lý do exit phổ biến nhất trong mẫu là "Volume giảm mạnh, momentum yếu" (11/20 lệnh) — toàn bộ đều lỗ, gợi ý điều kiện entry và điều kiện exit này bị mâu thuẫn hoặc entry vào đúng lúc momentum đã suy yếu.
- "Chạm stop loss" xuất hiện 5/20 lệnh, cũng toàn lỗ — cho thấy stop loss được đặt quá gần, hoặc entry timing kém khiến giá đi ngược ngay sau vào lệnh.

## Pattern
- Lệnh thắng duy nhất trong mẫu (entry_idx 1024) thoát bằng "Đạt take profit" với thời gian giữ lệnh dài hơn (entry_idx 1024 → exit_idx 1033, cách 9 nến) so với phần lớn lệnh thua (thường chỉ cách 1-2 nến).
- Các lệnh thua thường bị đóng rất nhanh (1-2 nến sau entry), phản ánh entry sai hướng ngay lập tức.

## Anti Pattern
- Exit theo "Volume giảm mạnh, momentum yếu" liên tục sinh lỗ nhỏ đều đặn — đây là anti-pattern chính, chiếm hơn nửa số lệnh lỗ trong mẫu.
- Entry ngay trước khi momentum yếu đi (dẫn đến bị chính điều kiện exit đó đóng lệnh gần như ngay sau vào) cho thấy filter entry và filter exit đang dùng chung một tín hiệu nhưng lệch pha thời gian.
- Stop loss bị chạm với khoảng cách entry-exit rất ngắn, hàm ý biên độ SL quá hẹp so với biến động 5m của BTC/USDT.

## Recommendation
- Rà lại điều kiện entry: cần loại bỏ hoặc trì hoãn entry khi tín hiệu momentum/volume đang có dấu hiệu suy yếu, vì đây đang là nguyên nhân gây lỗ chính trong mẫu.
- Xem lại khoảng cách stop loss, vì tỷ lệ chạm SL cao và khoảng cách entry-exit ngắn cho thấy SL hiện tại chưa phù hợp với biến động khung 5m.
- Với win rate 5.79% và Sharpe -21.953, chiến lược hiện tại không đạt ngưỡng khả dụng để triển khai — cần thiết kế lại logic entry/exit trước khi backtest lại, không nên chỉ tinh chỉnh tham số trên khung hiện tại.