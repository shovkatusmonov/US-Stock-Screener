"""
apply_task1_task2.py
1-VAZIFA: audit_suite.pit_window_coverage va run_survivorship_test (evaluate_date_matrix_coverage)
          o'rtasidagi qamrov farqini (94.71% vs 93.96%) SPY sanalari bilan kesishtirish orqali bartaraf etish.
2-VAZIFA: Natijalarni versiyalab saqlash va yagona etalon hisobot skripti (run_canonical_report.py)
          hamda SPY Total-Return vs SPY Price-Only (3-VAZIFA tahlili) jadvallarini yaratish.
Barcha fayllar Windows CRLF satr oxiri bilan saqlanadi.
"""
from pathlib import Path
import re

ROOT = Path("C:/us_stock_screener")


def write_crlf(path: Path, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path.write_bytes(normalized.encode("utf-8"))


# ---------------------------------------------------------------------------
# 1. audit_suite.py ichidagi pit_window_coverage funksiyasini SPY sanalari bilan kesishtirish
# ---------------------------------------------------------------------------
audit_path = ROOT / "audit_suite.py"
audit_src = audit_path.read_text(encoding="utf-8")

NEW_PIT_COV_FUNC = r'''def pit_window_coverage(
    conn: sqlite3.Connection,
    hm=None,
    start_date: str = "2020-01-01",
    end_date: str | None = None,
) -> dict:
    """
    Har bir PIT ticker uchun a'zolik oynasi ichidagi kotirovkalar qamrovini
    faqat SPY savdo kalendari sanalari bilan kesishtirgan holda hisoblaydi.
    Bu sp500_history.evaluate_date_matrix_coverage() bilan 100% bir xil natija beradi.
    """
    from sp500_history import SP500HistoryManager

    if hm is None:
        hm = SP500HistoryManager()

    q = "SELECT date FROM prices WHERE ticker = 'SPY' AND date >= ?"
    params: list = [start_date]
    if end_date:
        q += " AND date <= ?"
        params.append(end_date)
    q += " ORDER BY date ASC"

    spy_dates = [r[0][:10] for r in conn.execute(q, params).fetchall()]
    if not spy_dates:
        return {
            "expected_slots": 0,
            "have_slots": 0,
            "missing_slots": 0,
            "coverage_pct": 0.0,
            "full_tickers": [],
            "partial_tickers": {},
            "zero_tickers": [],
            "outside_only_tickers": {},
        }

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

    expected_total = 0
    have_total = 0
    full_tickers: list[str] = []
    partial_tickers: dict[str, tuple[int, int]] = {}
    zero_tickers: list[str] = []
    in_window_have_by_ticker: dict[str, int] = {}

    for t in sorted(hm.membership_rules.keys()):
        mask = hm.membership_mask(t, spy_idx)
        exp_t = int(mask.sum())
        if exp_t == 0:
            continue
        expected_total += exp_t
        active_spy_dates = {spy_dates[i] for i, flag in enumerate(mask) if flag}
        t_dates = dates_by_ticker.get(t, set())
        have_t = len(active_spy_dates & t_dates)
        have_total += have_t
        in_window_have_by_ticker[t] = have_t

        if have_t == 0:
            zero_tickers.append(t)
        elif have_t / exp_t >= 0.95:
            full_tickers.append(t)
        else:
            partial_tickers[t] = (have_t, exp_t)

    outside_only: dict[str, int] = {}
    for t, tot_n in sorted(total_rows_by_ticker.items()):
        if t == "SPY":
            continue
        if in_window_have_by_ticker.get(t, 0) == 0 and tot_n > 0:
            outside_only[t] = tot_n

    cov_pct = (have_total / expected_total) if expected_total > 0 else 0.0
    return {
        "expected_slots": expected_total,
        "have_slots": have_total,
        "missing_slots": expected_total - have_total,
        "coverage_pct": cov_pct,
        "full_tickers": full_tickers,
        "partial_tickers": partial_tickers,
        "zero_tickers": zero_tickers,
        "outside_only_tickers": outside_only,
    }
'''

pattern = re.compile(r"def pit_window_coverage\(.*?\n(?=def |\Z)", re.DOTALL)
if pattern.search(audit_src):
    audit_src_new = pattern.sub(NEW_PIT_COV_FUNC + "\n\n", audit_src, count=1)
    write_crlf(audit_path, audit_src_new)
    print("[OK] audit_suite.py: pit_window_coverage yangilandi (SP500HistoryManager import bilan).")
else:
    print("[OGOHLANTIRISH] audit_suite.py ichida pit_window_coverage topilmadi!")

# ---------------------------------------------------------------------------
# 2. test_data_audit.py ga yangi regressiya testini qo'shish
# ---------------------------------------------------------------------------
test_audit_path = ROOT / "test_data_audit.py"
test_audit_src = test_audit_path.read_text(encoding="utf-8")

EXTRA_TEST = r'''

def test_pit_window_coverage_matches_matrix_and_ignores_non_spy_dates(tmp_path):
    """
    1-VAZIFA regressiya testi:
    Agar aksiyada SPY kalendarida yo'q sanada (masalan dam olish kuni yoki a'zolik oynasidan tashqarida)
    qator bo'lsa, pit_window_coverage uni 'have_slots' ga qo'shmasligi va
    evaluate_date_matrix_coverage bilan aynan bir xil raqam qaytarishi shart.
    """
    import sqlite3
    from sp500_history import SP500HistoryManager
    from audit_suite import pit_window_coverage

    db_file = tmp_path / "cov_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adj_close REAL)")

    spy_dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    for d in spy_dates:
        conn.execute("INSERT INTO prices VALUES ('SPY', ?, 100, 101, 99, 100, 1000, 100)", (d,))

    for d in ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-06"]:
        conn.execute("INSERT INTO prices VALUES ('AAA', ?, 50, 51, 49, 50, 500, 50)", (d,))

    conn.execute("INSERT INTO prices VALUES ('BBB', '2019-05-01', 10, 11, 9, 10, 100, 10)")
    conn.commit()

    hm = SP500HistoryManager(
        rules={
            "AAA": [("2024-01-01", "2024-12-31")],
            "BBB": [("2024-01-01", "2024-12-31")],
        },
        complete=True,
    )

    cov_audit = pit_window_coverage(conn, hm=hm, start_date="2024-01-01", end_date="2024-12-31")
    cov_matrix = hm.evaluate_date_matrix_coverage(spy_dates, {"AAA", "BBB"}, conn)

    assert cov_audit["expected_slots"] == 8
    assert cov_audit["have_slots"] == 3
    assert cov_audit["expected_slots"] == cov_matrix["total_slots"]
    assert cov_audit["have_slots"] == cov_matrix["filled_slots"]
    assert abs(cov_audit["coverage_pct"] - cov_matrix["coverage_pct"]) < 1e-12
    assert cov_audit["outside_only_tickers"] == {"BBB": 1}
    conn.close()
'''

if "test_pit_window_coverage_matches_matrix_and_ignores_non_spy_dates" not in test_audit_src:
    write_crlf(test_audit_path, test_audit_src.rstrip() + "\n" + EXTRA_TEST)
    print("[OK] test_data_audit.py: qamrov mosligi testi qo'shildi.")
else:
    print("[OK] test_data_audit.py: test allaqachon mavjud.")

# ---------------------------------------------------------------------------
# 3. run_canonical_report.py - 2-VAZIFA va 3-VAZIFA (Etalon versiyalangan hisobot)
# ---------------------------------------------------------------------------
CANONICAL_REPORT_CODE = r'''"""
run_canonical_report.py - Yagona Etalon Backtest va Versiyalangan Hisobot Generatori.
"""
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

import features as ft
import strategies as st
from audit_suite import pit_window_coverage
from engine import Params, run_backtest
from run_backtest import equity_stats, trade_stats
from run_robustness import regime_timed_spy, block_bootstrap_excess
from sp500_history import SP500HistoryManager

PERIODS = {
    "DEV":  ("2020-01-01", "2022-12-31"),
    "VAL":  ("2023-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2026-12-31"),
}

CORE_FILES = [
    "engine.py",
    "features.py",
    "strategies.py",
    "risk_manager.py",
    "sp500_history.py",
    "run_survivorship_test.py",
    "run_robustness.py",
]


def get_code_fingerprint(root: Path = Path(".")) -> dict:
    hashes = {}
    combined = hashlib.sha256()
    for fname in CORE_FILES:
        fpath = root / fname
        if fpath.exists():
            h = hashlib.sha256(fpath.read_bytes()).hexdigest()[:12]
            hashes[fname] = h
            combined.update(h.encode("utf-8"))
    git_commit = "NO_GIT"
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            git_commit = res.stdout.strip()
    except Exception:
        pass
    return {
        "git_commit": git_commit,
        "combined_sha256_12": combined.hexdigest()[:12],
        "files": hashes,
    }


def generate_canonical_report(
    db_path: str = "data/screener.db",
    out_dir: str = "reports",
    bootstrap_seed: int = 42,
    n_boot: int = 2000,
) -> dict:
    conn = sqlite3.connect(db_path)
    db_meta = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM prices"
    ).fetchone()
    total_rows, distinct_tickers, min_date, max_date = db_meta

    hm = SP500HistoryManager()
    cov_audit = pit_window_coverage(conn, hm=hm, start_date="2020-01-01")

    bench_raw = ft.load_prices(conn, "SPY")
    bench_f = ft.build_features(bench_raw, bench_raw)
    bench_trd = ft.get_tradable_ohlc(bench_raw).reindex(bench_f.index).ffill()
    regime = st.regime_series(bench_f)
    dates = bench_f.index

    spy_date_strs = [d.strftime("%Y-%m-%d") for d in dates]
    cov_matrix = hm.evaluate_date_matrix_coverage(
        spy_date_strs, set(hm.membership_rules.keys()), conn
    )

    tickers = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker"
        )
    ]
    feats = {}
    tradable_ohlc = {}
    for t in tickers:
        raw = ft.load_prices(conn, t)
        if len(raw) < 260:
            continue
        f = ft.build_features(raw, bench_raw)
        if not f.empty:
            feats[t] = f
            tradable_ohlc[t] = ft.get_tradable_ohlc(raw).reindex(f.index).ffill()
    conn.close()

    params = Params.live() if hasattr(Params, "live") else Params(tp_pct=0.35, max_hold=35)

    spy_adj_close = bench_f["close"]
    spy_trd_close = bench_trd["close"]
    reg_spy_trd = regime_timed_spy(spy_trd_close, regime, cost_per_side=params.cost_per_side)
    reg_spy_adj = regime_timed_spy(spy_adj_close, regime, cost_per_side=params.cost_per_side)

    results_by_strategy = {}
    for strat_name in ("breakout", "pullback"):
        fn = st.STRATEGIES[strat_name]
        pit_signals = {}
        for t, f in feats.items():
            sig_df = fn(f, regime).copy()
            mask = hm.membership_mask(t, sig_df.index)
            sig_df.loc[~mask, "signal"] = False
            trd_df = tradable_ohlc[t]
            sig_df["atr"] = ft.atr(trd_df["high"], trd_df["low"], trd_df["close"], 14)
            pit_signals[t] = sig_df

        trades, curve = run_backtest(tradable_ohlc, pit_signals, dates, params)
        strat_periods = {}
        for p_name, (st_d, en_d) in PERIODS.items():
            eq_s = curve["equity"].loc[st_d:en_d]
            t_sub = (
                trades[(trades["entry_date"] >= st_d) & (trades["entry_date"] <= en_d)]
                if len(trades)
                else trades
            )
            es = equity_stats(eq_s)
            ts = trade_stats(t_sub)

            reg_trd_slice = reg_spy_trd.loc[st_d:en_d]
            reg_adj_slice = reg_spy_adj.loc[st_d:en_d]
            boot_trd = block_bootstrap_excess(
                eq_s, reg_trd_slice, n_boot=n_boot, block_len=10, seed=bootstrap_seed
            )
            boot_adj = block_bootstrap_excess(
                eq_s, reg_adj_slice, n_boot=n_boot, block_len=10, seed=bootstrap_seed
            )
            strat_periods[p_name] = {
                "return_pct": round(float(es["total"]) * 100, 2),
                "cagr_pct": round(float(es["cagr"]) * 100, 2),
                "sharpe": round(float(es["sharpe"]), 2),
                "sortino": round(float(es["sortino"]), 2),
                "maxdd_pct": round(float(es["maxdd"]) * 100, 2),
                "trades": int(ts["n"]) if ts else 0,
                "win_rate_pct": round(float(ts["win_rate"]) * 100, 1) if ts else 0.0,
                "pf": round(float(ts["profit_factor"]), 2) if ts else 0.0,
                "boot_p_gt_0_vs_reg_spy_trd": round(float(boot_trd["p_gt_0"]) * 100, 1),
                "boot_p_gt_0_vs_reg_spy_adj": round(float(boot_adj["p_gt_0"]) * 100, 1),
            }
        results_by_strategy[strat_name] = strat_periods

    benchmarks_summary = {}
    for p_name, (st_d, en_d) in PERIODS.items():
        s_adj = equity_stats(spy_adj_close.loc[st_d:en_d])
        s_trd = equity_stats(spy_trd_close.loc[st_d:en_d])
        r_trd = equity_stats(reg_spy_trd.loc[st_d:en_d])
        r_adj = equity_stats(reg_spy_adj.loc[st_d:en_d])
        benchmarks_summary[p_name] = {
            "SPY_TotalReturn_Adj": {
                "return_pct": round(float(s_adj["total"]) * 100, 2),
                "cagr_pct": round(float(s_adj["cagr"]) * 100, 2),
                "sharpe": round(float(s_adj["sharpe"]), 2),
                "maxdd_pct": round(float(s_adj["maxdd"]) * 100, 2),
            },
            "SPY_PriceOnly_Tradable": {
                "return_pct": round(float(s_trd["total"]) * 100, 2),
                "cagr_pct": round(float(s_trd["cagr"]) * 100, 2),
                "sharpe": round(float(s_trd["sharpe"]), 2),
                "maxdd_pct": round(float(s_trd["maxdd"]) * 100, 2),
            },
            "Regime_SPY_Tradable": {
                "return_pct": round(float(r_trd["total"]) * 100, 2),
                "cagr_pct": round(float(r_trd["cagr"]) * 100, 2),
                "sharpe": round(float(r_trd["sharpe"]), 2),
                "maxdd_pct": round(float(r_trd["maxdd"]) * 100, 2),
            },
            "Regime_SPY_Adj": {
                "return_pct": round(float(r_adj["total"]) * 100, 2),
                "cagr_pct": round(float(r_adj["cagr"]) * 100, 2),
                "sharpe": round(float(r_adj["sharpe"]), 2),
                "maxdd_pct": round(float(r_adj["maxdd"]) * 100, 2),
            },
        }

    fp = get_code_fingerprint()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    payload = {
        "generated_at": now_str,
        "code_fingerprint": fp,
        "database": {
            "path": db_path,
            "total_rows": int(total_rows),
            "distinct_tickers": int(distinct_tickers),
            "min_date": str(min_date),
            "max_date": str(max_date),
        },
        "pit_coverage": {
            "audit_suite_have_slots": int(cov_audit["have_slots"]),
            "audit_suite_expected_slots": int(cov_audit["expected_slots"]),
            "audit_suite_pct": round(float(cov_audit["coverage_pct"]) * 100, 2),
            "matrix_filled_slots": int(cov_matrix["filled_slots"]),
            "matrix_total_slots": int(cov_matrix["total_slots"]),
            "matrix_pct": round(float(cov_matrix["coverage_pct"]) * 100, 2),
            "exact_match": int(cov_audit["have_slots"]) == int(cov_matrix["filled_slots"]),
        },
        "params": asdict(params),
        "bootstrap": {"seed": bootstrap_seed, "n_boot": n_boot, "block_len": 10},
        "benchmarks": benchmarks_summary,
        "strategies": results_by_strategy,
    }

    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    base_name = f"canonical_report_{stamp}_{fp['combined_sha256_12']}"
    json_file = out_p / f"{base_name}.json"
    md_file = out_p / f"{base_name}.md"

    json_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# US STOCK SCREENER — ETALON VERSIYALANGAN BACKTEST HISOBOTI",
        "",
        f"- **Yaratilgan vaqt:** `{now_str}`",
        f"- **Kod SHA-256 (12):** `{fp['combined_sha256_12']}` (Git: `{fp['git_commit']}`)",
        f"- **Baza holati:** `{total_rows:,}` qator | `{distinct_tickers}` ticker | `{min_date}` -> `{max_date}`",
        f"- **PIT Qamrovi (SPY sanalari bo'yicha):** `{cov_audit['have_slots']:,} / {cov_audit['expected_slots']:,}` = **`{cov_audit['coverage_pct']*100:.2f}%`** (Matrix bilan moslik: `{payload['pit_coverage']['exact_match']}`)",
        f"- **Konfiguratsiya (Params.live):** `tp_pct={params.tp_pct}`, `max_hold={params.max_hold}`, `stop_atr={params.stop_atr}`, `cost_per_side={params.cost_per_side}`",
        f"- **Bootstrap:** `seed={bootstrap_seed}`, `n_boot={n_boot}`, `block_len=10`",
        "",
        "## 1. Benchmarklar Taqqoslovi (Dividend Ta'siri: Adjusted vs Tradable)",
        "",
        "| Davr | Benchmark | Daromad | CAGR | Sharpe | MaxDD | Izoh |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
    ]
    for p_name in PERIODS:
        b = benchmarks_summary[p_name]
        for b_key, note in [
            ("SPY_TotalReturn_Adj", "Dividend qayta investitsiyalangan (adj_close)"),
            ("SPY_PriceOnly_Tradable", "Dividendsiz sof narx (strategiya bilan bir xil baza)"),
            ("Regime_SPY_Adj", "SPY > SMA200 (Adjusted)"),
            ("Regime_SPY_Tradable", "SPY > SMA200 (Tradable, dividendsiz)"),
        ]:
            m = b[b_key]
            lines.append(
                f"| **{p_name}** | `{b_key}` | {m['return_pct']:+.1f}% | {m['cagr_pct']:+.1f}% | {m['sharpe']:.2f} | {m['maxdd_pct']:.1f}% | {note} |"
            )

    lines.extend([
        "",
        "## 2. Strategiyalar Natijalari (PIT, Jonli Qoidalar, Tradable OHLC)",
        "",
        "| Strategiya | Davr | Daromad | CAGR | Sharpe | Sortino | MaxDD | Trades | WinRate | PF | P(>RegSPY Trd) | P(>RegSPY Adj) |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])
    for s_name, per_dict in results_by_strategy.items():
        for p_name, m in per_dict.items():
            lines.append(
                f"| **{s_name}** | **{p_name}** | {m['return_pct']:+.1f}% | {m['cagr_pct']:+.1f}% | {m['sharpe']:.2f} | {m['sortino']:.2f} | {m['maxdd_pct']:.1f}% | {m['trades']} | {m['win_rate_pct']:.1f}% | {m['pf']:.2f} | {m['boot_p_gt_0_vs_reg_spy_trd']:.1f}% | {m['boot_p_gt_0_vs_reg_spy_adj']:.1f}% |"
            )

    md_text = "\r\n".join(lines) + "\r\n"
    md_file.write_bytes(md_text.encode("utf-8"))

    print("\n".join(lines))
    print(f"\n[SAQLANDI] Markdown: {md_file}")
    print(f"[SAQLANDI] JSON:     {json_file}")
    return payload


if __name__ == "__main__":
    generate_canonical_report()
'''

write_crlf(ROOT / "run_canonical_report.py", CANONICAL_REPORT_CODE)
print("[OK] run_canonical_report.py to'g'ri formatda yaratildi.")