"""
audit_suite.py - US Stock Screener mustaqil audit skripti.

Tekshiruvlar:
1. Ma'lumotlar bazasi va takroriy (ticker, date) yozuvlar.
2. 40% dan katta kunlik narx sakrashlarini hajmga qarab tasniflash.
3. Rolling high misolida shift zarurligini ko'rsatish.
4. S&P 500 tarixiy a'zolik oynasi ichidagi narxlar qamrovi.

Eslatma:
- Audit narxlarni o'zgartirmaydi va bazadan qator o'chirmaydi.
- pit_window_coverage(conn, membership_rules_dict) DataFrame qaytaradi
  (testlar va ticker bo'yicha batafsil tahlil uchun).
- pit_window_coverage(conn, history_manager) umumiy summary dict qaytaradi.
"""

import os
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

DB_PATH = os.path.join("data", "screener.db")
UNIVERSE_PATH = os.path.join("data", "universe_sp500.csv")


def audit_database_integrity():
    print("=" * 60)
    print("1. MA'LUMOTLAR BAZASI VA STRUKTURA AUDITI")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"[XATO] Baza topilmadi: {DB_PATH}")
        return None

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"Mavjud jadvallar: {tables}")

        price_table = "prices" if "prices" in tables else (
            tables[0] if tables else None
        )
        if not price_table:
            print("[XATO] Baza bo'sh!")
            return None

        cursor.execute(f"SELECT COUNT(DISTINCT ticker) FROM {price_table}")
        ticker_count = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM {price_table}")
        total_rows = cursor.fetchone()[0]
        print(
            f"Jadval: {price_table} | Tickerlar soni: "
            f"{ticker_count} | Jami qatorlar: {total_rows}"
        )

        cursor.execute(
            f"""
            SELECT ticker, date, COUNT(*)
            FROM {price_table}
            GROUP BY ticker, date
            HAVING COUNT(*) > 1
            """
        )
        duplicates = cursor.fetchall()
        if duplicates:
            print(
                f"[OGOHLANTIRISH] Takroriy qatorlar topildi: "
                f"{len(duplicates)} ta!"
            )
        else:
            print("[OK] Takroriy qatorlar (ticker + date) mavjud emas.")

        return price_table
    finally:
        conn.close()


def _membership_rules(conn):
    """Membership jadvalidan {ticker: [(start_date, end_date), ...]} lug'atini oladi."""
    try:
        rows = conn.execute(
            """
            SELECT ticker, start_date, end_date
            FROM sp500_historical_membership
            """
        ).fetchall()
    except sqlite3.Error:
        return {}

    rules = {}
    for ticker, start_date, end_date in rows:
        rules.setdefault(ticker, []).append((start_date, end_date))
    return rules


def _in_window(ticker, date, rules):
    date_str = str(date)[:10]
    for start_date, end_date in rules.get(ticker, []):
        if start_date <= date_str and (
            end_date is None or date_str <= end_date
        ):
            return True
    return False


def classify_jumps(df, rules=None, thr=0.40, market_wide_n=20):
    """
    Katta kunlik harakatlarni hajm va bozor kontekstiga qarab tasniflaydi.

    Kutiladigan ustunlar: ticker, date, close, volume.
    """
    if df.empty:
        result = df.copy()
        for col in [
            "prev_close", "ret", "vol_med20", "vol_ratio",
            "n_big_same_day", "class",
        ]:
            if col not in result:
                result[col] = pd.Series(dtype="object")
        if rules is not None:
            result["in_window"] = pd.Series(dtype=bool)
        return result

    d = df.sort_values(["ticker", "date"]).reset_index(drop=True).copy()
    d["date"] = pd.to_datetime(d["date"])
    grouped = d.groupby("ticker", sort=False)
    d["prev_close"] = grouped["close"].shift(1)
    d["ret"] = d["close"] / d["prev_close"] - 1.0
    d["vol_med20"] = grouped["volume"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=5).median()
    )
    d["vol_ratio"] = (
        d["volume"] / d["vol_med20"].replace(0, np.nan)
    )
    big_per_day = (
        d[d["ret"].abs() >= 0.15]
        .groupby("date")["ticker"]
        .nunique()
    )
    jumps = d[d["ret"].abs() >= thr].copy()
    jumps["n_big_same_day"] = (
        jumps["date"].map(big_per_day).fillna(0).astype(int)
    )
    volume_price_product = jumps["vol_ratio"] * (1.0 + jumps["ret"])

    def classify(index):
        row = jumps.loc[index]
        volume_ratio = row["vol_ratio"]
        product = volume_price_product.loc[index]

        if row["n_big_same_day"] >= market_wide_n:
            return "MARKET_WIDE"

        if (
            np.isfinite(volume_ratio)
            and np.isfinite(product)
            and product > 0
            and abs(np.log(product)) <= 0.29
            and (volume_ratio >= 1.5 or volume_ratio <= 0.67)
        ):
            return "SPLIT_SUSPECT"

        if np.isfinite(volume_ratio) and volume_ratio >= 3.0:
            return "EVENT_VOLUME"

        if min(row["prev_close"], row["close"]) < 2.0:
            return "PENNY_OR_REUSED"

        return "GLITCH_SUSPECT"

    jumps["class"] = [classify(index) for index in jumps.index]
    if rules is not None:
        jumps["in_window"] = [
            _in_window(ticker, date, rules)
            for ticker, date in zip(jumps["ticker"], jumps["date"])
        ]
    return jumps


