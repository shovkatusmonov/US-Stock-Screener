"""
app/telegram/formatters.py - Telegram Markdown Table and Message Formatters.

Handles Telegram message chunking (4096 char limit) and monospace table layouts.
"""

from typing import Any, Dict, List, Optional


def format_screener_results_table(
    candidates: List[Dict[str, Any]],
    preset_name: str = "Scan Results",
    max_rows: int = 25
) -> List[str]:
    """
    Formats screener candidates into monospace markdown tables.
    Returns list of message strings (split if exceeding Telegram 4096 char limit).
    """
    if not candidates:
        return [f"🔍 *{preset_name.upper()}*\n\nHech qanday instrument topilmadi (0 matches)."]

    total = len(candidates)
    display_candidates = candidates[:max_rows]

    header_lines = [
        f"🎯 *AQSH SKRINER — {preset_name.upper()}*",
        f"📊 Jami topildi: *{total} ta* (Ko'rsatilmoqda: {len(display_candidates)})\n",
        "```"
    ]

    col_fmt = "{:<6} {:>8} {:>5} {:>6} {:>7} {:<8}"
    header_lines.append(col_fmt.format("Ticker", "Price", "RSI", "R.Vol", "52w%", "Regime"))
    header_lines.append("-" * 44)

    rows = []
    for c in display_candidates:
        sym = str(c.get("symbol", ""))[:6]
        price_val = c.get("close")
        price_str = f"${price_val:.2f}" if price_val is not None else "N/A"
        rsi_val = c.get("rsi14") or c.get("rsi")
        rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "-"
        rvol_val = c.get("rel_vol")
        rvol_str = f"{rvol_val:.1f}x" if rvol_val is not None else "-"
        d52_val = c.get("dist_52w_high")
        d52_str = f"{d52_val * 100:+.1f}%" if d52_val is not None else "-"
        regime_value = str(c.get("trend_regime", "NEUTRAL"))
        regime = "NO DATA" if regime_value == "INSUFFICIENT_DATA" else regime_value[:8]

        rows.append(col_fmt.format(sym, price_str, rsi_str, rvol_str, d52_str, regime))

    messages = []
    current_msg = "\n".join(header_lines) + "\n"

    for r in rows:
        line = r + "\n"
        if len(current_msg) + len(line) + 10 > 3800:
            current_msg += "```"
            messages.append(current_msg)
            current_msg = "```\n" + col_fmt.format("Ticker", "Price", "RSI", "R.Vol", "52w%", "Regime") + "\n" + ("-" * 44) + "\n"
        current_msg += line

    current_msg += "```"
    messages.append(current_msg)

    return messages


def format_quote_card(data: Dict[str, Any]) -> str:
    """Formats single security quote card."""
    sym = data.get("symbol", "UNKNOWN")
    ex = data.get("exchange", "US")
    name = data.get("name") or sym
    close = data.get("close", 0.0)
    rsi14 = data.get("rsi14")
    sma50 = data.get("sma50")
    sma200 = data.get("sma200")
    rvol = data.get("rel_vol")
    regime = data.get("trend_regime", "NEUTRAL")
    d52_h = data.get("dist_52w_high")
    d52_l = data.get("dist_52w_low")
    ret20 = data.get("ret20")

    rsi_str = f"{rsi14:.1f}" if rsi14 is not None else "N/A"
    sma50_str = f"${sma50:.2f}" if sma50 is not None else "N/A"
    sma200_str = f"${sma200:.2f}" if sma200 is not None else "N/A"
    rvol_str = f"{rvol:.2f}x" if rvol is not None else "N/A"
    d52_h_str = f"{d52_h * 100:+.2f}%" if d52_h is not None else "N/A"
    d52_l_str = f"{d52_l * 100:+.2f}%" if d52_l is not None else "N/A"
    ret20_str = f"{ret20 * 100:+.2f}%" if ret20 is not None else "N/A"

    return f"""📌 *{sym}* ({ex}) — {name}
💵 *Narx:* `${close:.2f}` | *Rejim:* `{regime}`

```
Indikatorlar:
• RSI (14):       {rsi_str}
• Relative Vol:   {rvol_str}
• 20-Kunlik Ret:  {ret20_str}
• SMA 50:         {sma50_str}
• SMA 200:        {sma200_str}
• 52-Week High:   {d52_h_str}
• 52-Week Low:    {d52_l_str}
```
_Sana: {data.get('date', 'N/A')}_"""


