"""Entry Model — LightGBM.

Tham khảo cách tách feature khỏi model của Freqtrade FreqAI mà không cài
Freqtrade: raw feature đã có sẵn từ Feature Engine
(`technical.score_from_indicators`, `regime.classify_regime`), model chỉ học
từ các cột này — không tự tính lại indicator.

**Label:** forward return sau `horizon_bars` bar có vượt `label_threshold_pct`
không (binary classification) — đơn giản, dễ diễn giải, khớp vai trò "Entry
Model dự đoán xác suất".

**Giới hạn:** cùng giới hạn dữ liệu lịch sử như Backtest Engine — chỉ Technical
+ Regime có đủ lịch sử qua OHLCV công khai; Order Flow/Derivatives/Cross-market/
Sentiment chưa đưa vào feature (Feature Store chưa tích luỹ đủ dài cho các lớp
này — cần chạy `run.py` định kỳ một thời gian trước khi có đủ dữ liệu thật để
bổ sung).

**Walk-forward, không random split** (chống rủi ro "Backtest overfitting"):
tách train/test theo thời gian (train = đoạn đầu, test = đoạn cuối), không xáo
trộn — tránh model "nhìn thấy tương lai" qua các bar nằm rải rác trong tập train.
"""
import lightgbm as lgb
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score

from ..backtest.engine import WARMUP_BARS
from ..engine import regime as regime_engine
from ..indicators import technical

FEATURE_COLUMNS = [
    "ema20", "ema50", "ema200", "rsi", "macd", "macd_signal",
    "adx", "atr", "vwap", "vol_sma20", "supertrend_dir",
    "regime_atr_pct", "regime_score",
]

DEFAULT_HORIZON_BARS = 12  # 12 bar x 5m = 1 giờ
DEFAULT_LABEL_THRESHOLD_PCT = 0.3


def build_dataset(
    df: pd.DataFrame,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    label_threshold_pct: float = DEFAULT_LABEL_THRESHOLD_PCT,
) -> pd.DataFrame:
    """df: OHLCV thô. Trả về DataFrame feature + label, đã bỏ NaN (giai đoạn warmup)."""
    enriched = technical.add_indicators(df)
    close = df["close"].to_numpy()
    n = len(df)

    rows = []
    for i in range(WARMUP_BARS, n - horizon_bars):
        tech = technical.score_from_indicators(enriched, idx=i)
        reg = regime_engine.classify_regime(enriched, idx=i)
        raw = tech["raw"]

        forward_return_pct = (close[i + horizon_bars] - close[i]) / close[i] * 100
        rows.append(
            {
                "ema20": raw["ema20"],
                "ema50": raw["ema50"],
                "ema200": raw["ema200"],
                "rsi": raw["rsi"],
                "macd": raw["macd"],
                "macd_signal": raw["macd_signal"],
                "adx": raw["adx"],
                "atr": raw["atr"],
                "vwap": raw["vwap"],
                "vol_sma20": raw["vol_sma20"],
                "supertrend_dir": raw["supertrend_dir"],
                "regime_atr_pct": reg["raw"]["atr_pct"],
                "regime_score": reg["score"],
                "forward_return_pct": round(forward_return_pct, 4),
                "label": int(forward_return_pct >= label_threshold_pct),
            }
        )

    dataset = pd.DataFrame(rows).dropna()
    return dataset.reset_index(drop=True)


def train_entry_model(dataset: pd.DataFrame, test_size: float = 0.2, **lgbm_params):
    """Walk-forward split (không random) — xem docstring module."""
    split_idx = int(len(dataset) * (1 - test_size))
    train_df = dataset.iloc[:split_idx]
    test_df = dataset.iloc[split_idx:]

    if train_df.empty or test_df.empty:
        raise ValueError("Dataset quá nhỏ để tách train/test — cần thêm dữ liệu lịch sử")

    x_train, y_train = train_df[FEATURE_COLUMNS], train_df["label"]
    x_test, y_test = test_df[FEATURE_COLUMNS], test_df["label"]

    params = {"n_estimators": 200, "max_depth": 5, "learning_rate": 0.05, "min_child_samples": 20, **lgbm_params}
    model = lgb.LGBMClassifier(**params)
    model.fit(x_train, y_train)

    proba_test = model.predict_proba(x_test)[:, 1]
    pred_test = (proba_test >= 0.5).astype(int)

    metrics = {
        "accuracy": round(float(accuracy_score(y_test, pred_test)), 4),
        "auc": round(float(roc_auc_score(y_test, proba_test)), 4) if y_test.nunique() > 1 else None,
        "positive_rate_train": round(float(y_train.mean()), 4),
        "positive_rate_test": round(float(y_test.mean()), 4),
        "n_train": len(train_df),
        "n_test": len(test_df),
    }
    return model, metrics
