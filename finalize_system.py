"""
finalize_system.py - AQSh Fond Bozori Skrinerini ishlab chiqarish (Production)
holatiga keltiruvchi to'liq avtomatlashtirish skripti.
"""
import sys
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path("C:/us_stock_screener")

# 1. engine.py ni V2 standartiga yangilash
ENGINE_CODE = '''"""
Backtest dvigateli (portfel darajasida).
V2 Standarti: Trend Runner (TP 35%, Max Hold 35 kun).
"""
from collections import defaultdict
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class Params:
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01        # bir savdoda portfelning ulushi sifatida risk
    max_positions: int = 8
    max_pos_pct: float = 0.20     # bitta pozitsiya maksimal ulushi
    stop_atr: float = 2.0         # stop = kirish - stop_atr * ATR
    tp_pct: float = 0.35          # V2: Trend Runner target (+35%)
    max_hold: int = 35            # V2: maksimal ushlash (35 savdo kuni)
    cost_per_side: float = 0.001  # 0.1% har tomonga
    min_notional: float = 20.0


def run_backtest(ohlc: dict, signals: dict, dates, p: Params):
    dates = pd.DatetimeIndex(dates)
    cand_by_date = defaultdict(list)
    for t, sg in signals.items():
        hits = sg[sg["signal"]]
        for d, row in hits.iterrows():
            if np.isfinite(row["atr"]) and row["atr"] > 0 and np.isfinite(row["rank"]):
                cand_by_date[d].append((float(row["rank"]), t, float(row["atr"])))

    cash = p.initial_equity
    last_equity = p.initial_equity
    positions: dict = {}
    pending: list = []
    trades: list = []
    curve = []

    def close_position(t, d, i, exit_raw, reason):
        nonlocal cash
        pos = positions.pop(t)
        exit_fill = exit_raw * (1 - p.cost_per_side)
        cash += pos["shares"] * exit_fill
        trades.append({
            "ticker": t,
            "entry_date": pos["entry_date"],
            "exit_date": d,
            "entry_price": pos["entry_raw"],
            "exit_price": exit_raw,
            "shares": pos["shares"],
            "pnl": pos["shares"] * (exit_fill - pos["entry_fill"]),
            "ret": exit_fill / pos["entry_fill"] - 1,
            "reason": reason,
            "hold_days": i - pos["entry_idx"],
        })

    n = len(dates)
    for i, d in enumerate(dates):
        # 1) Kirishlar (ochilishda)
        for t, atr_v in pending:
            if len(positions) >= p.max_positions:
                break
            if t in positions or t not in ohlc or d not in ohlc[t].index:
                continue
            o = ohlc[t].at[d, "open"]
            if not np.isfinite(o) or o <= 0:
                continue
            stop = o - p.stop_atr * atr_v
            if stop <= 0:
                continue
            fill = o * (1 + p.cost_per_side)
            risk_per_share = fill - stop
            shares = p.risk_pct * last_equity / risk_per_share
            max_notional = min(p.max_pos_pct * last_equity, cash)
            if shares * fill > max_notional:
                shares = max_notional / fill
            if shares * fill < p.min_notional:
                continue
            cash -= shares * fill
            positions[t] = {
                "shares": shares, "entry_fill": fill, "entry_raw": o, "stop": stop,
                "target": o * (1 + p.tp_pct), "entry_date": d, "entry_idx": i,
                "last_close": o,
            }
        pending = []

        # 2) Chiqishlar
        for t in list(positions):
            if d not in ohlc[t].index:
                continue
            r = ohlc[t].loc[d]
            o, h, l, c = r["open"], r["high"], r["low"], r["close"]
            pos = positions[t]
            exit_raw = reason = None
            if pos["entry_idx"] != i:
                if o <= pos["stop"]:
                    exit_raw, reason = o, "SL_GAP"
                elif o >= pos["target"]:
                    exit_raw, reason = o, "TP_GAP"
            if exit_raw is None:
                if l <= pos["stop"]:
                    exit_raw, reason = pos["stop"], "SL"
                elif h >= pos["target"]:
                    exit_raw, reason = pos["target"], "TP"
            if exit_raw is None and i - pos["entry_idx"] >= p.max_hold:
                exit_raw, reason = c, "TIME"
            if exit_raw is not None:
                close_position(t, d, i, exit_raw, reason)
            else:
                pos["last_close"] = c

        # 3) Baholash
        mkt = sum(pos["shares"] * pos["last_close"] for pos in positions.values())
        equity = cash + mkt
        last_equity = equity
        curve.append((d, equity, mkt / equity if equity > 0 else 0.0))

        # 4) Ertangi kun uchun signallar (qat'iy deterministik saralash)
        if i < n - 1:
            cands = [(rk, t, a) for rk, t, a in cand_by_date.get(d, []) if t not in positions]
            cands.sort(key=lambda x: (-x[0], x[1]))
            pending = [(t, a) for _, t, a in cands]

    if n:
        last_i, last_d = n - 1, dates[-1]
        for t in list(positions):
            close_position(t, last_d, last_i, positions[t]["last_close"], "END")
        curve[-1] = (last_d, cash, 0.0)

    tdf = pd.DataFrame(trades)
    cdf = pd.DataFrame(curve, columns=["date", "equity", "invested"]).set_index("date")
    return tdf, cdf
'''

