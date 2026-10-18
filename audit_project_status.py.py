
from pathlib import Path
from datetime import datetime
import sqlite3
import subprocess
import sys
import json
import re

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "audit_reports"
REPORT_DIR.mkdir(exist_ok=True)

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", "site-packages", "audit_reports"
}

# Har bir yo'nalish uchun qidiriladigan belgilar.
PLANS = {
    "01_project_structure": [
        "run_daily.py", "main.py", "config.py", "requirements.txt",
        "pyproject.toml", "pytest.ini", "README.md"
    ],
    "02_technical_analysis": [
        "technical", "indicator", "sma", "rsi", "atr",
        "breakout", "pullback"
    ],
    "03_strategies_backtest": [
        "strategy", "strategies", "backtest", "engine",
        "breakout", "pullback", "portfolio"
    ],
    "04_risk_management": [
        "risk", "stop_loss", "position_size", "drawdown",
        "risk_manager", "heat"
    ],
    "05_fundamental_SEC": [
        "fundamental", "sec", "edgar", "earnings",
        "revenue", "eps", "roe", "debt"
    ],
    "06_macro_economics": [
        "macro", "fred", "cpi", "fomc", "treasury",
        "yield", "economic", "regime"
    ],
    "07_news_social_AI": [
        "news", "catalyst", "social", "sentiment",
        "headline", "event", "gemini", "openai"
    ],
    "08_scoring_screener": [
        "score", "screener", "ranking", "signal",
        "universe", "scanner"
    ],
    "09_paper_trading": [
        "paper", "virtual", "trade_journal", "order",
        "execution", "broker", "lifecycle"
    ],
    "10_scheduler_24_7": [
        "scheduler", "cron", "monitor", "healthcheck",
        "retry", "logging", "backup", "service"
    ],
    "11_data_quality_PIT": [
        "point_in_time", "pit", "survivorship",
        "corporate_action", "delisted", "data_quality"
    ],
    "12_reports_alerts": [
        "report", "telegram", "alert", "notification",
        "dashboard", "export"
    ],
    "13_tests": [
        "test_", "_test.py", "pytest", "unittest"
    ],
}

def section(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)

def project_files():
    result = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part.lower() in SKIP_DIRS for part in rel.parts):
            continue
        result.append(p)
    return result

def safe_text(path):
    try:
        if path.stat().st_size > 2_000_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

def scan_plans(files):
    output = {}
    for plan, terms in PLANS.items():
        matches = []
        for p in files:
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            name = p.name.lower()
            if any(term.lower() in name for term in terms):
                matches.append(rel)
                continue

            # Kod ichidan qidirish faqat matnli fayllarda.
            if p.suffix.lower() in {
                ".py", ".toml", ".ini", ".yaml", ".yml",
                ".json", ".md", ".txt", ".ps1"
            }:
                text = safe_text(p).lower()
                if text and any(term.lower() in text for term in terms):
                    matches.append(rel)

        output[plan] = sorted(set(matches))[:100]
    return output

def audit_databases(files):
    results = []
    for db in files:
        if db.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue

        item = {
            "file": str(db.relative_to(ROOT)),
            "size_bytes": db.stat().st_size,
            "status": "UNKNOWN",
            "tables": {},
            "errors": []
        }
        try:
            # mode=ro — bazaga yozishni taqiqlaydi.
            uri = db.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=5)
            cur = con.cursor()
            item["status"] = "READ_ONLY_OPEN_OK"

            tables = cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """).fetchall()

            for (table,) in tables:
                # Jadval nomi SQLite katalogidan olingan va qo'shtirnoqqa olindi.
                escaped = table.replace('"', '""')
                try:
                    count = cur.execute(
                        f'SELECT COUNT(*) FROM "{escaped}"'
                    ).fetchone()[0]
                    columns = cur.execute(
                        f'PRAGMA table_info("{escaped}")'
                    ).fetchall()
                    item["tables"][table] = {
                        "rows": count,
                        "columns": [c[1] for c in columns]
                    }
                except Exception as e:
                    item["errors"].append(f"{table}: {e}")

            con.close()
        except Exception as e:
            item["status"] = "OPEN_FAILED"
            item["errors"].append(str(e))

        results.append(item)
    return results

def run_tests():
    section("PYTEST")
    cmd = [sys.executable, "-m", "pytest", "-q"]
    try:
        result = subprocess.run(
            cmd, cwd=ROOT, capture_output=True,
            text=True, timeout=900
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        print(output[-12000:])
        return {
            "command": " ".join(cmd),
            "return_code": result.returncode,
            "passed": result.returncode == 0,
            "output_tail": output[-12000:]
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "Testlar 900 soniyada tugamadi"}
    except Exception as e:
        return {"passed": False, "error": str(e)}

def main():
    section("US STOCK SCREENER — PROJECT STATUS AUDIT")
    print("Vaqt:", datetime.now().isoformat(timespec="seconds"))
    print("Loyiha:", ROOT)
    print("Python:", sys.version.split()[0])

    files = project_files()
    print("Topilgan fayllar:", len(files))

    plan_results = scan_plans(files)
    for plan, matches in plan_results.items():
        section(plan)
        if matches:
            print("DALIL TOPILDI — bu modul ishlashini hali isbotlamaydi.")
            for f in matches:
                print(" -", f)
        else:
            print("Fayl yoki matnli belgi topilmadi.")

    section("SQLITE DATABASE AUDIT — READ ONLY")
    db_results = audit_databases(files)
    if not db_results:
        print("Loyiha ichida SQLite bazasi topilmadi.")
    for db in db_results:
        print(json.dumps(db, ensure_ascii=False, indent=2))

    test_result = run_tests()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(ROOT),
        "python_version": sys.version,
        "file_count": len(files),
        "plan_file_matches": plan_results,
        "databases": db_results,
        "pytest": test_result,
        "limitations": [
            "Fayl topilishi modul to'liq ishlashini isbotlamaydi.",
            "Testlar o'tishi backtest to'g'riligini o'zi tasdiqlamaydi.",
            "API, yangiliklar oqimi va 24/7 xizmat alohida tekshirilishi kerak.",
            "Ushbu skript loyiha fayllarini o'zgartirish uchun yozilmagan."
        ]
    }

    out = REPORT_DIR / "project_status_audit.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    section("YAKUN")
    print("Hisobot:", out)
    print("Testlar o'tdi:", test_result.get("passed", False))
    print("Eslatma: fayl topilishi = ishlash tasdig'i emas.")

if __name__ == "__main__":
    main()
