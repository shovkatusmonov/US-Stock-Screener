"""
app/indicators/technical.py - Vectorized Technical Indicator Computations.

Includes:
- SMA (20, 50, 200), EMA (9, 21, 50)
- RSI (14) with division-by-zero protection
- MACD (12, 26, 9) line, signal, and histogram
- ATR (14) and ATR percentage
- Bollinger Bands (20, 2 std): upper, middle, lower, %b, bandwidth
- ADX (14) trend strength with +DI, -DI
- VWAP (Volume Weighted Average Price)
- Relative Volume (rel_vol)
- Momentum returns (ret5, ret20, ret60)
- 52-week High/Low and percentage distance
- Market trend regime classification
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=span, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range calculation."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range (Wilder's smoothing)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index with proper zero-loss handling."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    res = 100.0 - (100.0 / (1.0 + rs))

    conds = [
        (avg_loss == 0) & (avg_gain > 0),
        (avg_gain == 0) & (avg_loss > 0),
        (avg_gain == 0) & (avg_loss == 0)
    ]
    choices = [100.0, 0.0, 50.0]
    out = np.select(conds, choices, default=res)
    out_s = pd.Series(out, index=close.index)
    out_s.iloc[:window] = np.nan
    return out_s


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_span: int = 9
) -> pd.DataFrame:
    """MACD Line, Signal Line, and Histogram."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_span)
    hist = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": hist
    }, index=close.index)


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands (upper, middle, lower, %b, bandwidth)."""
    mid = sma(close, window)
    std = close.rolling(window=window).std()
    upper = mid + (std * num_std)
    lower = mid - (std * num_std)
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({
        "bb_upper": upper,
        "bb_middle": mid,
        "bb_lower": lower,
        "bb_bandwidth": bandwidth,
        "bb_pct_b": pct_b
    }, index=close.index)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.DataFrame:
    """Average Directional Index (ADX), +DI, -DI."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = true_range(high, low, close)
    tr_smooth = tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    plus_dm_s = pd.Series(plus_dm, index=close.index).ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=close.index).ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    plus_di = 100.0 * (plus_dm_s / tr_smooth.replace(0, np.nan))
    minus_di = 100.0 * (minus_dm_s / tr_smooth.replace(0, np.nan))

    dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_val = dx.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    return pd.DataFrame({
        "adx": adx_val,
        "plus_di": plus_di,
        "minus_di": minus_di
    }, index=close.index)


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Rolling or cumulative typical price VWAP."""
    typical_price = (high + low + close) / 3.0
    tp_vol = typical_price * volume
    # 20-day rolling VWAP proxy for daily timeframes
    return tp_vol.rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes full technical indicator suite on OHLCV DataFrame.
    Returns feature DataFrame aligned on same DatetimeIndex.
    """
    if df.empty or len(df) < 5:
        return pd.DataFrame()

    out = df.copy()
    c = out["close"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    o = out["open"].astype(float)
    v = out["volume"].astype(float)

    # Moving averages
    out["sma20"] = sma(c, 20)
    out["sma50"] = sma(c, 50)
    out["sma200"] = sma(c, 200)

    out["ema9"] = ema(c, 9)
    out["ema21"] = ema(c, 21)
    out["ema50"] = ema(c, 50)

    # Momentum
    out["rsi14"] = rsi(c, 14)
    macd_df = macd(c, 12, 26, 9)
    out["macd"] = macd_df["macd"]
    out["macd_signal"] = macd_df["macd_signal"]
    out["macd_hist"] = macd_df["macd_hist"]

    # Volatility
    atr_val = atr(h, l, c, 14)
    out["atr14"] = atr_val
    out["atr_pct"] = atr_val / c.replace(0, np.nan)

    bb_df = bollinger_bands(c, 20, 2.0)
    out["bb_upper"] = bb_df["bb_upper"]
    out["bb_middle"] = bb_df["bb_middle"]
    out["bb_lower"] = bb_df["bb_lower"]
    out["bb_pct_b"] = bb_df["bb_pct_b"]

    # Trend Strength
    adx_df = adx(h, l, c, 14)
    out["adx14"] = adx_df["adx"]
    out["plus_di"] = adx_df["plus_di"]
    out["minus_di"] = adx_df["minus_di"]

    # Volume & VWAP
    out["vol_sma20"] = sma(v, 20)
    out["rel_vol"] = v / out["vol_sma20"].shift(1).replace(0, np.nan)
    out["vwap"] = vwap(h, l, c, v)

    # Multi-period returns
    out["ret5"] = c.pct_change(5)
    out["ret20"] = c.pct_change(20)
    out["ret60"] = c.pct_change(60)

    # 52-Week High / Low (approx 252 trading days)
    # A true 52-week high/low requires 252 trading bars.
    if len(df) >= 252:
        out["high_52w"] = h.rolling(252, min_periods=252).max()
        out["low_52w"] = l.rolling(252, min_periods=252).min()
    else:
        out["high_52w"] = np.nan
        out["low_52w"] = np.nan
    out["dist_52w_high"] = (c - out["high_52w"]) / out["high_52w"].replace(0, np.nan)
    out["dist_52w_low"] = (c - out["low_52w"]) / out["low_52w"].replace(0, np.nan)

    # Trend regime: do not confuse unavailable history with a neutral trend.
    trend_ready = out["sma50"].notna() & out["sma200"].notna()
    uptrend = trend_ready & (c > out["sma50"]) & (out["sma50"] > out["sma200"])
    downtrend = trend_ready & (c < out["sma50"]) & (out["sma50"] < out["sma200"])

    out["trend_regime"] = np.select(
        [~trend_ready, uptrend, downtrend],
        ["INSUFFICIENT_DATA", "UPTREND", "DOWNTREND"],
        default="NEUTRAL"
    )

    return out


def create_snapshot_row(
    symbol: str,
    exchange: str,
    df: pd.DataFrame
) -> Optional[Dict[str, Any]]:
    """
    Computes indicators and returns latest snapshot dictionary ready for DB insert.
    """
    # Allow partial indicator snapshots.
    # Individual indicators remain NaN until enough history exists.
    if df.empty or len(df) < 5:
        return None

    features = compute_all_indicators(df)
    if features.empty:
        return None

    last = features.iloc[-1]
    date_str = features.index[-1].strftime("%Y-%m-%d") if hasattr(features.index[-1], "strftime") else str(features.index[-1])[:10]

    def _val(k):
        v = last.get(k)
        if pd.isna(v):
            return None
        if isinstance(v, (int, float, np.number)):
            if np.isinf(v):
                return None
            return float(v)
        return str(v)

    return {
        "symbol": symbol,
        "exchange": exchange,
        "date": date_str,
        "close": _val("close"),
        "sma20": _val("sma20"),
        "sma50": _val("sma50"),
        "sma200": _val("sma200"),
        "ema9": _val("ema9"),
        "ema21": _val("ema21"),
        "ema50": _val("ema50"),
        "rsi14": _val("rsi14"),
        "macd": _val("macd"),
        "macd_signal": _val("macd_signal"),
        "macd_hist": _val("macd_hist"),
        "atr14": _val("atr14"),
        "atr_pct": _val("atr_pct"),
        "bb_upper": _val("bb_upper"),
        "bb_middle": _val("bb_middle"),
        "bb_lower": _val("bb_lower"),
        "bb_pct_b": _val("bb_pct_b"),
        "adx14": _val("adx14"),
        "rel_vol": _val("rel_vol"),
        "ret5": _val("ret5"),
        "ret20": _val("ret20"),
        "ret60": _val("ret60"),
        "high_52w": _val("high_52w"),
        "low_52w": _val("low_52w"),
        "dist_52w_high": _val("dist_52w_high"),
        "dist_52w_low": _val("dist_52w_low"),
        "trend_regime": _val("trend_regime") or "NEUTRAL",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
    }