def format_market_breadth_card(breadth: Optional[Dict[str, Any]], benchmarks: Dict[str, Dict[str, Any]]) -> str:
    """Formats market breadth and index benchmark summary."""
    if not breadth:
        return "⚠️ Bozor kengligi ma'lumotlari hali hisoblanmagan."

    adv = breadth.get("advancing", 0)
    dec = breadth.get("declining", 0)
    ratio = breadth.get("adv_dec_ratio", 0.0)
    above50 = breadth.get("above_sma50_pct", 0.0)
    above200 = breadth.get("above_sma200_pct", 0.0)
    nh = breadth.get("new_52w_highs", 0)
    nl = breadth.get("new_52w_lows", 0)

    bench_lines = []
    for b in ["SPY", "QQQ", "IWM", "DIA"]:
        b_info = benchmarks.get(b, {})
        b_close = b_info.get("close")
        b_rsi = b_info.get("rsi14")
        b_reg = b_info.get("trend_regime", "NEUTRAL")
        b_ret = b_info.get("ret5")
        if b_close:
            ret_str = f"{b_ret * 100:+.2f}% (5d)" if b_ret else ""
            bench_lines.append(f"• *{b}*: `${b_close:.2f}` | RSI `{b_rsi:.1f}` | `{b_reg}` {ret_str}")

    bench_str = "\n".join(bench_lines) if bench_lines else "Etalonlar yuklanmagan."

    return f"""🌍 *AQSH BOZOR KENGLIGI VA ETALONLAR*
_Sana: {breadth.get('date', 'N/A')}_

📈 *Bozor Kengligi (Market Breadth):*
```
• Oshganlar / Tushganlar: {adv} / {dec} (Ratio: {ratio:.2f})
• > 50 SMA aksiyalar:     {above50:.1f}%
• > 200 SMA aksiyalar:    {above200:.1f}%
• Yangi 52w Yuqori/Quyi:  {nh} / {nl}
```

🏆 *Asosiy Indekslar:*
{bench_str}"""


def format_health_card(health: Dict[str, Any]) -> str:
    """Formats VPS and system health status."""
    overall = health.get("overall_status", "UNKNOWN")
    db = health.get("database", {})
    freshness = health.get("data_freshness", {})
    res = health.get("resources", {})

    status_icon = "🟢" if overall == "HEALTHY" else ("🟡" if overall == "WARNING" else "🔴")

    tbl_counts = db.get("table_counts", {})
    mdd_count = tbl_counts.get("market_data_daily", 0)
    inst_count = tbl_counts.get("instruments", 0) or tbl_counts.get("universe", 0)
    snap_count = tbl_counts.get("technical_snapshots", 0)

    return f"""{status_icon} *TIZIM VA VPS HOLATI* (`{overall}`)

💾 *Xotira va Resurslar (952 MiB VPS):*
• RAM ishlatilmoqda: `{res.get('process_ram_mb', 0)} MB` / `{res.get('ram_limit_mb', 700)} MB` limit
• Disk bo'sh joy:    `{res.get('disk_free_gb', 0)} GB` / `{res.get('disk_total_gb', 0)} GB`

🗄 *Ma'lumotlar Bazasi:*
• Baza hajmi:        `{db.get('db_size_mb', 0)} MB`
• Yaxlitlik (Integrity): `{'OK' if db.get('integrity_ok') else 'XATO'}`
• Instrumentlar:     `{inst_count:,} ta`
• Narx yozuvlari:    `{mdd_count:,} ta`
• Texnik snapshots:  `{snap_count:,} ta`

⏰ *Ma'lumot Yangiligi:*
• Oxirgi savdo kuni: `{freshness.get('latest_data_date', 'N/A')}`
• Qoloqlik:          `{freshness.get('lag_days', 0)} kun`
"""
