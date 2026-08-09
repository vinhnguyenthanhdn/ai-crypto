"""Tooling Champion–Challenger qua MLflow Model Registry alias.

Runtime hiện dùng Champion rule-based và chưa load model MLflow. Module này chỉ
chuẩn bị thao tác alias cho `TODO-CHALLENGER`, không chứng minh model đang serving.
"""
import mlflow
from mlflow.tracking import MlflowClient

from . import experiment

CHAMPION_ALIAS = "champion"
CHALLENGER_ALIAS = "challenger"


def get_client() -> MlflowClient:
    mlflow.set_tracking_uri(experiment._tracking_uri())
    return MlflowClient()


def set_challenger(version: str):
    get_client().set_registered_model_alias(experiment.ENTRY_MODEL_REGISTRY_NAME, CHALLENGER_ALIAS, version)


def promote_challenger_to_champion():
    """Challenger phải backtest tốt, paper trading ổn định, và vượt Champion trong
    thời gian đủ dài mới được thay Champion — điều kiện này đánh giá thủ công/bằng
    script riêng trước khi gọi hàm này, hàm chỉ thực hiện việc promote.
    """
    client = get_client()
    challenger = get_challenger_version()
    if challenger is None:
        raise ValueError("Chưa có challenger nào được set — dùng set_challenger(version) trước")
    client.set_registered_model_alias(experiment.ENTRY_MODEL_REGISTRY_NAME, CHAMPION_ALIAS, challenger.version)
    return challenger.version


def _get_by_alias(alias: str):
    client = get_client()
    try:
        return client.get_model_version_by_alias(experiment.ENTRY_MODEL_REGISTRY_NAME, alias)
    except mlflow.exceptions.MlflowException:
        return None


def get_champion_version():
    return _get_by_alias(CHAMPION_ALIAS)


def get_challenger_version():
    return _get_by_alias(CHALLENGER_ALIAS)


def load_champion_model():
    mlflow.set_tracking_uri(experiment._tracking_uri())
    return mlflow.lightgbm.load_model(f"models:/{experiment.ENTRY_MODEL_REGISTRY_NAME}@{CHAMPION_ALIAS}")
