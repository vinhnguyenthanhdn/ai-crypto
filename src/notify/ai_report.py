"""AI Report qua Claude.

Gọi qua Claude Code CLI cục bộ (OAuth subscription, không phải Anthropic API
key trả phí theo token) — cùng cách OpenClaw gọi Claude trong các cron job
khác trên máy này (xem `/Users/administrator/.openclaw/bin/daily-briefing.py`).

Chỉ gọi khi đã có quyết định BUY/SELL — không gọi mỗi tick, để tránh chi phí
và độ trễ không cần thiết (rủi ro "chi phí LLM tăng theo tần suất cron").
"""
import subprocess

CLAUDE_BIN = "claude"
MODEL = "claude-sonnet-5"
TIMEOUT_SECONDS = 60


def _fallback_text(decision, symbol, price, total_score, reason):
    return f"{decision} {symbol} @ {price} — score {total_score}. {reason}"


def generate_report(symbol, decision, price, total_score, layer_scores, reason) -> str:
    prompt = (
        f"Tóm tắt tín hiệu giao dịch sau thành report ngắn gọn cho Telegram (tiếng Việt, "
        f"dùng markdown đơn giản, không quá 120 từ). Chỉ tóm tắt dữ liệu định lượng đã có, "
        f"không tự suy diễn thêm số liệu:\n\n"
        f"Symbol: {symbol}\n"
        f"Quyết định: {decision}\n"
        f"Giá: {price}\n"
        f"Tổng điểm Decision Engine: {total_score}/100\n"
        f"Điểm từng lớp: {layer_scores}\n"
        f"Lý do: {reason}\n"
    )

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--print", "--model", MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _fallback_text(decision, symbol, price, total_score, reason)

    if result.returncode != 0 or not result.stdout.strip():
        return _fallback_text(decision, symbol, price, total_score, reason)

    return result.stdout.strip()
