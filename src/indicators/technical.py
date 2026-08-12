import pandas as pd
import numpy as np
from typing import Optional, Tuple


def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI)
    """
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(data: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate MACD (Moving Average Convergence Divergence)
    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast = data.ewm(span=fast, adjust=False).mean()
    ema_slow = data.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(data: pd.Series, period: int = 20, num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate Bollinger Bands
    Returns: (upper_band, middle_band, lower_band)
    """
    middle_band = data.rolling(window=period).mean()
    std = data.rolling(window=period).std()
    upper_band = middle_band + (std * num_std)
    lower_band = middle_band - (std * num_std)
    return upper_band, middle_band, lower_band


def find_recent_swing_low(lows: pd.Series, lookback: int = 5, window: int = 5) -> Optional[float]:
    """
    Find the most recent swing low in the price data.
    A swing low is a local minimum where the low is strictly less than surrounding lows.
    
    Args:
        lows: Series of low prices
        lookback: How many bars to look back
        window: Window size for checking surrounding bars
    
    Returns:
        The swing low value or None if not found
    """
    if len(lows) < window * 2 + 1:
        return None
    
    for i in range(len(lows) - 1, max(len(lows) - lookback - 1, window), -1):
        if i < window or i >= len(lows) - window:
            continue
        
        left = lows[i - window:i]
        right = lows[i + 1:i + window + 1]
        
        if lows[i] < left.min() and lows[i] < right.min():
            return lows[i]
    
    return None


def find_recent_swing_high(highs: pd.Series, lookback: int = 5, window: int = 5) -> Optional[float]:
    """
    Find the most recent swing high in the price data.
    A swing high is a local maximum where the high is strictly greater than surrounding highs.
    
    Args:
        highs: Series of high prices
        lookback: How many bars to look back
        window: Window size for checking surrounding bars
    
    Returns:
        The swing high value or None if not found
    """
    if len(highs) < window * 2 + 1:
        return None
    
    for i in range(len(highs) - 1, max(len(highs) - lookback - 1, window), -1):
        if i < window or i >= len(highs) - window:
            continue
        
        left = highs[i - window:i]
        right = highs[i + 1:i + window + 1]
        
        if highs[i] > left.max() and highs[i] > right.max():
            return highs[i]
    
    return None


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range (ATR)
    """
    high_low = high - low
    high_close = np.abs(high - close.shift())
    low_close = np.abs(low - close.shift())
    
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr