"""
app/indicators/multi_timeframe.py - Multi-Timeframe Alignment and Analysis.

Enables cross-timeframe signal verification:
- Confirms Daily signals against Weekly trend (e.g. Weekly Close > Weekly EMA21).
- Multi-timeframe momentum scoring.
"""

from typing import Dict, Any, Optional
import pandas as pd
from app.indicators.technical import compute_all_indicators
from app.data.loader import resample_ohlcv


def compute_multi_timeframe_analysis(daily_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Computes Daily and Weekly trend alignment.
    Alignment is only produced when both timeframes have sufficient history.
    """
    if daily_df.empty or len(daily_df) < 200:
        return {
            "status": "INSUFFICIENT_DATA",
            "alignment": "INSUFFICIENT_DATA",
            "daily": {"ready": False},
            "weekly": {"ready": False}
        }

    daily_ind = compute_all_indicators(daily_df)
    weekly_df = resample_ohlcv(daily_df, rule="W-FRI")

    last_d = daily_ind.iloc[-1]
    daily_ready = (
        pd.notna(last_d["sma50"])
        and pd.notna(last_d["sma200"])
    )

    weekly_ind = (
        compute_all_indicators(weekly_df)
        if len(weekly_df) >= 50 else pd.DataFrame()
    )

    last_w = weekly_ind.iloc[-1] if not weekly_ind.empty else None

    weekly_ready = (
        last_w is not None
        and pd.notna(last_w["ema21"])
        and pd.notna(last_w["rsi14"])
    )

    if not daily_ready or not weekly_ready:
        return {
            "status": "INSUFFICIENT_DATA",
            "alignment": "INSUFFICIENT_DATA",
            "daily": {
                "ready": bool(daily_ready),
                "bars": len(daily_df)
            },
            "weekly": {
                "ready": bool(weekly_ready),
                "bars": len(weekly_df)
            }
        }

    d_close = float(last_d["close"])
    d_sma50 = float(last_d["sma50"])
    d_sma200 = float(last_d["sma200"])
    d_rsi = float(last_d["rsi14"]) if pd.notna(last_d["rsi14"]) else None

    w_close = float(last_w["close"])
    w_ema21 = float(last_w["ema21"])
    w_rsi = float(last_w["rsi14"])

    daily_bullish = d_close > d_sma50 > d_sma200
    daily_bearish = d_close < d_sma50 < d_sma200

    weekly_bullish = w_close > w_ema21 and w_rsi > 48.0
    weekly_bearish = w_close < w_ema21 and w_rsi < 52.0

    if daily_bullish and weekly_bullish:
        alignment = "BULLISH_CONFLUENCE"
    elif daily_bearish and weekly_bearish:
        alignment = "BEARISH_CONFLUENCE"
    elif (daily_bullish and weekly_bearish) or (
        daily_bearish and weekly_bullish
    ):
        alignment = "DAILY_PULLBACK_OR_COUNTERTREND"
    else:
        alignment = "NEUTRAL"

    return {
        "status": "OK",
        "alignment": alignment,
        "daily": {
            "ready": True,
            "close": d_close,
            "rsi": d_rsi,
            "regime": str(last_d["trend_regime"]),
            "above_sma50": d_close > d_sma50,
            "above_sma200": d_close > d_sma200
        },
        "weekly": {
            "ready": True,
            "close": w_close,
            "rsi": w_rsi,
            "above_ema21": w_close > w_ema21,
            "is_bullish": weekly_bullish
        }
    }
