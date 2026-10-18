from pathlib import Path
from datetime import datetime
import sqlite3
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "project_status_audit.txt"
SKIP = {".venv", "venv", "__pycache__", ".git", "node_modules"}

PLANS = {
    "Project": ["run_daily.py", "main.py", "config.py", "requirements.txt"],
    "Technical analysis": ["technical", "indicator", "sma", "rsi", "atr"],
    "Strategies and backtest": ["strategy", "breakout", "pullback", "backtest"],
    "Risk management": ["risk", "stop_loss", "position_size", "drawdown"],
    "Fundamentals and SEC": ["fundamental", "sec", "edgar", "earnings", "eps"],
    "Macro": ["macro", "fred", "cpi", "fomc", "treasury", "regime"],
    "News and AI": ["news", "catalyst", "sentiment", "social", "gemini", "openai"],
    "Scoring and screener": ["score", "screener", "ranking", "signal"],
    "Paper trading": ["paper", "virtual", "trade_journal", "lifecycle"],
    "24/7 monitoring": ["scheduler", "monitor", "retry", "backup", "healthcheck"],
    "Historical data and PIT": ["point_in_time", "survivorship", "delisted", "filing_date"],
    "Reports and alerts": ["report", "telegram", "alert", "notification"],
}

lines = []
def log(s=""):
    print(s)
    lines.append(str(s))

log("US STOCK SCREENER — STATUS AUDIT")
log(f"Time: {datetime.now().isoformat(timespec='seconds')}")
log(f"Root: {ROOT}")
log(f"Python: {sys.version}")
log()

files = []
for p in ROOT.rglob("*"):
    if p.is_file() and not any(x.lower() in SKIP for x in p.parts):
        files.append(p)

log(f"Total files found: {len(files)}")

for plan, keywords in PLANS.items():
    log()
    log("=" * 60)
    log(plan)
    matches = []
    for p in files:
        if any(k in p.name.lower() for k in keywords):
            matches.append(p)
            continue
        if p.suffix.lower() in {".py", ".md", ".txt", ".json", ".toml", ".yml", ".yaml"}:
            try:
                if p.stat().st_size < 1_000_000:
                    text = p.read_text(encoding="utf-8", errors="ignore").lower()
                    if any(k in text for k in keywords):
                        matches.append(p)
            except Exception:
                pass

    if matches:
        for p in sorted(set(matches))[:50]:
            log("  FOUND: " + str(p.relative_to(ROOT)))
    else:
        log("  NO MATCHES FOUND")

log()
log("=" * 60)
log("DATABASE AUDIT — READ ONLY")

dbs = [p for p in files if p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}]
if not dbs:
    log("No SQLite database found.")
for db in dbs:
    log(f"\nDatabase: {db.relative_to(ROOT)} | Size: {db.stat().st_size:,} bytes")
    try:
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        cur = con.cursor()
        tables = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            safe = table.replace('"', '""')
            count = cur.execute(f'SELECT COUNT(*) FROM "{safe}"').fetchone()[0]
            log(f"  {table}: {count:,} rows")
        con.close()
    except Exception as e:
        log(f"  DATABASE ERROR: {e}")

log()
log("=" * 60)
log("PYTEST")
try:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT, capture_output=True, text=True, timeout=900
    )
    log(f"Exit code: {r.returncode}")
    log(r.stdout[-8000:])
    if r.stderr:
        log(r.stderr[-3000:])
except Exception as e:
    log(f"Could not run pytest: {e}")

OUT.write_text("\n".join(lines), encoding="utf-8")
log()
log(f"REPORT SAVED: {OUT}")
