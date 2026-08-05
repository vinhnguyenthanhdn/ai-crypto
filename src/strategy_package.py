"""Strategy Package: không build nhiều project — chỉ có một Trading Framework,
model chỉ là một Strategy Package gồm Config, Model Weight, Feature Version,
Risk Profile, Exit Rule, Backtest Result, Paper Result, Model Card. Framework
luôn giống nhau, chỉ thay Strategy Package.

Lưu 2 nơi theo đúng phân tách Data Memory / Knowledge Memory:
- Manifest JSON (Config/Model Weight/Feature Version/Risk Profile/Backtest/
  Paper Result — dữ liệu định lượng, tái tạo được) → `data/strategy_packages/`.
- Model Card Markdown (Summary/Strength/Weakness/... — tri thức, con người đọc
  được) → `knowledge/Models/` (Obsidian vault).
"""
import json

from . import config
from .ml import entry_model

STRATEGY_PACKAGES_DIR = config.BASE_DIR / "data" / "strategy_packages"
MODEL_CARDS_DIR = config.BASE_DIR / "knowledge" / "Models"


def _exit_rule_description() -> str:
    """Mô tả Exit Rule hiện tại (rule-based) — không tự động trích từ code, cập
    nhật thủ công khi `decision.decide_exit` đổi logic."""
    return (
        "Rule-based: stop loss/take profit theo ATR (risk.py, R:R 1.5), "
        "MACD đảo chiều xuống, RSI quá mua (>75), volume giảm mạnh (<50% SMA20), "
        "EMA20 cắt xuống EMA50."
    )


def build_manifest(
    name: str,
    entry_model_version: str,
    entry_model_metrics: dict,
    backtest_result: dict | None = None,
    paper_stats: dict | None = None,
) -> dict:
    return {
        "name": name,
        "config": {
            "weights": config.WEIGHTS,
            "buy_score_threshold": config.BUY_SCORE_THRESHOLD,
            "watch_score_threshold": config.WATCH_SCORE_THRESHOLD,
        },
        "model_weight": {
            "registry_name": "entry-model",
            "version": entry_model_version,
            "uri": f"models:/entry-model/{entry_model_version}",
            "metrics": entry_model_metrics,
        },
        "feature_version": {
            "columns": entry_model.FEATURE_COLUMNS,
            "horizon_bars": entry_model.DEFAULT_HORIZON_BARS,
            "label_threshold_pct": entry_model.DEFAULT_LABEL_THRESHOLD_PCT,
        },
        "risk_profile": {
            "account_equity_usd": config.ACCOUNT_EQUITY_USD,
            "risk_per_trade_pct": config.RISK_PER_TRADE_PCT,
            "daily_loss_limit_pct": config.DAILY_LOSS_LIMIT_PCT,
            "max_drawdown_pct": config.MAX_DRAWDOWN_PCT,
            "cooldown_minutes": config.COOLDOWN_MINUTES,
            "max_concurrent_positions": config.MAX_CONCURRENT_POSITIONS,
        },
        "exit_rule": _exit_rule_description(),
        "backtest_result": backtest_result,
        "paper_result": paper_stats,
    }


def _format_metric(value):
    return "—" if value is None else value


def render_model_card(manifest: dict) -> str:
    bt = manifest.get("backtest_result") or {}
    paper = manifest.get("paper_result") or {}
    mw = manifest["model_weight"]

    return f"""# Strategy Package: {manifest['name']}

## Summary
Model Weight: `entry-model` version {mw['version']} — AUC {_format_metric(mw['metrics'].get('auc'))}, \
accuracy {_format_metric(mw['metrics'].get('accuracy'))}.

## Feature
Feature Version: {len(manifest['feature_version']['columns'])} cột \
({', '.join(manifest['feature_version']['columns'])}), horizon \
{manifest['feature_version']['horizon_bars']} bar, label threshold \
{manifest['feature_version']['label_threshold_pct']}%.

## Risk
{manifest['risk_profile']}

## Backtest
Total return: {_format_metric(bt.get('total_return_pct'))}% — Max drawdown: \
{_format_metric(bt.get('max_drawdown_pct'))}% — Win rate: \
{_format_metric(bt.get('win_rate_pct'))}% — Sharpe: {_format_metric(bt.get('sharpe_ratio'))} \
— Số trade: {_format_metric(bt.get('n_trades'))}.

## Paper Trading
Total return: {_format_metric(paper.get('total_return_pct'))}% — Max drawdown: \
{_format_metric(paper.get('max_drawdown_pct'))}% — Win rate: \
{_format_metric(paper.get('win_rate_pct'))}%.

## Strength

## Weakness
Chỉ dùng Technical + Regime cho Entry Model (Order Flow/Derivatives/Cross-market/
Sentiment chưa đủ dữ liệu lịch sử).

## Deployment History
- {manifest['name']}: model_weight version {mw['version']}.

## Related Experiments
MLflow run — xem registry `entry-model`, experiment `ai-crypto-entry-model`.
"""


def save(manifest: dict) -> tuple:
    """Lưu manifest JSON (Data Memory) + Model Card Markdown (Knowledge Memory).
    Trả về (manifest_path, model_card_path)."""
    STRATEGY_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CARDS_DIR.mkdir(parents=True, exist_ok=True)

    manifest_path = STRATEGY_PACKAGES_DIR / f"{manifest['name']}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    model_card_path = MODEL_CARDS_DIR / f"{manifest['name']}.md"
    model_card_path.write_text(render_model_card(manifest), encoding="utf-8")

    return manifest_path, model_card_path