def audit_price_jumps(price_table):
    print("\n" + "=" * 60)
    print("2. NARX SAKRASHLARI (>40%) - HAJMGA ASOSLANGAN TASNIF")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT ticker, date, close, volume
            FROM {price_table}
            ORDER BY ticker, date
            """,
            conn,
        )
        rules = _membership_rules(conn)
    finally:
        conn.close()

    jumps = classify_jumps(df, rules if rules else None)
    print(f"Jami >40% sakrashlar soni: {len(jumps)}")
    if jumps.empty:
        print("[OK] 40% dan katta sakrashlar topilmadi.")
        return

    if "in_window" in jumps.columns:
        table = pd.crosstab(
            jumps["class"],
            jumps["in_window"].map({
                True: "a'zolik oynasida",
                False: "oynadan tashqarida",
            }),
        )
    else:
        table = jumps["class"].value_counts().to_frame("soni")
    print(table.to_string())
    print(
        "\nIzoh: faqat a'zolik oynasidagi sakrashlar tegishli "
        "tarixiy backtestga ta'sir qilishi mumkin."
    )

    suspicious = jumps[
        jumps["class"].isin(["SPLIT_SUSPECT", "GLITCH_SUSPECT"])
    ]
    if "in_window" in suspicious.columns:
        suspicious = suspicious[suspicious["in_window"]]

    print(
        "\nTEKSHIRISH KERAK (SPLIT_SUSPECT / GLITCH_SUSPECT, "
        f"oyna ichida): {len(suspicious)} ta"
    )
    for row in suspicious.itertuples():
        volume_ratio = (
            "n/a"
            if not np.isfinite(row.vol_ratio)
            else f"{row.vol_ratio:.1f}x"
        )
        print(
            f"  [{row.ticker}] {row.date.date()} | "
            f"{row.prev_close:.2f} -> {row.close:.2f} "
            f"({row.ret * 100:+.1f}%) | hajm {volume_ratio} | "
            f"{getattr(row, 'class')}"
        )


def pit_window_coverage(
    conn: sqlite3.Connection,
    rules=None,
    start_date: str = "2020-01-01",
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Har bir PIT ticker uchun a'zolik oynasi ichidagi kotirovkalar qamrovini
    faqat SPY savdo kalendari sanalari bilan kesishtirgan holda hisoblaydi.
    'ratio', 'coverage_pct' va 'outside_rows' bilan birga to'liq DataFrame qaytaradi.
    """
    from sp500_history import SP500HistoryManager

    if rules is None:
        hm = SP500HistoryManager()
        rule_dict = hm.membership_rules
    elif isinstance(rules, dict):
        rule_dict = rules
        hm = SP500HistoryManager(complete=True)
        hm.membership_rules = rules
    elif hasattr(rules, "membership_rules"):
        hm = rules
        rule_dict = hm.membership_rules
    else:
        rule_dict = {}
        hm = SP500HistoryManager(complete=True)

    q = "SELECT date FROM prices WHERE ticker = 'SPY' AND date >= ?"
    params: list = [start_date]
    if end_date:
        q += " AND date <= ?"
        params.append(end_date)
    q += " ORDER BY date ASC"

    spy_dates = [r[0][:10] for r in conn.execute(q, params).fetchall()]
    if not spy_dates:
        cols = ["ticker", "expected", "have", "missing", "coverage_pct", "ratio", "outside_rows"]
        return pd.DataFrame(columns=cols)

    spy_idx = pd.to_datetime(spy_dates)
    spy_date_set = set(spy_dates)

    cur = conn.cursor()
    cur.execute("SELECT ticker, substr(date, 1, 10) FROM prices")
    dates_by_ticker: dict[str, set[str]] = {}
    total_rows_by_ticker: dict[str, int] = {}
    for t, d_s in cur.fetchall():
        total_rows_by_ticker[t] = total_rows_by_ticker.get(t, 0) + 1
        if d_s in spy_date_set:
            dates_by_ticker.setdefault(t, set()).add(d_s)

    rows = []
    for t in sorted(rule_dict.keys()):
        mask = hm.membership_mask(t, spy_idx)
        exp_t = int(mask.sum())
        tot_rows = total_rows_by_ticker.get(t, 0)
        if exp_t == 0 and tot_rows == 0:
            continue
            
        active_spy_dates = {spy_dates[i] for i, flag in enumerate(mask) if flag}
        t_dates = dates_by_ticker.get(t, set())
        have_t = len(active_spy_dates & t_dates)
        missing_t = exp_t - have_t
        cov_pct = (have_t / exp_t) if exp_t > 0 else 0.0
        outside_rows = tot_rows - have_t

        rows.append({
            "ticker": t,
            "expected": exp_t,
            "have": have_t,
            "missing": missing_t,
            "coverage_pct": cov_pct,
            "ratio": cov_pct,
            "outside_rows": outside_rows,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        cols = ["ticker", "expected", "have", "missing", "coverage_pct", "ratio", "outside_rows"]
        df = pd.DataFrame(columns=cols)
    return df


def audit_pit_window_coverage():
    print("\n" + "=" * 60)
    print("4. PIT QAMROV: A'ZOLIK OYNASI ICHIDAGI NARXLAR")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"[XATO] Baza topilmadi: {DB_PATH}")
        return

    from sp500_history import SP500HistoryManager

    conn = sqlite3.connect(DB_PATH)
    try:
        rules = _membership_rules(conn)
        if not rules:
            print(
                "[OGOHLANTIRISH] sp500_historical_membership jadvali "
                "yo'q (build_pit_membership.py ni ishga tushiring)."
            )
            return

        # Manager obyektidan foydalaniladi
        manager = SP500HistoryManager(db_path=DB_PATH)
        cov = pit_window_coverage(conn, manager)

        if isinstance(cov, pd.DataFrame):
            expected = int(cov["expected"].sum()) if not cov.empty else 0
            have = int(cov["have"].sum()) if not cov.empty else 0
            pct = (have / expected * 100) if expected > 0 else 0.0

            full_tickers = cov[cov["ratio"] >= 0.95]["ticker"].tolist() if not cov.empty else []
            partial_df = cov[(cov["ratio"] < 0.95) & (cov["have"] > 0)] if not cov.empty else pd.DataFrame()
            zero_tickers = cov[(cov["expected"] > 0) & (cov["have"] == 0)]["ticker"].tolist() if not cov.empty else []
            outside = cov[(cov["have"] == 0) & (cov["outside_rows"] > 0)].set_index("ticker")["outside_rows"].to_dict() if not cov.empty else {}
            partial = [(r.ticker, (int(r.have), int(r.expected))) for r in partial_df.itertuples()]
        else:
            expected = cov["expected_slots"]
            have = cov["have_slots"]
            pct = cov["coverage_pct"] * 100
            full_tickers = cov["full_tickers"]
            zero_tickers = cov["zero_tickers"]
            outside = cov.get("outside_only_tickers", {})
            partial = sorted(
                cov["partial_tickers"].items(),
                key=lambda item: item[1][0] / item[1][1] if item[1][1] else 1.0,
            )

        print(
            f"Oyna ichidagi umumiy qamrov: {have:,} / "
            f"{expected:,} = {pct:.2f}%"
        )
        print(
            f"Tickerlar: to'liq (>=95%) {len(full_tickers)}, "
            f"qisman {len(partial)}, "
            f"oynada narx yo'q {len(zero_tickers)}"
        )

        print(
            "\nBazadagi qatorlari bor, lekin a'zolik oynasida "
            f"narx yo'q: {len(outside)} ta"
        )
        for ticker, row_count in sorted(
            outside.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            print(
                f"  {ticker:<7} oyna ichida 0 kun, "
                f"oynadan tashqaridagi qatorlar: {row_count}"
            )

        print(
            "Bular a'zolik maskasi orqali backtestdan chiqarilishi kerak; "
            "lekin ma'lumotlarni tekshirmasdan o'chirmang."
        )

        if partial:
            print("\nQisman qamrov (eng past 15 ta):")
            for ticker, (ticker_have, ticker_expected) in partial[:15]:
                ratio = (
                    ticker_have / ticker_expected
                    if ticker_expected
                    else 0.0
                )
                print(
                    f"  {ticker:<7} {ticker_have}/{ticker_expected} kun "
                    f"({ratio * 100:.0f}%)"
                )
    finally:
        conn.close()


def audit_lookahead_logic():
    print("\n" + "=" * 60)
    print("3. LOOK-AHEAD BIAS VA INDIKATORLAR MANTIG'I AUDITI")
    print("=" * 60)

    test_dates = pd.date_range("2024-01-01", periods=5)
    test_df = pd.DataFrame(
        {
            "high": [100.0, 102.0, 101.0, 105.0, 103.0],
            "close": [99.0, 101.0, 100.0, 104.0, 102.0],
        },
        index=test_dates,
    )

    wrong_breakout = test_df["high"].rolling(3).max()
    correct_breakout = test_df["high"].shift(1).rolling(3).max()

    comparison = pd.DataFrame(
        {
            "High": test_df["high"],
            "No_Shift (Xato)": wrong_breakout,
            "With_Shift (To'g'ri)": correct_breakout,
        }
    )
    print("Solishtirish: 3 kunlik High Breakout:")
    print(comparison)
    print(
        "\n[QOIDA] Agar strategiya 'High > No_Shift' deb tekshirsa, "
        "u hech qachon True bo'lmaydi,"
    )
    print(
        "chunki High o'zining rolling oynasining ichida bo'ladi!"
    )


def main():
    price_table = audit_database_integrity()
    if price_table:
        audit_price_jumps(price_table)
    audit_lookahead_logic()
    if price_table:
        audit_pit_window_coverage()


if __name__ == "__main__":
    main()
