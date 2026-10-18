#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Stock Screener — xavfsiz, faqat-o'qish audit tekshiruvlari.
Foydalanish:
  python us_stock_screener_check.py "C:\\us_stock_screener"
  python us_stock_screener_check.py "C:\\us_stock_screener" --run-tests
  python us_stock_screener_check.py "C:\\us_stock_screener" --run-tests --run-backtests

Eslatma: skript ma'lumotlar bazasini yoki loyiha fayllarini o'zgartirmaydi.
Backtest bayroqlari mavjud skriptlarni ishga tushiradi; natijalar hisobotga qo'shiladi.
"""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", "build", "dist"
}
PRICE_ALIASES = {
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c"),
    "volume": ("volume", "vol", "v"),
}
DATE_NAMES = {"date", "datetime", "timestamp", "trade_date", "session_date"}
TICKER_NAMES = {"ticker", "symbol", "stock", "asset", "security"}


def emit(results: list[dict[str, Any]], area: str, status: str,
         message: str, details: Any = None) -> None:
    item = {"area": area, "status": status, "message": message}
    if details is not None:
        item["details"] = details
    results.append(item)
    print(f"[{status:>8}] {area}: {message}")
    if details is not None:
        print("           " + json.dumps(details, ensure_ascii=False, default=str)[:1200])


def python_syntax_check(root: Path, results: list[dict[str, Any]]) -> None:
    py_files = []
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        py_files.append(p)

    errors = []
    for p in sorted(py_files):
        try:
            source = p.read_text(encoding="utf-8-sig")
            compile(source, str(p), "exec")
        except UnicodeDecodeError:
            try:
                source = p.read_text(encoding="cp1252")
                compile(source, str(p), "exec")
            except Exception as exc:
                errors.append({"file": str(p.relative_to(root)), "error": repr(exc)})
        except Exception as exc:
            errors.append({"file": str(p.relative_to(root)), "error": repr(exc)})

    if errors:
        emit(results, "Python sintaksisi", "FAIL",
             f"{len(errors)} ta faylda xato, jami {len(py_files)} ta fayl tekshirildi",
             errors)
    else:
        emit(results, "Python sintaksisi", "PASS",
             f"{len(py_files)} ta Python fayli sintaksisdan o'tdi")


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def columns_for(conn: sqlite3.Connection, table: str) -> list[str]:
    # Table name comes from sqlite_master and is quoted defensively.
    safe = table.replace('"', '""')
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{safe}")').fetchall()]


def normalized_map(cols: list[str]) -> dict[str, str]:
    return {c.lower().strip(): c for c in cols}


def find_col(mapping: dict[str, str], aliases: set[str]) -> str | None:
    for low, original in mapping.items():
        if low in aliases:
            return original
    return None


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def database_check(db_path: Path, results: list[dict[str, Any]], out_dir: Path) -> None:
    if not db_path.exists():
        emit(results, "SQLite baza", "WARN", f"Baza topilmadi: {db_path}")
        return

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        emit(results, "SQLite integrity_check", "PASS" if integrity == "ok" else "FAIL",
             f"Natija: {integrity}")

        tables = sqlite_tables(conn)
        emit(results, "SQLite jadvallari", "INFO", f"{len(tables)} ta jadval topildi", tables)

        table_summaries = []
        quality_rows = []
        duplicate_findings = []
        ohlc_findings = []

        for table in tables:
            cols = columns_for(conn, table)
            mapping = normalized_map(cols)
            safe_table = quote_ident(table)
            count = conn.execute(f"SELECT COUNT(*) FROM {safe_table}").fetchone()[0]
            table_summaries.append({"table": table, "rows": count, "columns": cols})

            ticker_col = find_col(mapping, TICKER_NAMES)
            date_col = find_col(mapping, DATE_NAMES)
            if ticker_col and date_col:
                q_ticker, q_date = quote_ident(ticker_col), quote_ident(date_col)
                dup_sql = (
                    f"SELECT {q_ticker}, {q_date}, COUNT(*) AS n "
                    f"FROM {safe_table} GROUP BY {q_ticker}, {q_date} HAVING COUNT(*) > 1 LIMIT 100"
                )
                dups = [dict(r) for r in conn.execute(dup_sql).fetchall()]
                if dups:
                    duplicate_findings.append({"table": table, "duplicates_sample": dups})
                emit(results, f"Takroriy kalitlar: {table}",
                     "FAIL" if dups else "PASS",
                     f"(ticker/date) dublikat namunalari: {len(dups)}",
                     dups[:10] if dups else None)

            open_col = find_col(mapping, set(PRICE_ALIASES["open"]))
            high_col = find_col(mapping, set(PRICE_ALIASES["high"]))
            low_col = find_col(mapping, set(PRICE_ALIASES["low"]))
            close_col = find_col(mapping, set(PRICE_ALIASES["close"]))
            vol_col = find_col(mapping, set(PRICE_ALIASES["volume"]))
            if all([open_col, high_col, low_col, close_col]):
                o, h, l, c = map(quote_ident, [open_col, high_col, low_col, close_col])
                predicates = [
                    (f"{o} <= 0", "open<=0"),
                    (f"{h} <= 0", "high<=0"),
                    (f"{l} <= 0", "low<=0"),
                    (f"{c} <= 0", "close<=0"),
                    (f"{l} > {h}", "low>high"),
                    (f"{h} < MAX({o}, {c})", "high<max(open,close)"),
                    (f"{l} > MIN({o}, {c})", "low>min(open,close)"),
                ]
                if vol_col:
                    v = quote_ident(vol_col)
                    predicates.append((f"{v} < 0", "volume<0"))

                ticker_expr = quote_ident(ticker_col) if ticker_col else "NULL"
                date_expr = quote_ident(date_col) if date_col else "NULL"
                for predicate, violation in predicates:
                    sql = (
                        f"SELECT {ticker_expr} AS ticker, {date_expr} AS date, "
                        f"{o} AS open, {h} AS high, {l} AS low, {c} AS close "
                        f"FROM {safe_table} WHERE {predicate} LIMIT 20"
                    )
                    sample = [dict(r) for r in conn.execute(sql).fetchall()]
                    if sample:
                        ohlc_findings.append({
                            "table": table, "violation": violation,
                            "sample_count_capped_at_20": len(sample), "sample": sample
                        })
                        quality_rows.extend([
                            {"table": table, "ticker": x.get("ticker"), "date": x.get("date"),
                             "violation": violation, "open": x.get("open"), "high": x.get("high"),
                             "low": x.get("low"), "close": x.get("close")}
                            for x in sample
                        ])

        emit(results, "SQLite jadval statistikasi", "INFO",
             "Jadvallar va qatorlar sanaldi", table_summaries)

        emit(results, "OHLCV validatsiyasi", "FAIL" if ohlc_findings else "PASS",
             f"Qoidabuzarlik turlari topildi: {len(ohlc_findings)}",
             ohlc_findings[:12] if ohlc_findings else None)

        if quality_rows:
            csv_path = out_dir / "data_quality_findings.csv"
            with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "table", "ticker", "date", "violation", "open", "high", "low", "close"
                ])
                writer.writeheader()
                writer.writerows(quality_rows)
            emit(results, "Data-quality CSV", "INFO", f"Namuna hisobot yozildi: {csv_path}")

        # Detect likely ticker counts from tables containing a ticker/symbol field.
        ticker_stats = []
        for table in tables:
            mapping = normalized_map(columns_for(conn, table))
            ticker_col = find_col(mapping, TICKER_NAMES)
            if ticker_col:
                qcol = quote_ident(ticker_col)
                qtable = quote_ident(table)
                n = conn.execute(
                    f"SELECT COUNT(DISTINCT {qcol}) FROM {qtable} WHERE {qcol} IS NOT NULL"
                ).fetchone()[0]
                ticker_stats.append({"table": table, "distinct_tickers": n, "ticker_column": ticker_col})
        emit(results, "Ticker qamrovi", "INFO", "Ticker ustunlari bo'yicha statistika", ticker_stats)

        conn.close()
    except Exception as exc:
        emit(results, "SQLite audit", "FAIL", f"Baza tekshiruvida xato: {exc!r}")


def static_code_checks(root: Path, results: list[dict[str, Any]]) -> None:
    run_daily = root / "run_daily.py"
    lifecycle = root / "lifecycle.py"
    if not run_daily.exists():
        emit(results, "Kunlik pipeline", "WARN", "run_daily.py topilmadi")
    elif not lifecycle.exists():
        emit(results, "Kunlik pipeline", "WARN", "lifecycle.py topilmadi")
    else:
        rd = run_daily.read_text(encoding="utf-8-sig", errors="replace")
        lc = lifecycle.read_text(encoding="utf-8-sig", errors="replace")
        has_advance_def = bool(re.search(r"def\s+advance_portfolio\s*\(", lc))
        calls_advance = bool(re.search(r"\badvance_portfolio\s*\(", rd))
        if has_advance_def and calls_advance:
            emit(results, "Kunlik pipeline", "INFO",
                 "advance_portfolio() ta'rifi va run_daily.py ichida chaqiruv topildi; bu ishlashini to'liq isbotlamaydi")
        elif has_advance_def:
            emit(results, "Kunlik pipeline", "WARN",
                 "lifecycle.py ichida advance_portfolio() bor, lekin run_daily.py matnida chaqiruv topilmadi")
        else:
            emit(results, "Kunlik pipeline", "WARN",
                 "lifecycle.py ichida advance_portfolio() ta'rifi topilmadi")

    strategy_files = []
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if any(word in text.lower() for word in ("breakout", "high20", "rolling(")):
            strategy_files.append((p, text))

    shifted = []
    unverified = []
    for p, text in strategy_files:
        if re.search(r"(high20|rolling\s*\([^)]*20[^)]*\)).{0,160}shift\s*\(\s*1\s*\)", text, re.I | re.S):
            shifted.append(str(p.relative_to(root)))
        elif "high20" in text.lower() or re.search(r"rolling\s*\([^)]*20", text, re.I):
            unverified.append(str(p.relative_to(root)))
    if shifted:
        emit(results, "Breakout/look-ahead", "INFO",
             "Matnda shift(1) bilan bog'liq ehtimoliy ifoda topildi; semantik to'g'rilikni kod oqimi bilan tekshirish kerak",
             shifted)
    if unverified:
        emit(results, "Breakout/look-ahead", "WARN",
             "20 kunlik maksimumga o'xshash ifoda topildi, lekin shu faylda yonma-yon shift(1) aniqlanmadi",
             unverified)


def run_command(command: list[str], cwd: Path, results: list[dict[str, Any]],
                area: str, timeout: int = 1800) -> None:
    print(f"\n>>> {' '.join(command)}")
    try:
        proc = subprocess.run(
            command, cwd=str(cwd), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, shell=False
        )
        output = proc.stdout[-12000:] if proc.stdout else "(chiqish yo'q)"
        status = "PASS" if proc.returncode == 0 else "FAIL"
        emit(results, area, status, f"Exit code: {proc.returncode}", output)
    except subprocess.TimeoutExpired as exc:
        emit(results, area, "FAIL", f"Vaqt limiti oshdi: {timeout} soniya",
             (exc.stdout or "")[-5000:] if isinstance(exc.stdout, str) else None)
    except Exception as exc:
        emit(results, area, "FAIL", f"Buyruq bajarilmadi: {exc!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="US Stock Screener read-only audit")
    parser.add_argument("project", nargs="?", default=r"C:\us_stock_screener",
                        help="Loyiha papkasi, standart: C:\\us_stock_screener")
    parser.add_argument("--run-tests", action="store_true",
                        help="pytest -q testlarini ishga tushirish")
    parser.add_argument("--run-backtests", action="store_true",
                        help="Mavjud run_backtest.py va run_survivorship_test.py skriptlarini ishga tushirish")
    args = parser.parse_args()

    root = Path(args.project).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"XATO: loyiha papkasi topilmadi: {root}", file=sys.stderr)
        return 2

    out_dir = root / "reports" / "audit_check"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    print("=" * 72)
    print("US STOCK SCREENER — READ-ONLY AUDIT")
    print(f"Loyiha: {root}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Hisobot papkasi: {out_dir}")
    print("=" * 72)

    python_syntax_check(root, results)
    static_code_checks(root, results)

    db_path = root / "data" / "screener.db"
    database_check(db_path, results, out_dir)

    if args.run_tests:
        run_command([sys.executable, "-m", "pytest", "-q"], root, results, "Pytest", timeout=1800)

    if args.run_backtests:
        for name in ("run_backtest.py", "run_survivorship_test.py"):
            if (root / name).exists():
                run_command([sys.executable, name], root, results, f"Backtest: {name}", timeout=3600)
            else:
                emit(results, f"Backtest: {name}", "WARN", "Skript topilmadi, o'tkazib yuborildi")

    report = {
        "generated_at_local": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": str(root),
        "python": sys.version,
        "checks": results,
        "note": "Statik tekshiruvlar kodning to'g'ri ishlashini to'liq isbotlamaydi."
    }
    json_path = out_dir / "audit_results.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"Tekshiruv yakunlandi. JSON hisobot: {json_path}")
    print("Izoh: WARN/FAIL natijalarini kod va ma'lumotlar bilan qo'lda tekshirish kerak.")
    print("=" * 72)

    return 1 if any(x["status"] == "FAIL" for x in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
