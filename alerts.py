"""
alerts.py - Deduplikatsiya qilingan ogohlantirishlar tizimi va Telegram transporti.
Yetkazish holatlari: SENT, LOGGED_ONLY, FAILED.
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Tuple


class AlertManager:
    def __init__(self, db_path: str = "data/screener.db", log_file: str = "reports/alerts.log"):
        self.db_path = db_path
        self.log_file = log_file
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sent_alerts (
                    alert_id TEXT PRIMARY KEY,
                    ticker TEXT,
                    alert_type TEXT,
                    severity TEXT,
                    delivery_status TEXT,
                    message TEXT,
                    created_at TEXT
                )
            """)

    def send_alert(
        self,
        alert_id: str,
        ticker: str,
        alert_type: str,
        severity: str,
        message: str
    ) -> Tuple[bool, str]:
        now_ts = datetime.now().isoformat()
        with self._get_conn() as conn:
            existing = conn.execute("SELECT alert_id FROM sent_alerts WHERE alert_id = ?", (alert_id,)).fetchone()
            if existing:
                return False, f"DUPLICATE_SUPPRESSED: {alert_id} avval yuborilgan"

        # Faylga yozish
        log_path = Path(self.log_file)
        log_path.parent.mkdir(exist_ok=True)
        log_entry = f"[{now_ts}] [{severity}] [{alert_type}] [{ticker}] {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)

        # Telegram holatini tekshirish
        tg_status = self._send_telegram(f"[{severity}] {ticker}: {message}")
        if tg_status is True:
            final_status = "SENT"
        elif tg_status is False:
            final_status = "FAILED"
        else:
            final_status = "LOGGED_ONLY"  # Telegram sozlanmagan

        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sent_alerts (alert_id, ticker, alert_type, severity, delivery_status, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (alert_id, ticker, alert_type, severity, final_status, message, now_ts))

        return True, final_status

    def _send_telegram(self, text: str):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return None  # Konfiguratsiya qilinmagan

        try:
            import urllib.request
            import urllib.parse
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False