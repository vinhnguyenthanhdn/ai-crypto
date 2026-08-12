import pandas as pd
import numpy as np
from typing import Optional, Tuple


def find_recent_swing_low(df: pd.DataFrame, lookback: int = 5, swing_size: int = 3) -> Optional[float]:
    """
    Find the most recent swing low in the dataframe.
    
    A swing low is defined as a low that is lower than 'swing_size' bars on either side.
    
    Args:
        df: DataFrame with OHLCV data
        lookback: Number of recent bars to search
        swing_size: Number of bars on each side to compare
    
    Returns:
        The swing low value, or None if not found
    """
    if len(df) < swing_size * 2 + 1:
        return None
    
    lows = df['low'].values
    start_idx = max(swing_size, len(lows) - lookback)
    
    for i in range(len(lows) - swing_size - 1, start_idx - 1, -1):
        left = lows[i - swing_size:i]
        right = lows[i + 1:i + swing_size + 1]
        
        if len(left) == swing_size and len(right) == swing_size:
            if lows[i] < left.min() and lows[i] < right.min():
                return float(lows[i])
    
    return None


def find_recent_swing_high(df: pd.DataFrame, lookback: int = 5, swing_size: int = 3) -> Optional[float]:
    """
    Find the most recent swing high in the dataframe.
    
    A swing high is defined as a high that is higher than 'swing_size' bars on either side.
    
    Args:
        df: DataFrame with OHLCV data
        lookback: Number of recent bars to search
        swing_size: Number of bars on each side to compare
    
    Returns:
        The swing high value, or None if not found
    """
    if len(df) < swing_size * 2 + 1:
        return None
    
    highs = df['high'].values
    start_idx = max(swing_size, len(highs) - lookback)
    
    for i in range(len(highs) - swing_size - 1, start_idx - 1, -1):
        left = highs[i - swing_size:i]
        right = highs[i + 1:i + swing_size + 1]
        
        if len(left) == swing_size and len(right) == swing_size:
            if highs[i] > left.max() and highs[i] > right.max():
                return float(highs[i])
    
    return None


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate the Relative Strength Index (RSI).
    
    Args:
        df: DataFrame with OHLCV data
        period: RSI period
    
    Returns:
        Series with RSI values
    """
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_ema(df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
    """
    Calculate Exponential Moving Average.
    
    Args:
        df: DataFrame with OHLCV data
        period: EMA period
        column: Column to calculate EMA on
    
    Returns:
        Series with EMA values
    """
    return df[column].ewm(span=period, adjust=False).mean()


def calculate_sma(df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
    """
    Calculate Simple Moving Average.
    
    Args:
        df: DataFrame with OHLCV data
        period: SMA period
        column: Column to calculate SMA on
    
    Returns:
        Series with SMA values
    """
    return df[column].rolling(window=period).mean()


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate Bollinger Bands.
    
    Args:
        df: DataFrame with OHLCV data
        period: Period for moving average
        std_dev: Number of standard deviations
    
    Returns:
        Tuple of (upper_band, middle_band, lower_band)
    """
    middle_band = calculate_sma(df, period)
    std = df['close'].rolling(window=period).std()
    
    upper_band = middle_band + (std * std_dev)
    lower_band = middle_band - (std * std_dev)
    
    return upper_band, middle_band, lower_band


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate MACD (Moving Average Convergence Divergence).
    
    Args:
        df: DataFrame with OHLCV data
        fast: Fast EMA period
        slow: Slow EMA period
        signal: Signal line period
    
    Returns:
        Tuple of (macd_line, signal_line, histogram)
    """
    fast_ema = calculate_ema(df, fast)
    slow_ema = calculate_ema(df, slow)
    
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    
    return macd_line, signal_line, histogram