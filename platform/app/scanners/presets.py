"""
app/scanners/presets.py - Standard Built-in Screener Presets.
"""

from typing import Dict
from app.scanners.filters import ScreenFilter

PRESETS: Dict[str, ScreenFilter] = {
    "bullish_breakout": ScreenFilter(
        name="bullish_breakout",
        description="Strong uptrend, breakout near 52w high with elevated volume",
        min_price=5.0,
        min_volume=200000.0,
        min_rel_vol=1.3,
        trend_regime="UPTREND",
        above_sma50=True,
        above_sma200=True,
        min_dist_52w_high=-0.05,
        min_rsi=50.0,
        max_rsi=78.0
    ),

    "pullback_support": ScreenFilter(
        name="pullback_support",
        description="Healthy uptrend stocks pulling back to 50 SMA / 21 EMA support",
        min_price=5.0,
        min_volume=150000.0,
        above_sma200=True,
        min_rsi=38.0,
        max_rsi=52.0,
        min_dist_52w_high=-0.25,
        trend_regime="UPTREND"
    ),

    "oversold_bounce": ScreenFilter(
        name="oversold_bounce",
        description="Deeply oversold stocks with potential mean-reversion bounce",
        min_price=3.0,
        min_volume=100000.0,
        max_rsi=32.0,
        min_rel_vol=1.0
    ),

    "volume_surge": ScreenFilter(
        name="volume_surge",
        description="Unusual volume spike (> 2.5x 20-day average) with positive price action",
        min_price=3.0,
        min_volume=300000.0,
        min_rel_vol=2.5,
        min_ret20=0.0
    ),

    "golden_cross": ScreenFilter(
        name="golden_cross",
        description="SMA50 > SMA200 with positive medium-term momentum",
        min_price=5.0,
        min_volume=150000.0,
        above_sma50=True,
        above_sma200=True,
        min_ret20=0.02
    ),

    "52w_high_breakout": ScreenFilter(
        name="52w_high_breakout",
        description="Testing or making new 52-week highs with above-average volume",
        min_price=5.0,
        min_volume=250000.0,
        min_dist_52w_high=-0.02,
        min_rel_vol=1.2,
        above_sma50=True
    ),

    "bearish_breakdown": ScreenFilter(
        name="bearish_breakdown",
        description="Stocks breaking down below key moving averages near 52w lows",
        min_price=3.0,
        min_volume=100000.0,
        above_sma50=False,
        above_sma200=False,
        max_dist_52w_low=0.04,
        max_rsi=45.0,
        trend_regime="DOWNTREND"
    ),

    "overbought_warning": ScreenFilter(
        name="overbought_warning",
        description="Extremely extended stocks with RSI > 75 facing pullback risk",
        min_price=5.0,
        min_volume=100000.0,
        min_rsi=75.0
    )
}


def get_preset(name: str) -> ScreenFilter:
    """Get a preset by name (case-insensitive) or raise ValueError."""
    key = name.strip().lower()
    if key in PRESETS:
        return PRESETS[key]

    available = ", ".join(PRESETS.keys())
    raise ValueError(f"Unknown screener preset '{name}'. Available presets: {available}")
