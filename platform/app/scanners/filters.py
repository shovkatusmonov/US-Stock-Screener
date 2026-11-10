"""
app/scanners/filters.py - Screener Filter Definitions and Primitives.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ScreenFilter:
    name: str = "custom"
    description: str = "Custom Scan"
    min_price: Optional[float] = 2.0
    max_price: Optional[float] = None
    min_volume: Optional[float] = 100000.0
    min_rel_vol: Optional[float] = None
    max_rel_vol: Optional[float] = None
    min_rsi: Optional[float] = None
    max_rsi: Optional[float] = None
    trend_regime: Optional[str] = None  # 'UPTREND', 'DOWNTREND', 'NEUTRAL'
    above_sma50: Optional[bool] = None
    above_sma200: Optional[bool] = None
    min_dist_52w_high: Optional[float] = None
    max_dist_52w_low: Optional[float] = None
    min_ret20: Optional[float] = None
    asset_classes: List[str] = field(default_factory=lambda: ["CS", "ETF", "ADR"])
    exchanges: Optional[List[str]] = None
    custom_predicate: Optional[Callable[[Dict[str, Any]], bool]] = None

    def matches(self, row: Dict[str, Any]) -> bool:
        """Evaluates whether a candidate snapshot row satisfies the filter criteria."""
        close = row.get("close")
        if close is None:
            return False

        # Price boundaries
        if self.min_price is not None and close < self.min_price:
            return False
        if self.max_price is not None and close > self.max_price:
            return False

        # Volume & Relative Volume
        volume = row.get("volume") or row.get("vol_sma20")
        if self.min_volume is not None and volume is not None and volume < self.min_volume:
            return False

        rel_vol = row.get("rel_vol")
        if self.min_rel_vol is not None:
            if rel_vol is None or rel_vol < self.min_rel_vol:
                return False
        if self.max_rel_vol is not None:
            if rel_vol is not None and rel_vol > self.max_rel_vol:
                return False

        # RSI thresholds
        rsi_val = row.get("rsi14") or row.get("rsi")
        if self.min_rsi is not None:
            if rsi_val is None or rsi_val < self.min_rsi:
                return False
        if self.max_rsi is not None:
            if rsi_val is None or rsi_val > self.max_rsi:
                return False

        # Moving average alignment
        sma50 = row.get("sma50")
        if self.above_sma50 is not None:
            if sma50 is None:
                return False
            if self.above_sma50 is True and close <= sma50:
                return False
            if self.above_sma50 is False and close >= sma50:
                return False

        sma200 = row.get("sma200")
        if self.above_sma200 is not None:
            if sma200 is None:
                return False
            if self.above_sma200 is True and close <= sma200:
                return False
            if self.above_sma200 is False and close >= sma200:
                return False

        # Trend regime
        regime = row.get("trend_regime")
        if self.trend_regime is not None and regime != self.trend_regime:
            return False

        # 52w high / low distance
        dist_high = row.get("dist_52w_high")
        if self.min_dist_52w_high is not None:
            if dist_high is None or dist_high < self.min_dist_52w_high:
                return False

        dist_low = row.get("dist_52w_low")
        if self.max_dist_52w_low is not None:
            if dist_low is None or dist_low > self.max_dist_52w_low:
                return False

        # 20d return
        ret20 = row.get("ret20")
        if self.min_ret20 is not None:
            if ret20 is None or ret20 < self.min_ret20:
                return False

        # Asset class & exchange filters
        ac = row.get("asset_class")
        if ac and self.asset_classes and ac not in self.asset_classes:
            return False

        ex = row.get("exchange")
        if ex and self.exchanges and ex not in self.exchanges:
            return False

        if self.custom_predicate is not None:
            if not self.custom_predicate(row):
                return False

        return True
