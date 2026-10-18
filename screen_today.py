import sqlite3
from pathlib import Path

import numpy as np
import features as ft
import strategies as st
from risk_manager import RiskConfig, create_trade_plan


DB_PATH = Path("data/screener.db")


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Ma'lumotlar bazasi topilmadi: {DB_PATH.resolve()}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        bench_raw = ft.load_prices(conn, "SPY")
        if bench_raw.empty or len(bench_raw) < 200:
            print("[XATO] SPY uchun yetarli tarixiy ma'lumot yo'q.")
            return

        bench_f = ft.build_features(bench_raw, bench_raw)
        if bench_f.empty or not np.isfinite(bench_f["sma200"].iloc[-1]):
            print("[XATO] SPY SMA200 hali hisoblanmagan.")
            return

        latest_date = bench_f.index[-1]
        regime = st.regime_series(bench_f)
        is_bull = bool(regime.iloc[-1])
        spy_close = float(bench_f["close"].iloc[-1])
        spy_sma200 = float(bench_f["sma200"].iloc[-1])

        print("=" * 88)
        print(f"AQSH BOZORI — OLDINDAN SAVDO REJASI | SANA: {latest_date.date()}")
        print("=" * 88)
        print(f"SPY: ${spy_close:.2f} | SMA200: ${spy_sma200:.2f}")
        print(f"Bozor rejimi: {'BULLISH' if is_bull else 'BEARISH — yangi rejalar bloklangan'}")
        print("-" * 88)
        if not is_bull:
            print("[BLOKLANDI] SPY SMA200 dan pastda. Yangi savdo rejalari tuzilmaydi.")
            return

        tickers = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker"
            )
        ]
        cfg = RiskConfig()
        current_equity = cfg.initial_equity
        current_heat = 0.0
        candidates = []

        for ticker in tickers:
            raw = ft.load_prices(conn, ticker)
            if len(raw) < 260:
                continue
            f = ft.build_features(raw, bench_raw)
            if f.empty or latest_date not in f.index:
                continue
            sig = st.breakout_signal(f, regime)
            if latest_date not in sig.index or not bool(sig.at[latest_date, "signal"]):
                continue

            atr_value = sig.at[latest_date, "atr"]
            rank_value = sig.at[latest_date, "rank"]
            close_value = f.at[latest_date, "close"]
            rel_vol = f.at[latest_date, "rel_vol"]
            if not all(np.isfinite(v) for v in (atr_value, rank_value, close_value)):
                continue
            candidates.append((float(rank_value), ticker, float(close_value),
                               float(atr_value), float(rel_vol) if np.isfinite(rel_vol) else np.nan))

        if not candidates:
            print("Bugun mezonlarga mos Breakout signali topilmadi.")
            return

        candidates.sort(key=lambda x: (-x[0], x[1]))
        accepted, blocked = [], []
        for rank, ticker, close, atr_value, rel_vol in candidates:
            plan, message = create_trade_plan(
                ticker, close, atr_value, rank, latest_date.date(),
                cfg, current_equity, current_heat
            )
            if plan is None:
                blocked.append((ticker, message))
                continue
            plan["rel_vol"] = rel_vol
            accepted.append(plan)
            current_heat += plan["risk_pct"]
            if len(accepted) >= cfg.max_positions:
                break

        print(f"Rejalar: qabul qilindi {len(accepted)} | bloklandi {len(blocked)}")
        print(f"Rejalashtirilgan risk: {current_heat * 100:.2f}% "
              f"(limit {cfg.max_portfolio_risk_pct * 100:.2f}%)")
        if accepted:
            print()
            print(f"{'Ticker':<7} {'Signal':>10} {'Open min':>10} {'Open max':>10} "
                  f"{'Stop':>10} {'Target':>10} {'Shares':>8} {'Risk $':>10} {'RS':>8}")
            for p in accepted:
                print(f"{p['ticker']:<7} {p['signal_price']:>10.2f} "
                      f"{p['min_open_allowed']:>10.2f} {p['max_open_allowed']:>10.2f} "
                      f"{p['planned_stop']:>10.2f} {p['planned_target']:>10.2f} "
                      f"{p['shares']:>8d} {p['dollar_risk']:>10.2f} {p['rank']:>8.3f}")
        if blocked:
            print("\nBloklangan rejalar:")
            for ticker, message in blocked:
                print(f" - {ticker}: {message}")
        print("\nEslatma: bu faqat reja; brokerga buyurtma yuborilmaydi.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
