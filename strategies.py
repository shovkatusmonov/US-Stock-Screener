"""
3-qadam: ikki oddiy strategiya signali (parametrlar minimal, overfitting oldini olish uchun).

Signal t kuni YOPILISHDA hisoblanadi. Kirish eng erta t+1 kuni ochilishida (engine.py).

A) Pullback: o'suvchi trend ichida SMA20 ga qaytish va birinchi "bounce" kuni.
B) Breakout: o'suvchi trend ichida 20 kunlik maksimumdan chiqish, hajm bilan tasdiqlangan.

Ikkala strategiya uchun umumiy filtr:
- trend: close > SMA50 > SMA200
- bozor rejimi: SPY close > SPY SMA200
- likvidlik: 20 kunlik o'rtacha dollar aylanma >= min_dollar_vol, narx >= min_price
- ma'lumot yetarli (rs60, atr14 mavjud)
"""
import pandas as pd


def regime_series(bench_f: pd.DataFrame) -> pd.Series:
    """True = bozor o'suvchi rejimda (SPY > SMA200). NaN solishtirish -> False."""
    return bench_f["close"] > bench_f["sma200"]


def _base(f, regime, min_dollar_vol, min_price) -> pd.Series:
    reg = regime.reindex(f.index).fillna(False).astype(bool)
    return (
        (f["dollar_vol20"] >= min_dollar_vol)
        & (f["raw_close"] >= min_price)
        & f["trend_up"].astype(bool)
        & reg
        & f["rs60"].notna()
        & f["atr14"].notna()
    )


def _pack(f: pd.DataFrame, cond: pd.Series) -> pd.DataFrame:
    atr_series = f["atr_tradable"] if "atr_tradable" in f.columns else f["atr14"]
    return pd.DataFrame(
        {"signal": cond.fillna(False).astype(bool), "atr": atr_series, "rank": f["rs60"]},
        index=f.index,
    )


def pullback_signal(f, regime, min_dollar_vol=20e6, min_price=10.0, rsi_lo=30.0, rsi_hi=55.0):
    cond = (
        _base(f, regime, min_dollar_vol, min_price)
        & (f["close"] <= f["sma20"])
        & (f["close"] > f["sma50"])
        & f["rsi14"].between(rsi_lo, rsi_hi)
        & (f["close"] > f["close"].shift(1))
    )
    return _pack(f, cond)


def breakout_signal(f, regime, min_dollar_vol=20e6, min_price=10.0, min_rel_vol=1.5):
    cond = (
        _base(f, regime, min_dollar_vol, min_price)
        # high20 allaqachon shift(1) qilingan (t kunigacha bo'lgan 20 kunlik maksimum) -> qayta siljitilmaydi
        & (f["close"] > f["high20"])
        & (f["rel_vol"] >= min_rel_vol)
    )
    return _pack(f, cond)


STRATEGIES = {"pullback": pullback_signal, "breakout": breakout_signal}
