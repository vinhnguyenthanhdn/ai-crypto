# Backlog — Ý tưởng chưa chốt đưa vào Roadmap

Khác với `tasks.md` (việc đã chốt theo Roadmap ở `plan-02.md` mục 20), file này chứa các đề xuất **đã research nhưng chưa quyết định implement** — cần đánh giá thêm (thường là qua Backtest Engine) trước khi chuyển thành task thật hoặc bị loại bỏ.

**Cách dùng:** mỗi mục ghi rõ nguồn gốc đề xuất, đánh giá hiện tại, và điều kiện để chuyển sang `tasks.md`. Khi quyết định làm, chuyển task sang `tasks.md` đúng Phase liên quan và xoá mục ở đây (không giữ song song 2 nơi — SSOT).

---

## Market Structure / Smart Money Concepts (BOS, CHoCH, Order Block)

**Nguồn:** đã khảo sát OSS ở `plan-02.md` mục 5c (`smart-money-concepts`, joshyattridge, PyPI — xác nhận lib thật, đang maintain, dùng trực tiếp được), review kỹ thuật price action.

**Đánh giá:**
- Lấp gap thật: Rule Engine hiện tại (`technical.py`, mục 7b) chưa có tín hiệu nào đọc trực tiếp cấu trúc thị trường (swing high/low, vùng thanh khoản) — toàn bộ 8 tín hiệu hiện có đều là indicator phái sinh từ giá.
- Bằng chứng: backtest cộng đồng ~2600 lệnh cho win rate 50-65% khi áp rule chặt — có cơ sở, không phải "chén thánh".
- Liquidity sweep (nhánh ICT liên quan) được cho là "sạch" nhất ở crypto (24/7, đòn bẩy cao, market maker thuật toán) — nhưng cần dữ liệu tick/order book chi tiết hơn khung 5m hiện dùng, hợp hướng event-driven (NautilusTrader, mục 5c) hơn Rule Engine hiện tại — **để sau, chưa xét ở đây**.

**Rủi ro cần xử lý nếu làm:**
1. Hiệu năng: Order Block trong lib từng bị báo cáo chạy ~20s/lần (vòng lặp Python) — không được gọi trong vòng lặp theo dõi mỗi `MONITOR_POLL_SECONDS=5s` (mục 5d), phải tính 1 lần/cửa sổ cron (giống cách `regime` tính 1 lần).
2. Tham số `swing_length` cần Backtest Engine tự dò, không đoán số cố định.

**Điều kiện để chuyển sang `tasks.md`:** chạy thử BOS/CHoCH qua `src/backtest/engine.py` (đã có, Phase 2) so sánh có/không tín hiệu này, xác nhận cải thiện metric (Sharpe/Win Rate) trước khi đưa vào live Rule Engine hoặc `decide_exit`.

**Vị trí dự kiến nếu adopt:** sub-signal trong Technical score (không tạo layer/trọng số mới ngay), hoặc tín hiệu exit bổ sung cho `decide_exit` (CHoCH phản ứng sớm hơn EMA20<EMA50/MACD cross hiện dùng).

---

## Fibonacci Retracement/Extension

**Nguồn:** review kỹ thuật price action.

**Đánh giá:** không nên thêm ở vai trò tính điểm entry — trùng lặp vai trò trend/support-resistance đã có (EMA/Supertrend), cần chọn swing high/low (tham số chủ quan) dễ gây whipsaw ở khung ngắn (1m/5m), không có bằng chứng vượt trội so với support/resistance thường.

**Khả năng duy nhất còn mở:** Fibonacci **extension** làm target Take Profit thay/bổ sung cho R:R cố định hiện tại (`risk.py`) — thuộc phạm vi Exit Rule (mục 9), không phải Rule Engine chấm điểm. Chưa đủ lý do ưu tiên để đưa vào `tasks.md`.

**Kết luận:** để đây làm ghi nhận đã cân nhắc, không cần theo dõi tiếp trừ khi có lý do mới.