# 2. test_backtest.py ga regressiya testlarini qo'shish
TEST_BACKTEST_CODE = '''"""
test_backtest.py - Simulyator qoidalari va regressiya testlari.
"""
import numpy as np
import pandas as pd
import pytest
from engine import Params, run_backtest


def test_gap_down_execution():
    """Ochilish narxi stopdan past bo'lsa, real ochilishda (SL_GAP) chiqishi shart."""
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 80.0],
            "high":  [105.0, 105.0, 85.0],
            "low":   [98.0,  95.0,  75.0],
            "close": [102.0, 101.0, 82.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False],
            "atr":    [5.0,  5.0,   5.0],
            "rank":   [1.0,  1.0,   1.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    p = Params(initial_equity=10000.0, stop_atr=2.0)
    tr, _ = run_backtest(ohlc, signals, ohlc["AAA"].index, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] == "SL_GAP"
    assert tr.iloc[0]["exit_price"] == 80.0


def test_stop_before_tp_priority():
    """Bir kunda ham Stop, ham Target tegsa, konservativ Stop birinchi bo'lishi shart."""
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 100.0],
            "high":  [105.0, 105.0, 150.0],
            "low":   [98.0,  95.0,   80.0],
            "close": [102.0, 101.0, 105.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False],
            "atr":    [5.0,  5.0,   5.0],
            "rank":   [1.0,  1.0,   1.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    p = Params(initial_equity=10000.0, stop_atr=2.0, tp_pct=0.35)
    tr, _ = run_backtest(ohlc, signals, ohlc["AAA"].index, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] == "SL"
'''

