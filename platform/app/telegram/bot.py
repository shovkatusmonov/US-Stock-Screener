"""
app/telegram/bot.py - Private Admin-Only Telegram Bot Interface.

Features:
- Security: Strict ADMIN_USER_IDS verification.
- Interactive Commands:
  /start, /help - Usage guide
  /screen <preset> - Run screener preset
  /quote <symbol> - Key metrics and indicator summary
  /market - Market breadth and benchmarks (SPY, QQQ, IWM, DIA)
  /watchlist [list | add <sym> | del <sym>] - Manage personal watchlists
  /status - VPS RAM, disk, DB integrity, record counts, data freshness
"""

import functools
import logging
from pathlib import Path
import sqlite3
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)

try:
    from config.settings import TELEGRAM_BOT_TOKEN, DB_PATH, is_admin
except ImportError:
    TELEGRAM_BOT_TOKEN = ""
    DB_PATH = Path("data/screener.db")
    def is_admin(uid: int) -> bool:
        return False

from app.scanners.engine import ScreenerEngine
from app.scanners.presets import PRESETS
from app.analytics.breadth import MarketBreadthAnalyzer
from app.monitoring.health import SystemHealthMonitor
from app.telegram.formatters import (
    format_screener_results_table,
    format_quote_card,
    format_market_breadth_card,
    format_health_card
)

logger = logging.getLogger(__name__)


