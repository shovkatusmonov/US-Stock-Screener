"""
features.py - Texnik indikatorlar, xususiyatlar va Tradable OHLC ajratilishi.
Impossible OHLC qatorlarini filtrlash va to'liq moslashtirilgan adjust_ohlc.
"""
import sqlite3
import numpy as np
import pandas as pd


def load_prices(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    """Bazadan kotirovkalarni yuklaydi, xato (impossible) OHLC qatorlarni filtrlaydi va sana bo'yicha indekslaydi."""
    query = """
        SELECT date, open, high, low, close, volume, adj_close
        FROM prices
        WHERE ticker = ?
        ORDER BY date ASC
    """
    df = pd.read_sql_query(query, conn, params=(ticker,))
    if df.empty:
        return df
    
    valid = (
        (df["high"] >= df["low"])
        & (df["high"] >= df["open"])
        & (df["high"] >= df["close"])
        & (df["low"] <= df["open"])
        & (df["low"] <= df["close"])
        & (df["low"] > 0)
        & (df["volume"] >= 0)
    )
    df = df[valid].copy()
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def get_tradable_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Ijro (execution) uchun sof savdo qilinadigan OHLC narxlarini qaytaradi."""
    if df.empty:
        return df
    tradable = pd.DataFrame(index=df.index)
    tradable["open"] = df["open"].astype(float)
    tradable["high"] = df["high"].astype(float)
    tradable["low"] = df["low"].astype(float)
    tradable["close"] = df["close"].astype(float)
    tradable["volume"] = df["volume"].astype(float)
    return tradable


def adjust_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Indikatorlar uzluksizligi uchun OHLC ni adj_close nisbatiga moslaydi."""
    if df.empty:
        return df
    out = df.copy()
    has_adj = "adj_close" in out.columns and out["adj_close"].notna().any()
    if has_adj:
        close_safe = np.where(out["close"] > 0, out["close"], np.nan)
        factor = out["adj_close"] / close_safe
        factor = pd.Series(factor, index=out.index).ffill().fillna(1.0)
        out["open"] = out["open"] * factor
        out["high"] = out["high"] * factor
        out["low"] = out["low"] * factor
        out["close"] = np.where(out["adj_close"].notna(), out["adj_close"], out["close"] * factor)
    return out


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


compute_atr = atr


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    calc_rsi = 100.0 - (100.0 / (1.0 + rs))

    conds = [
        (avg_loss == 0) & (avg_gain > 0),
        (avg_gain == 0) & (avg_loss > 0),
        (avg_gain == 0) & (avg_loss == 0),
    ]
    choices = [100.0, 0.0, 50.0]
    out = np.select(conds, choices, default=calc_rsi)
    out_s = pd.Series(out, index=close.index)
    out_s.iloc[:window] = np.nan
    return out_s


compute_rsi = rsi


def relative_strength(stock: pd.Series, bench: pd.Series, window: int = 60) -> pd.Series:
    bench_aligned = bench.reindex(stock.index).ffill()
    return stock.pct_change(window) - bench_aligned.pct_change(window)


def build_features(df: pd.DataFrame, bench_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    adj = adjust_ohlc(df)
    bench_adj = adjust_ohlc(bench_df)

    feat = pd.DataFrame(index=df.index)
    feat["open"] = adj["open"].astype(float)
    feat["high"] = adj["high"].astype(float)
    feat["low"] = adj["low"].astype(float)
    feat["close"] = adj["close"].astype(float)
    feat["volume"] = df["volume"].astype(float)
    
    feat["raw_close"] = df["close"].astype(float)

    feat["sma20"] = sma(feat["close"], 20)
    feat["sma50"] = sma(feat["close"], 50)
    feat["sma200"] = sma(feat["close"], 200)

    atr_val = atr(feat["high"], feat["low"], feat["close"], 14)
    feat["atr"] = atr_val
    feat["atr14"] = atr_val
    feat["atr_pct"] = atr_val / feat["close"].replace(0, np.nan)
    feat["atr_tradable"] = (feat["atr_pct"] * feat["raw_close"]).where(feat["raw_close"] > 0, atr_val)

    rsi_val = rsi(feat["close"], 14)
    feat["rsi"] = rsi_val
    feat["rsi14"] = rsi_val

    for w in (5, 20, 60):
        feat[f"ret{w}"] = feat["close"].pct_change(w)
        feat[f"rs{w}"] = relative_strength(feat["close"], bench_adj["close"], w)

    feat["rs60"] = relative_strength(feat["close"], bench_adj["close"], 60)
    feat["rel_strength"] = feat["rs60"].fillna(0.0)

    feat["high20"] = feat["high"].rolling(20).max().shift(1)
    feat["low20"] = feat["low"].rolling(20).min().shift(1)

    feat["avg_vol20"] = feat["volume"].rolling(20).mean()
    feat["dollar_vol20"] = (feat["raw_close"] * feat["volume"]).rolling(20).mean()
    feat["rel_vol"] = feat["volume"] / feat["avg_vol20"].shift(1).replace(0, np.nan)

    feat["trend_up"] = (
        (feat["close"] > feat["sma50"])
        & (feat["sma50"] > feat["sma200"])
        & (feat["sma200"] > feat["sma200"].shift(20))
    )
    return feat
