"""
reporting.py - Professional darajadagi kunlik savdo hisoboti generatori.
Skoringlar evristik (Heuristic) ko'rsatkich sifatida aniq belgilangan.
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from risk_manager import reward_risk


def calculate_candidate_scores(candidate: dict) -> dict:
    """Nomzod uchun evristik texnik ko'rsatkichlarni hisoblaydi."""
    rank = candidate.get("rank")
    atr = candidate.get("atr")
    close = candidate.get("close") or candidate.get("signal_price")
    rel_vol = candidate.get("rel_vol", 1.0)
    # Kirish ma'lumoti saqlanmagan bo'lsa (masalan, bazadan yuklangan eski reja),
    # uydirma standart qiymatlar o'rniga None qaytariladi.
    if rank is None or atr is None or not close or close <= 0:
        return {"swing_score": None, "risk_score": None, "volatility_score": None}
    atr_pct = atr / close

    # Heuristic Momentum/Volume balli (0 - 100)
    swing_score = min(100.0, max(0.0, 50.0 + (rank * 200.0) + ((rel_vol - 1.0) * 15.0)))

    # Heuristic ATR Volatillik balli (0 - 100)
    volatility_score = min(100.0, max(10.0, atr_pct * 1000.0))

    return {
        "swing_score": round(swing_score, 1),
        "risk_score": round(volatility_score, 1),
        "volatility_score": round(volatility_score, 1),
    }


def generate_full_report(
    report_date: str,
    data_date: str,
    bench_info: dict,
    candidates: List[dict],
    open_positions_summary: dict,
    total_evaluated: int,
    data_source_status: str = "ONLINE (SQLite / yfinance)",
    output_dir: str = "reports",
    events: Optional[Dict[str, list]] = None
) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    is_bull = bench_info.get("is_bull", False)
    spy_close = bench_info.get("spy_close", 0.0)
    spy_sma200 = bench_info.get("spy_sma200", 0.0)

    lines = [
        "=" * 85,
        "AQSH FOND BOZORI KUNLIK TEXNIK TAHLIL VA SAVDO REJASI HISOBOTI",
        "=" * 85,
        f"Hisobot vaqti:            {now_str}",
        f"Kotirovkalar sanasi:       {data_date}",
        f"Ma'lumot manbasi holati:   {data_source_status}",
        f"SPY Ko'rsatkichi:          ${spy_close:.2f} (200 SMA: ${spy_sma200:.2f})",
        f"Bozor Rejimi:              {'BULLISH (Trend ustida - Savdoga ruxsat)' if is_bull else 'BEARISH (Qat\\\'iy kassa rejimi)'}",
        f"Tekshirilgan aksiyalar:    {total_evaluated} ta",
        f"Ruxsat etilgan rejalar:    {len(candidates)} ta",
        f"Portfel ochiq xavfi:       {open_positions_summary.get('portfolio_heat_pct', 0.0)*100:.1f}%",
        f"Ochiq pozitsiyalar soni:   {open_positions_summary.get('open_count', 0)} ta",
        "-" * 85,
    ]

    if events and any(events.get(k) for k in ("filled", "cancelled", "closed")):
        lines.append("PORTFEL HODISALARI (oxirgi yangilanish):")
        for e in events.get("filled", []):
            lines.append(f"    - OCHILDI:   {e['ticker']} {e['date']} ochilish ${e['open']:.2f}")
        for e in events.get("closed", []):
            lines.append(f"    - YOPILDI:   {e['ticker']} {e['date']} {e['reason']} ${e['exit_price']:.2f} (PnL ${e['pnl']:.2f})")
        for e in events.get("cancelled", []):
            lines.append(f"    - BEKOR:     {e['ticker']} {e['reason']}")
        lines.append("-" * 85)

    if not is_bull:
        lines.append("[XULOSA] Bozor tushuvchi rejimda bo'lgani sababli barcha yangi signallar bloklandi.")
    elif not candidates:
        lines.append("SAVDO UCHUN MOS NOMZOD TOPILMADI")
        lines.append("Izoh: Barcha ko'rilgan instrumentlar texnik va risk talablariga to'liq javob bermadi.")
    else:
        lines.append("TASDIQLANGAN SAVDO NOMZODLARI VA OPERATSION REJA:")
        lines.append("")
        for idx, c in enumerate(candidates, 1):
            scores = calculate_candidate_scores(c)
            close_p = c["signal_price"]
            min_o = c.get("min_open", c.get("min_open_allowed", close_p * 0.97))
            max_o = c.get("max_open", c.get("max_open_allowed", close_p * 1.03))
            stop_p = c["planned_stop"]
            tp2 = c["planned_target"]
            rr = c.get("rr")
            if rr is None:
                rr = reward_risk(close_p, stop_p, tp2)
            rr_str = f"1 : {rr:.2f}" if rr is not None else "N/A"
            sc_swing = "N/A" if scores["swing_score"] is None else f"{scores['swing_score']}/100"
            sc_vol = "N/A" if scores["volatility_score"] is None else f"{scores['volatility_score']}/100"
            conf = c.get("confidence", "AUDITED_PRICE_DATA")

            counter_arguments = []
            if c.get("rel_vol", 1.0) > 4.0:
                counter_arguments.append("Hajm haddan tashqari yuqori (klimaks xavfi)")
            if scores["volatility_score"] is not None and scores["volatility_score"] > 60.0:
                counter_arguments.append("Yuqori ATR tebranishi")
            if counter_arguments:
                counter_str = "; ".join(counter_arguments)
            elif scores["volatility_score"] is None:
                counter_str = "Baholanmadi (signal ma'lumotlari saqlanmagan)"
            else:
                counter_str = "Texnik to'siqlar aniqlanmadi"

            lines.append(f"[{idx}] TICKER: {c['ticker']} | Strategiya: Breakout V2 Trend Runner")
            lines.append(f"    - Heuristic Ballar:  Momentum: {sc_swing} | Volatillik: {sc_vol}")
            lines.append(f"    - Ma'lumot sifati:   {conf}")
            lines.append(f"    - Yopilish narxi:    ${close_p:.2f}")
            lines.append(f"    - Ruxsat etilgan kirish: Open ${min_o:.2f}..${max_o:.2f} oralig'ida bo'lishi shart (±3%)")
            lines.append(f"    - Stop-Loss:         ${stop_p:.2f} (-2 x ATR)")
            lines.append(f"    - Target:            TP2: ${tp2:.2f} (+35.0%)")
            lines.append(f"    - Risk/Reward:       {rr_str}")
            lines.append(f"    - Tavsiya hajmi:     {c['shares']} dona aksiya (Qiymat: ${c['shares']*close_p:.2f})")
            lines.append(f"    - Pul riski:         ${c['dollar_risk']:.2f} (Risk ulushi: {c['risk_pct']*100:.1f}%)")
            lines.append(f"    - Qarshi dalillar:   {counter_str}")
            lines.append(f"    - Bekor qilish:      Agar ertaga ochilish narxi belgilangan chegaradan chiqsa (Gap > 3%), savdo bekor qilinadi.")
            lines.append("-" * 85)

    report_text = "\n".join(lines)
    out_path = Path(output_dir)
    out_path.mkdir(exist_ok=True)
    file_name = out_path / f"daily_report_{data_date.replace('-', '')}.txt"
    file_name.write_text(report_text, encoding="utf-8")
    return report_text