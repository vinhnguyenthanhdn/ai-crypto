"""Experiment Engine — tích hợp MLflow.

Mỗi lần Backtest/train Entry Model sinh ra một Experiment (Config/Feature,
Metrics, Decision) — dùng MLflow Tracking thay vì tự xây log. Backend
**sqlite** (`data/mlflow.db`), không cần server MLflow riêng ở quy mô hiện
tại — nâng cấp sau nếu cần chia sẻ nhiều máy.
"""
import json
import os
import tempfile

import mlflow

from . import config

EXPERIMENT_NAME = "ai-crypto-backtest"
PAPER_TRADING_EXPERIMENT_NAME = "ai-crypto-paper-trading"
ENTRY_MODEL_EXPERIMENT_NAME = "ai-crypto-entry-model"
ENTRY_MODEL_REGISTRY_NAME = "entry-model"


def _tracking_uri():
    # MLflow 3.x: filesystem backend ("file:./mlruns") đã vào maintenance mode,
    # báo lỗi trừ khi set MLFLOW_ALLOW_FILE_STORE — dùng sqlite backend theo
    # khuyến nghị chính thức, cũng nhất quán với SQLite đã dùng cho state_store.
    return f"sqlite:///{config.BASE_DIR / 'data' / 'mlflow.db'}"


def _artifact_location(name: str) -> str:
    # Artifact (model file, trades.json...) KHÔNG nằm trong sqlite — mặc định
    # MLflow lưu tương đối theo cwd lúc gọi (`./mlruns`), dễ vỡ nếu chạy script
    # từ thư mục khác. Chỉ định tuyệt đối dưới `data/mlruns/<experiment>` để ổn
    # định bất kể cwd (chỉ áp dụng khi experiment MỚI được tạo — đổi sau khi đã
    # tồn tại không có tác dụng, phải tạo experiment mới nếu cần đổi).
    return f"file:{config.BASE_DIR / 'data' / 'mlruns' / name}"


def _ensure_experiment(name: str):
    if mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(name, artifact_location=_artifact_location(name))
    mlflow.set_experiment(name)


def log_backtest_run(result: dict, params: dict, run_name: str | None = None) -> str:
    """Log 1 lần backtest thành 1 MLflow run, trả về run_id.

    `params`: Config dùng cho lần backtest (symbol, timeframe, fee/slippage,
    threshold...) — log làm MLflow params. `result`: output của
    `backtest.engine.run_backtest()` — log metrics; trade list log làm artifact
    JSON (không log từng trade thành metric riêng, tránh phình run).
    """
    mlflow.set_tracking_uri(_tracking_uri())
    _ensure_experiment(EXPERIMENT_NAME)

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(params)

        for key in ("n_bars", "n_trades", "total_return_pct", "max_drawdown_pct", "win_rate_pct", "sharpe_ratio"):
            value = result.get(key)
            if value is not None:
                mlflow.log_metric(key, value)

        with tempfile.TemporaryDirectory() as tmp_dir:
            trades_path = os.path.join(tmp_dir, "trades.json")
            with open(trades_path, "w", encoding="utf-8") as f:
                json.dump(result.get("trades", []), f, ensure_ascii=False, indent=2)
            mlflow.log_artifact(trades_path)

        return run.info.run_id


def log_entry_model_run(model, metrics: dict, params: dict, run_name: str | None = None, register: bool = True) -> str:
    """Log 1 lần train Entry Model — metrics + model artifact, đăng ký vào MLflow
    Model Registry (Strategy Package/Champion-Challenger dùng alias trên
    registry này ở bước sau) khi `register=True`.
    """
    import mlflow.lightgbm

    mlflow.set_tracking_uri(_tracking_uri())
    _ensure_experiment(ENTRY_MODEL_EXPERIMENT_NAME)

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(params)
        for key, value in metrics.items():
            if value is not None:
                mlflow.log_metric(key, value)

        mlflow.lightgbm.log_model(
            model,
            artifact_path="model",
            registered_model_name=ENTRY_MODEL_REGISTRY_NAME if register else None,
        )
        return run.info.run_id


def log_paper_trading_run(stats: dict, n_trades: int, run_name: str | None = None) -> str:
    """Log 1 lần chốt sổ Paper Trading (Success Criteria: edge dương sau phí) —
    khác Backtest (replay lịch sử), đây là thống kê trên lệnh ENTRY/EXIT thật đã
    phát sinh qua `run.py`. Gọi định kỳ (vd sau mỗi N lệnh đóng) qua
    `scripts/paper_trading_report.py`.
    """
    mlflow.set_tracking_uri(_tracking_uri())
    _ensure_experiment(PAPER_TRADING_EXPERIMENT_NAME)

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_param("symbol", config.SYMBOL)
        mlflow.log_metric("n_trades", n_trades)
        for key in ("total_return_pct", "max_drawdown_pct", "win_rate_pct", "sharpe_ratio"):
            value = stats.get(key)
            if value is not None:
                mlflow.log_metric(key, value)
        return run.info.run_id
