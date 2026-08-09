"""AI Report qua Claude.

Gọi qua Claude Code CLI cục bộ (OAuth subscription, không phải Anthropic API
key trả phí theo token) — cùng cách các automation local gọi Claude
khác trên máy này (xem `/Users/administrator/.openclaw/bin/daily-briefing.py`).

Chỉ gọi khi đã có quyết định BUY/SELL — không gọi mỗi tick, để tránh chi phí
và độ trễ không cần thiết.
"""
import subprocess

from .. import config, state_store

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


def generate_report_cached(cache_key, symbol, decision, price, total_score, layer_scores, reason) -> str:
    """Như `generate_report`, nhưng cache theo `cache_key` (vd `f"{decision}:{trade_id}"`)
    trong `AI_REPORT_CACHE_TTL_SECONDS`. Cache là best-effort; get/generate/set
    chưa atomic nên race vẫn có thể gọi trùng (`TODO-AI-CACHE-ATOMIC`)."""
    cached = state_store.get_cached_report(cache_key, config.AI_REPORT_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    report = generate_report(symbol, decision, price, total_score, layer_scores, reason)
    state_store.set_cached_report(cache_key, report)
    return report
