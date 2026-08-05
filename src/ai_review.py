"""AI Review Backtest.

LLM đọc Metrics/Trade Log/Config → sinh Summary/Lesson Learned/Pattern/Anti
Pattern/Recommendation, lưu vào Obsidian (`knowledge/Backtests/`). Tái dùng cơ
chế gọi LLM ở `notify/ai_report.py` (Claude CLI local qua OAuth, không dùng
Anthropic API key) — khác vai trò: review sau khi có kết quả, không phải mỗi tick,
nên timeout dài hơn (phân tích sâu hơn tóm tắt ngắn).
"""
import subprocess
from datetime import datetime, timezone

from . import config

CLAUDE_BIN = "claude"
MODEL = "claude-sonnet-5"
TIMEOUT_SECONDS = 120

BACKTESTS_DIR = config.BASE_DIR / "knowledge" / "Backtests"

REVIEW_TEMPLATE = "## Summary\n## Lesson Learned\n## Pattern\n## Anti Pattern\n## Recommendation"


def _build_prompt(result: dict, params: dict) -> str:
    n_trades = result.get("n_trades", 0)
    trades_sample = result.get("trades", [])[:20]  # mẫu, tránh vượt context với backtest nhiều lệnh
    return (
        "Bạn là quant reviewer. Đọc kết quả backtest sau và viết review theo đúng mẫu Markdown "
        "dưới đây, dựa HOÀN TOÀN trên số liệu đã cho — không tự suy diễn số liệu không có. "
        "Viết tiếng Việt, súc tích.\n\n"
        f"Config: {params}\n"
        f"Metrics: total_return_pct={result.get('total_return_pct')}, "
        f"max_drawdown_pct={result.get('max_drawdown_pct')}, sharpe_ratio={result.get('sharpe_ratio')}, "
        f"win_rate_pct={result.get('win_rate_pct')}, n_trades={n_trades}\n"
        f"Mẫu {len(trades_sample)}/{n_trades} lệnh đầu tiên: {trades_sample}\n\n"
        f"Viết theo đúng mẫu này (giữ nguyên heading):\n{REVIEW_TEMPLATE}\n"
    )


def _fallback_text(result: dict) -> str:
    return (
        f"{REVIEW_TEMPLATE}\n\n"
        f"(LLM review lỗi/timeout — đây là tóm tắt rule-based thay thế.)\n"
        f"Total return {result.get('total_return_pct')}%, max drawdown {result.get('max_drawdown_pct')}%, "
        f"win rate {result.get('win_rate_pct')}%, sharpe {result.get('sharpe_ratio')}, "
        f"{result.get('n_trades')} lệnh."
    )


def review_backtest(name: str, result: dict, params: dict) -> str:
    """Sinh review, lưu vào `knowledge/Backtests/<name>.md`, trả về đường dẫn file."""
    prompt = _build_prompt(result, params)
    review_text = None
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "--print", "--model", MODEL],
            input=prompt, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            review_text = proc.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        review_text = None

    if not review_text:
        review_text = _fallback_text(result)

    BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKTESTS_DIR / f"{name}.md"
    header = f"# Backtest Review: {name}\n\n_{datetime.now(timezone.utc).isoformat()}_\n\n"
    path.write_text(header + review_text, encoding="utf-8")
    return str(path)
