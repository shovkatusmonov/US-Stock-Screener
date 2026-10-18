"""
run_daily.py - Kundalik Yagona Savdo Orkestratori (To'liq ishlab chiqarish kodi).
Vazifalari:
1. Ma'lumot yangiligi (Data Freshness) va SPY bozor rejimini tekshirish.
2. Virtual portfel holati, mavjud naqd pul, ochiq pozitsiyalar va Heat riskini hisoblash.
3. Bozor bull rejimida bo'lsa, yangi Breakout V2 signallarini skanerlash.
4. Yangi savdo rejalarini TradeManager bazasiga kiritish va ogohlantirish berish.
5. Bazadagi avvaldan mavjud barcha PLANNED rejalar va yangi rejalarni jamlab,
   yagona professional hisobot shakllantirish.
"""
import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path

import features as ft
import strategies as st
from risk_manager import RiskConfig, create_trade_plan, reward_risk
from trade_manager import TradeManager
from portfolio_tracker import PortfolioTracker
from reporting import generate_full_report
from alerts import AlertManager
from lifecycle import advance_portfolio


def run_daily_pipeline(db_path: str = "data/screener.db", max_data_age_days: int = 5, max_hold: int = 35):
    print("=" * 85)
    print("KUNDALIK SAVDO ORKESTRATORI (RUN_DAILY PIPELINE)")
    print("=" * 85)

    conn = sqlite3.connect(db_path)

    # 1. DATA FRESHNESS & BENCHMARK AUDITI
    bench_raw = ft.load_prices(conn, "SPY")
    if bench_raw.empty:
        print("[KRITIK XATO] SPY benchmark kotirovkalari topilmadi. Pipeline to'xtatildi.")
        conn.close()
        return False

    bench_f = ft.build_features(bench_raw, bench_raw)
    latest_date = bench_f.index[-1]
    today_dt = datetime.now()
    data_age_days = (today_dt.date() - latest_date.date()).days

    print(f"Baza oxirgi sessiya sanasi: {latest_date.date()} (Eskirganlik: {data_age_days} kun)")

    if data_age_days > 365:
        print(f"[OGOHLANTIRISH] Ma'lumot sanasi arxiv rejimida: {latest_date.date()}")
    elif data_age_days > max_data_age_days:
        print(f"[BLOKLANDI] Kotirovkalar eskirgan ({data_age_days} kun oldingi). Yangi rejalar tuzilmaydi.")
        conn.close()
        return False

    regime = st.regime_series(bench_f)
    is_bull = bool(regime.iloc[-1])
    spy_close = bench_f["close"].iloc[-1]
    spy_sma200 = bench_f["sma200"].iloc[-1]
    print(f"SPY: ${spy_close:.2f} | 200 SMA: ${spy_sma200:.2f} | Rejim: {'BULLISH' if is_bull else 'BEARISH'}")
    print("-" * 85)

    # 2. VIRTUAL PORTFEL VA TRADE MANAGER HOLATI
    tm = TradeManager(db_path=db_path)
    pt = PortfolioTracker(tm)
    cfg = RiskConfig()
    alert_mgr = AlertManager(db_path=db_path)

    # 2a. PORTFELNI YURGIZISH: kechagi rejalar bugungi ochilishda bajariladi / bekor qilinadi,
    #     ochiq pozitsiyalarga bugungi bar qo'llanadi (Stop/Target/Time). Bozor ayiq bo'lsa ham ishlaydi.
    events = advance_portfolio(tm, conn, str(latest_date.date()), cfg, max_hold=max_hold)
    for e in events["filled"]:
        print(f"[FILL]   {e['ticker']:<6} {e['date']} ochilish ${e['open']:.2f} -> pozitsiya ochildi")
        alert_mgr.send_alert(f"{e['signal_id']}_ENTRY", e["ticker"], "POSITION_OPENED", "INFO",
                             f"Pozitsiya ochildi: ochilish ${e['open']:.2f}")
    for e in events["cancelled"]:
        print(f"[BEKOR]  {e['ticker']:<6} {e['reason']}")
        alert_mgr.send_alert(f"{e['signal_id']}_CANCELLED", e["ticker"], "PLAN_CANCELLED", "INFO", e["reason"])
    for e in events["closed"]:
        print(f"[CHIQISH] {e['ticker']:<6} {e['date']} {e['reason']} ${e['exit_price']:.2f} PnL ${e['pnl']:.2f}")
        alert_mgr.send_alert(f"{e['signal_id']}_EXIT", e["ticker"], "POSITION_CLOSED",
                             "WARNING" if e["reason"].startswith("SL") else "INFO",
                             f"{e['reason']} ${e['exit_price']:.2f}, PnL ${e['pnl']:.2f}")

    open_tickers = [r["ticker"] for r in tm._get_conn().execute(
        "SELECT DISTINCT ticker FROM portfolio_trades WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
    ).fetchall()]

    current_prices = {}
    for t in open_tickers:
        r = conn.execute("SELECT close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1", (t,)).fetchone()
        if r:
            current_prices[t] = float(r[0])

    portfolio_snap = pt.get_portfolio_snapshot(current_prices)
    current_equity = portfolio_snap["total_equity"]
    current_heat = portfolio_snap["portfolio_heat_pct"]
    open_count = portfolio_snap["open_count"]

    print(f"Joriy Portfel: Equity=${current_equity:.2f} | Cash=${portfolio_snap['cash']:.2f} | Ochiq Pozitsiyalar={open_count} ta | Heat={current_heat*100:.1f}%")
    print("-" * 85)

    # 3. BOZOR REJIMI VA LIMITLAR NAZORATI
    if not is_bull:
        print("[BLOKLANDI] Bozor ayiq rejimida. Yangi savdo rejalari shakllantirilmaydi.")
        generate_full_report(
            str(today_dt.date()), str(latest_date.date()),
            {"is_bull": False, "spy_close": spy_close, "spy_sma200": spy_sma200},
            [], portfolio_snap,
            conn.execute("SELECT COUNT(DISTINCT ticker) FROM prices WHERE ticker != 'SPY'").fetchone()[0],
            events=events
        )
        conn.close()
        return True

    # 4. SKRINING VA YANGI SIGNALLARNI SARALASH
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker")]
    candidates = []
    for t in tickers:
        raw = ft.load_prices(conn, t)
        if len(raw) < 260:
            continue
        f = ft.build_features(raw, bench_raw)
        sig_df = st.breakout_signal(f, regime)
        if sig_df.index[-1] == latest_date and sig_df["signal"].iloc[-1]:
            candidates.append((sig_df["rank"].iloc[-1], t, f["close"].iloc[-1], sig_df["atr"].iloc[-1], f["rel_vol"].iloc[-1]))
    conn.close()

    candidates.sort(key=lambda x: -x[0])
    accepted_plans = []
    running_heat = current_heat
    for rank_v, t, close_v, atr_v, rel_vol in candidates:
        if open_count + len(accepted_plans) >= cfg.max_positions:
            break

        plan, msg = create_trade_plan(t, close_v, atr_v, rank_v, latest_date.date(), cfg, current_equity, running_heat)
        if plan is not None:
            plan["rel_vol"] = rel_vol
            ok, sig_id = tm.add_planned_trade(plan, strategy_version="V2_BREAKOUT")
            if ok:
                accepted_plans.append(plan)
                running_heat += plan["risk_pct"]
                alert_mgr.send_alert(
                    alert_id=sig_id,
                    ticker=t,
                    alert_type="NEW_TRADE_PLAN",
                    severity="INFO",
                    message=f"Breakout rejasi tuzildi: Kirish ${plan['signal_price']:.2f}, Stop ${plan['planned_stop']:.2f}"
                )

    # 5. BAZADAGI MAVJUD KUTILAYOTGAN (PLANNED) REJALARNI YUKLASH
    with tm._get_conn() as conn_tm:
        pending_db_rows = conn_tm.execute("""
            SELECT ticker, planned_entry, initial_stop, initial_target, shares, planned_risk, signal_date
            FROM portfolio_trades WHERE status = 'PLANNED'
        """).fetchall()

    all_active_plans = list(accepted_plans)
    existing_planned_tickers = {p["ticker"] for p in accepted_plans}

    for pr in pending_db_rows:
        if pr["ticker"] not in existing_planned_tickers:
            all_active_plans.append({
                "ticker": pr["ticker"],
                "signal_price": pr["planned_entry"],
                "min_open": pr["planned_entry"] * (1 - cfg.max_allowed_gap_pct),
                "max_open": pr["planned_entry"] * (1 + cfg.max_allowed_gap_pct),
                "planned_stop": pr["initial_stop"],
                "planned_target": pr["initial_target"],
                "shares": pr["shares"],
                "dollar_risk": pr["planned_risk"],
                "risk_pct": cfg.risk_per_trade_pct,
                "rr": reward_risk(pr["planned_entry"], pr["initial_stop"], pr["initial_target"], cfg.cost_per_side),
                "confidence": "ACTIVE_PENDING_PLAN"
            })

    # 6. PROFESSIONAL HISOBOT GENERATSIYASI VA CHIQARISH
    rep_text = generate_full_report(
        report_date=str(today_dt.date()),
        data_date=str(latest_date.date()),
        bench_info={"is_bull": is_bull, "spy_close": spy_close, "spy_sma200": spy_sma200},
        candidates=all_active_plans,
        open_positions_summary=portfolio_snap,
        total_evaluated=len(tickers),
        events=events
    )
    print(rep_text)
    return True


if __name__ == "__main__":
    run_daily_pipeline()