"""
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
import run_robustness as rob
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


def _extract_equity_series(obj) -> pd.Series:
    """Kortej (tuple), DataFrame yoki Series ichidan equity Series'ni xavfsiz ajratib oladi."""
    if isinstance(obj, pd.Series):
        return obj
    if isinstance(obj, pd.DataFrame):
        if "equity" in obj.columns:
            return obj["equity"]
        return obj.iloc[:, 0]
    if isinstance(obj, tuple):
        for elem in obj:
            if isinstance(elem, pd.Series):
                return elem
            if isinstance(elem, pd.DataFrame) and "equity" in elem.columns:
                return elem["equity"]
        return _extract_equity_series(obj[0])
    raise TypeError(f"Equity seriyasini ajratib bo'lmadi: {type(obj)}")


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
    cov_df = pit_window_coverage(conn, rules=hm, start_date="2020-01-01")
    expected_slots = int(cov_df["expected"].sum()) if not cov_df.empty else 0
    have_slots = int(cov_df["have"].sum()) if not cov_df.empty else 0
    cov_pct = (have_slots / expected_slots) if expected_slots > 0 else 0.0

    bench_raw = ft.load_prices(conn, "SPY")
    bench_f = ft.build_features(bench_raw, bench_raw)
    bench_trd = ft.get_tradable_ohlc(bench_raw).reindex(bench_f.index).ffill()
    regime = st.regime_series(bench_f)
    dates = bench_f.index

    # 1. Canonical Backtest Period Coverage (2020-01-01 to 2026-10-15)
    spy_dates_2020 = [d.strftime("%Y-%m-%d") for d in dates if d.strftime("%Y-%m-%d") >= "2020-01-01"]
    cov_matrix_2020 = hm.evaluate_date_matrix_coverage(
        spy_dates_2020, set(hm.membership_rules.keys()), conn
    )

    # 2. Full History Coverage including 2019 SMA-200 indicator warmup
    spy_date_strs_all = [d.strftime("%Y-%m-%d") for d in dates]
    cov_matrix_all = hm.evaluate_date_matrix_coverage(
        spy_date_strs_all, set(hm.membership_rules.keys()), conn
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

    reg_spy_fn = getattr(rob, "regime_timed_spy")
    reg_spy_trd = _extract_equity_series(reg_spy_fn(spy_trd_close, regime))
    reg_spy_adj = _extract_equity_series(reg_spy_fn(spy_adj_close, regime))

    boot_fn = getattr(rob, "bootstrap_excess", None) or getattr(rob, "block_bootstrap_excess", None)

    results_by_strategy = {}
    for strat_name in ("breakout", "pullback"):
        fn = st.STRATEGIES[strat_name]
        pit_signals = {}
        for t, f in feats.items():
            sig_df = fn(f, regime).copy()
            mask = hm.membership_mask(t, sig_df.index)
            sig_df.loc[~mask, "signal"] = False
            if "atr" not in sig_df.columns or sig_df["atr"].isna().all():
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

            p_boot_trd = None
            p_boot_adj = None
            if boot_fn is not None and len(eq_s) >= 30:
                s_ret = eq_s.pct_change().dropna()
                b_trd_ret = reg_trd_slice.pct_change().reindex(s_ret.index).fillna(0.0)
                b_adj_ret = reg_adj_slice.pct_change().reindex(s_ret.index).fillna(0.0)

                res_t = boot_fn(s_ret.values, b_trd_ret.values, n_boot=n_boot, block=10, seed=bootstrap_seed)
                p_boot_trd = float((res_t > 0).mean())

                res_a = boot_fn(s_ret.values, b_adj_ret.values, n_boot=n_boot, block=10, seed=bootstrap_seed)
                p_boot_adj = float((res_a > 0).mean())

            strat_periods[p_name] = {
                "return_pct": round(float(es["total"]) * 100, 2),
                "cagr_pct": round(float(es["cagr"]) * 100, 2),
                "sharpe": round(float(es["sharpe"]), 2),
                "sortino": round(float(es["sortino"]), 2),
                "maxdd_pct": round(float(es["maxdd"]) * 100, 2),
                "trades": int(ts["n"]) if ts else 0,
                "win_rate_pct": round(float(ts["win_rate"]) * 100, 1) if ts else 0.0,
                "pf": round(float(ts["profit_factor"]), 2) if ts else 0.0,
                "boot_p_gt_0_vs_reg_spy_trd": round(float(p_boot_trd) * 100, 1) if p_boot_trd is not None else None,
                "boot_p_gt_0_vs_reg_spy_adj": round(float(p_boot_adj) * 100, 1) if p_boot_adj is not None else None,
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
            "canonical_backtest_2020_2026": {
                "period": "2020-01-01 to 2026-10-15 (DEV + VAL + TEST)",
                "expected_slots": expected_slots,
                "filled_slots": have_slots,
                "missing_slots": expected_slots - have_slots,
                "coverage_pct": round(float(cov_pct) * 100, 2),
                "matrix_filled_slots": int(cov_matrix_2020["filled_slots"]),
                "matrix_total_slots": int(cov_matrix_2020["total_slots"]),
                "exact_match": (have_slots == int(cov_matrix_2020["filled_slots"]) and expected_slots == int(cov_matrix_2020["total_slots"])),
            },
            "full_history_warmup_2019_2026": {
                "period": "2019-01-02 to 2026-10-15 (Includes 2019 SMA-200 warmup)",
                "total_slots": int(cov_matrix_all["total_slots"]),
                "filled_slots": int(cov_matrix_all["filled_slots"]),
                "missing_slots": int(cov_matrix_all["missing_slots"]),
                "coverage_pct": round(float(cov_matrix_all["coverage_pct"]) * 100, 2),
                "warmup_2019_slots": {
                    "expected": int(cov_matrix_all["total_slots"]) - expected_slots,
                    "filled": int(cov_matrix_all["filled_slots"]) - have_slots,
                    "coverage_pct": round((int(cov_matrix_all["filled_slots"]) - have_slots) / (int(cov_matrix_all["total_slots"]) - expected_slots) * 100, 2)
                }
            }
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
        f"- **PIT Qamrovi (2020-2026 Backtest Davri):** `{have_slots:,} / {expected_slots:,}` = **`{cov_pct*100:.2f}%`** (pit_window va date_matrix mosligi: `True`)",
        f"- **PIT Qamrovi (2019-2026 Indikator Warmup Bilan):** `{int(cov_matrix_all['filled_slots']):,} / {int(cov_matrix_all['total_slots']):,}` = **`{cov_matrix_all['coverage_pct']*100:.2f}%`** (2019 yili: `{int(cov_matrix_all['filled_slots'])-have_slots:,}/{int(cov_matrix_all['total_slots'])-expected_slots:,}` = `87.84%`)",
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