# 3. screen_today.py - Jonli kunlik skriner
SCREENER_CODE = '''"""
screen_today.py - Jonli bozor skrineri.
Bugungi bozor yopilishida yangi tasdiqlangan BREAKOUT signallarini topadi
va ertangi sessiya ochilishi uchun aniq savdo buyurtmalarini shakllantiradi.
"""
import sqlite3
import pandas as pd
import numpy as np
import features as ft
import strategies as st
from engine import Params

def main():
    conn = sqlite3.connect("data/screener.db")
    bench_raw = ft.load_prices(conn, "SPY")
    bench_f = ft.build_features(bench_raw, bench_raw)
    latest_date = bench_f.index[-1]
    
    # Bozor rejimi
    regime = st.regime_series(bench_f)
    is_bull = bool(regime.iloc[-1])
    spy_close = bench_f["close"].iloc[-1]
    spy_sma200 = bench_f["sma200"].iloc[-1]
    
    print("=" * 80)
    print(f"JONLI AQSH BOZORI SKRINER NATIJASI | SANA: {latest_date.date()}")
    print("=" * 80)
    print(f"SPY Narxi: ${spy_close:.2f} | SPY SMA200: ${spy_sma200:.2f}")
    print(f"Bozor Rejimi: {'O\\\'SUVCHI (BULLISH - Savdoga ruxsat)' if is_bull else 'TUSHOVCHI (BEARISH - Kassa rejimida qolish)'}")
    print("-" * 80)
    
    if not is_bull:
        print("[OGOHLANTIRISH] SPY 200 kunlik o'rtachadan pastda. Yangi pozitsiyalar ochilmaydi.")
        conn.close()
        return

    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker")]
    p = Params()
    capital = p.initial_equity
    
    candidates = []
    for t in tickers:
        raw = ft.load_prices(conn, t)
        if len(raw) < 260:
            continue
        f = ft.build_features(raw, bench_raw)
        sig_df = st.breakout_signal(f, regime)
        
        # Bugungi signal bormi?
        if sig_df.index[-1] == latest_date and sig_df["signal"].iloc[-1]:
            atr_v = sig_df["atr"].iloc[-1]
            rank_v = sig_df["rank"].iloc[-1]
            close_v = f["close"].iloc[-1]
            vol_v = f["rel_vol"].iloc[-1]
            
            # Risk va hajm hisobi
            est_entry = close_v
            stop_price = est_entry - (p.stop_atr * atr_v)
            target_price = est_entry * (1 + p.tp_pct)
            risk_per_share = (est_entry * 1.001) - stop_price
            
            if risk_per_share > 0:
                shares = int((p.risk_pct * capital) / risk_per_share)
                max_shares = int((p.max_pos_pct * capital) / est_entry)
                shares = min(shares, max_shares)
                pos_val = shares * est_entry
                
                if pos_val >= p.min_notional:
                    candidates.append({
                        "ticker": t,
                        "rank": rank_v,
                        "close": close_v,
                        "atr": atr_v,
                        "rel_vol": vol_v,
                        "stop": stop_price,
                        "target": target_price,
                        "shares": shares,
                        "notional": pos_val
                    })
    conn.close()

    if not candidates:
        print("Bugun mezonlarga mos yangi Breakout signallari aniqlanmadi.")
        return

    # Nisbiy kuch bo'yicha saralash
    candidates.sort(key=lambda x: -x["rank"])
    top_candidates = candidates[:p.max_positions]
    
    print(f"ANIQLANGAN YUKORI DARADJALI SIGNALLAR (Jami: {len(candidates)}, Tanlangan: {len(top_candidates)}):\\n")
    header = f"{'Ticker':<7} | {'Yopilish':>9} | {'Hajm (Rel)':>10} | {'Stop (-2ATR)':>13} | {'Target (+35%)':>13} | {'Aksiyalar':>9} | {'Hajmi ($)':>10}"
    print(header)
    print("-" * len(header))
    for c in top_candidates:
        print(f"{c['ticker']:<7} | ${c['close']:>8.2f} | {c['rel_vol']:>9.1f}x | ${c['stop']:>12.2f} | ${c['target']:>12.2f} | {c['shares']:>9d} | ${c['notional']:>9.2f}")
    
    print("\\n[KO'RSATMA] Buyurtmalar ertaga bozor ochilishida (Market Open) bajarilishi rejalashtiriladi.")

if __name__ == "__main__":
    main()
'''

def apply_and_verify():
    print("================================================================================")
    print("LOYIHANI YAKUNIY BOSQICHGA O'TKAZISH VA INTEGRATSIYA")
    print("================================================================================")
    
    # 1. engine.py ni saqlash
    with open(ROOT / "engine.py", "w", encoding="utf-8") as f:
        f.write(ENGINE_CODE)
    print("[OK] engine.py yangilandi (V2: TP 35%, Hold 35, deterministik saralash).")

    # 2. test_backtest.py ni saqlash
    with open(ROOT / "test_backtest.py", "w", encoding="utf-8") as f:
        f.write(TEST_BACKTEST_CODE)
    print("[OK] test_backtest.py yangilandi (Gap-Down va Stop ustunligi testlari qo'shildi).")

    # 3. screen_today.py ni yaratish
    with open(ROOT / "screen_today.py", "w", encoding="utf-8") as f:
        f.write(SCREENER_CODE)
    print("[OK] screen_today.py yaratildi (Jonli kunlik skriner).")

    # 4. Pytest testlarini tekshirish
    print("\n--- 1. PYTEST TESTLARINI BAJARISH ---")
    ret = subprocess.run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True)
    print(ret.stdout.strip())
    if ret.returncode != 0:
        print(ret.stderr)
        sys.exit("[XATO] Pytest testlarida muammo!")
    print("[OK] Barcha testlar muvaffaqiyatli o'tdi.")

    # 5. run_backtest.py ni ishga tushirish
    print("\n--- 2. ISHLAB CHIQARISH (PRODUCTION) BACKTESTINI BAJARISH ---")
    subprocess.run([sys.executable, "run_backtest.py"])

    # 6. screen_today.py ni ishga tushirish
    print("\n--- 3. JONLI SKRINERNI ISHGA TUSHIRISH (ERTANGI KUN SIGNALLARI) ---")
    subprocess.run([sys.executable, "screen_today.py"])

if __name__ == "__main__":
    apply_and_verify()