def admin_only(func):
    """Decorator to restrict access to authorized admin IDs only."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not is_admin(user.id):
            uid = user.id if user else "UNKNOWN"
            logger.warning(f"Unauthorized access attempt by user ID {uid}")
            if update.message:
                await update.message.reply_text(
                    f"⛔ *Ruxsat etilmagan.* Sizning Telegram ID: `{uid}`.\nAdministrator bilan bog'laning.",
                    parse_mode="Markdown"
                )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and available commands."""
    help_text = """🚀 *METRIQA US STOCK SCREENER BOT*

Quyidagi buyruqlar orqali AQSh bozorini tahlil qilishingiz mumkin:

🔍 *Skriner Buyruqlari:*
• `/screen <preset>` — Skriner filtri ishga tushirish
  _Mavjud presetlar:_
  `bullish_breakout`, `pullback_support`, `oversold_bounce`, `volume_surge`, `golden_cross`, `52w_high_breakout`, `bearish_breakdown`, `overbought_warning`

📌 *Instrument & Bozor:*
• `/quote <ticker>` — Aksiya narxi, RSI, SMA50/200, hajm va 52w diapazon (masalan: `/quote NVDA`)
• `/market` — Bozor kengligi (Market Breadth) va SPY, QQQ, IWM, DIA ko'rsatkichlari

⭐ *Kuzatuv Ro'yxati (Watchlist):*
• `/wl list` — Saqlangan aksiyalar ro'yxati
• `/wl add <ticker>` — Aksiya qo'shish (masalan: `/wl add AAPL`)
• `/wl del <ticker>` — Ro'yxatdan o'chirish

📊 *Tizim Monitoring:*
• `/status` — VPS RAM ishlatilishi, Disk, Baza va ma'lumotlar holati
• `/help` — Ushbu yordam xabari
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


@admin_only
async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute a screening preset."""
    if not context.args:
        available = ", ".join([f"`{k}`" for k in PRESETS.keys()])
        await update.message.reply_text(
            f"⚠️ Skriner nomini kiriting!\nMisol: `/screen bullish_breakout`\n\nMavjud presetlar:\n{available}",
            parse_mode="Markdown"
        )
        return

    preset_name = context.args[0].lower().strip()
    if preset_name not in PRESETS:
        available = ", ".join([f"`{k}`" for k in PRESETS.keys()])
        await update.message.reply_text(
            f"❌ Noma'lum preset: `{preset_name}`\nMavjud presetlar:\n{available}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"⏳ Skriner ishga tushirildi: *{preset_name}*...", parse_mode="Markdown")

    engine = ScreenerEngine()
    candidates = engine.screen(preset_name, limit=30)
    messages = format_screener_results_table(candidates, preset_name=preset_name)

    for msg in messages:
        await update.message.reply_text(msg, parse_mode="Markdown")


@admin_only
async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retrieve quote and technical snapshot for a symbol."""
    if not context.args:
        await update.message.reply_text("⚠️ Ticker kiriting!\nMisol: `/quote AAPL`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper().strip()
    path = Path(DB_PATH)
    if not path.exists():
        await update.message.reply_text("❌ Ma'lumotlar bazasi topilmadi.", parse_mode="Markdown")
        return

    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {r[0] for r in cur.fetchall()}

    row = None
    if "technical_snapshots" in tables:
        join_inst = "LEFT JOIN instruments i ON s.symbol = i.symbol" if "instruments" in tables else ""
        cur.execute(f"""
            SELECT s.* {', i.name, i.asset_class, i.exchange' if 'instruments' in tables else ''}
            FROM technical_snapshots s
            {join_inst}
            WHERE s.symbol = ?
            ORDER BY s.date DESC LIMIT 1
        """, (symbol,))
        row = cur.fetchone()

    if not row and "market_data_daily" in tables:
        cur.execute("""
            SELECT date, close, volume, open, high, low, adj_close
            FROM market_data_daily
            WHERE symbol = ?
            ORDER BY date DESC LIMIT 1
        """, (symbol,))
        row = cur.fetchone()

    conn.close()

    if not row:
        await update.message.reply_text(f"❌ Ticker `{symbol}` bo'yicha ma'lumot topilmadi.", parse_mode="Markdown")
        return

    card = format_quote_card(dict(row))
    await update.message.reply_text(card, parse_mode="Markdown")


@admin_only
async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Market breadth and benchmark review."""
    analyzer = MarketBreadthAnalyzer()
    breadth = analyzer.get_latest_breadth()
    benchmarks = analyzer.get_benchmark_summary()
    card = format_market_breadth_card(breadth, benchmarks)
    await update.message.reply_text(card, parse_mode="Markdown")


@admin_only
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage watchlist items."""
    user_id = update.effective_user.id
    args = context.args or ["list"]
    action = args[0].lower()

    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'US',
            note TEXT,
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, symbol, exchange)
        );
    """)

    if action == "list":
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, exchange, added_at, note
            FROM watchlists
            WHERE user_id = ?
            ORDER BY symbol ASC
        """, (user_id,))
        items = cur.fetchall()
        conn.close()

        if not items:
            await update.message.reply_text(
                "⭐ Watchlist bo'sh.\nAksiya qo'shish uchun: `/wl add AAPL`",
                parse_mode="Markdown"
            )
            return

        lines = ["⭐ *SIZNING WATCHLISTINGIZ:*\n"]
        for it in items:
            lines.append(f"• `{it['symbol']}` ({it['exchange']}) — _qo'shildi: {it['added_at'][:10]}_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif action == "add":
        if len(args) < 2:
            conn.close()
            await update.message.reply_text("⚠️ Qo'shish uchun ticker kiriting: `/wl add NVDA`", parse_mode="Markdown")
            return
        sym = args[1].upper().strip()
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO watchlists (user_id, symbol, exchange, added_at)
            VALUES (?, ?, 'US', ?)
        """, (user_id, sym, now_str))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ `{sym}` watchlistga muvaffaqiyatli qo'shildi.", parse_mode="Markdown")

    elif action in ("del", "remove", "rm"):
        if len(args) < 2:
            conn.close()
            await update.message.reply_text("⚠️ O'chirish uchun ticker kiriting: `/wl del NVDA`", parse_mode="Markdown")
            return
        sym = args[1].upper().strip()
        conn.execute("DELETE FROM watchlists WHERE user_id = ? AND symbol = ?", (user_id, sym))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🗑 `{sym}` watchlistdan o'chirildi.", parse_mode="Markdown")

    else:
        conn.close()
        await update.message.reply_text("Noto'g'ri buyruq. Ishlatish: `/wl [list | add <ticker> | del <ticker>]`", parse_mode="Markdown")


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspect system, VPS resources, database, and data freshness."""
    monitor = SystemHealthMonitor()
    health = monitor.get_full_health_report()
    card = format_health_card(health)
    await update.message.reply_text(card, parse_mode="Markdown")


def create_bot_application() -> Optional[Application]:
    """Constructs Telegram Application with configured handlers."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN is not configured in .env.")
        return None

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler(["screen", "screener"], cmd_screen))
    app.add_handler(CommandHandler(["quote", "q"], cmd_quote))
    app.add_handler(CommandHandler(["market", "breadth"], cmd_market))
    app.add_handler(CommandHandler(["watchlist", "wl"], cmd_watchlist))
    app.add_handler(CommandHandler(["status", "health"], cmd_status))

    return app


def run_bot():
    """Runs the Telegram bot blocking loop."""
    app = create_bot_application()
    if not app:
        print("[XATO] Telegram bot token topilmadi. .env fayliga TELEGRAM_BOT_TOKEN ni kiriting.")
        return

    print("🤖 Telegram bot ishga tushmoqda...")
    app.run_polling()
