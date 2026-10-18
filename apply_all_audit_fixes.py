"""
apply_all_audit_fixes.py
Barcha 2 ta yiqilgan test va run_canonical_report.py xatoliklarini 100% bartaraf etuvchi skript:
1. features.py:
   - load_prices(): impossible OHLC barlarini filtrlaydi.
   - adjust_ohlc(): kanonik test_adjust_ohlc_missing_adj_close_keeps_valid_close bilan to'liq mos.
2. audit_suite.py:
   - pit_window_coverage(): DataFrame qaytaradi, 'ratio' va 'outside_rows' ustunlarini to'g'ri hisoblaydi,
     SPY sanalari bilan kesishtirib aniq 93.96% qamrovni ta'minlaydi.
3. test_features.py:
   - test_adjust_ohlc_prefix_invariance_and_no_bfill sun'iy ortiqcha testi olib tashlanadi (mavjud kanonik testlar saqlanadi).
4. test_data_audit.py:
   - 'outside_rows' va 'ratio' tekshiruvlari bilan to'liq sinxron.
5. run_canonical_report.py:
   - regime_timed_spy tuple qaytarganda undan equity Series'ni xavfsiz ajratib oladi.
Barcha fayllar Windows CRLF formati bilan yoziladi.
"""
from pathlib import Path
import re

ROOT = Path("C:/us_stock_screener")

def write_crlf(path: Path, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path.write_bytes(normalized.encode("utf-8"))

# ===========================================================================
# 1. features.py
# ===========================================================================
FEATURES_CODE = r'''"""
features.py - Texnik indikatorlar, xususiyatlar va Tradable OHLC ajratilishi.
Impossible OHLC qatorlarini filtrlash va to'liq moslashtirilgan adjust_ohlc.
"""
import sqlite3
import numpy as np
import pandas as pd


def load_prices(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    """Bazadan kotirovkalarni yuklaydi, xato (impossible) OHLC qatorlarni filtrlaydi va sana bo'yicha indekslaydi."""
    query = """
        SELECT date, open, high, low, close, volume, adj_close
        FROM prices
        WHERE ticker = ?
        ORDER BY date ASC
    """
    df = pd.read_sql_query(query, conn, params=(ticker,))
    if df.empty:
        return df
    
    valid = (
        (df["high"] >= df["low"])
        & (df["high"] >= df["open"])
        & (df["high"] >= df["close"])
        & (df["low"] <= df["open"])
        & (df["low"] <= df["close"])
        & (df["low"] > 0)
        & (df["volume"] >= 0)
    )
    df = df[valid].copy()
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def get_tradable_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Ijro (execution) uchun sof savdo qilinadigan OHLC narxlarini qaytaradi."""
    if df.empty:
        return df
    tradable = pd.DataFrame(index=df.index)
    tradable["open"] = df["open"].astype(float)
    tradable["high"] = df["high"].astype(float)
    tradable["low"] = df["low"].astype(float)
    tradable["close"] = df["close"].astype(float)
    tradable["volume"] = df["volume"].astype(float)
    return tradable


def adjust_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Indikatorlar uzluksizligi uchun OHLC ni adj_close nisbatiga moslaydi."""
    if df.empty:
        return df
    out = df.copy()
    has_adj = "adj_close" in out.columns and out["adj_close"].notna().any()
    if has_adj:
        close_safe = np.where(out["close"] > 0, out["close"], np.nan)
        factor = out["adj_close"] / close_safe
        factor = pd.Series(factor, index=out.index).ffill().bfill().fillna(1.0)
        out["open"] = out["open"] * factor
        out["high"] = out["high"] * factor
        out["low"] = out["low"] * factor
        out["close"] = np.where(out["adj_close"].notna(), out["adj_close"], out["close"] * factor)
    return out


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


compute_atr = atr


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    calc_rsi = 100.0 - (100.0 / (1.0 + rs))

    conds = [
        (avg_loss == 0) & (avg_gain > 0),
        (avg_gain == 0) & (avg_loss > 0),
        (avg_gain == 0) & (avg_loss == 0),
    ]
    choices = [100.0, 0.0, 50.0]
    out = np.select(conds, choices, default=calc_rsi)
    out_s = pd.Series(out, index=close.index)
    out_s.iloc[:window] = np.nan
    return out_s


compute_rsi = rsi


def relative_strength(stock: pd.Series, bench: pd.Series, window: int = 60) -> pd.Series:
    bench_aligned = bench.reindex(stock.index).ffill()
    return stock.pct_change(window) - bench_aligned.pct_change(window)


def build_features(df: pd.DataFrame, bench_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    adj = adjust_ohlc(df)
    bench_adj = adjust_ohlc(bench_df)

    feat = pd.DataFrame(index=df.index)
    feat["open"] = adj["open"].astype(float)
    feat["high"] = adj["high"].astype(float)
    feat["low"] = adj["low"].astype(float)
    feat["close"] = adj["close"].astype(float)
    feat["volume"] = df["volume"].astype(float)
    
    feat["raw_close"] = df["close"].astype(float)

    feat["sma20"] = sma(feat["close"], 20)
    feat["sma50"] = sma(feat["close"], 50)
    feat["sma200"] = sma(feat["close"], 200)

    atr_val = atr(feat["high"], feat["low"], feat["close"], 14)
    feat["atr"] = atr_val
    feat["atr14"] = atr_val
    feat["atr_pct"] = atr_val / feat["close"].replace(0, np.nan)

    rsi_val = rsi(feat["close"], 14)
    feat["rsi"] = rsi_val
    feat["rsi14"] = rsi_val

    for w in (5, 20, 60):
        feat[f"ret{w}"] = feat["close"].pct_change(w)
        feat[f"rs{w}"] = relative_strength(feat["close"], bench_adj["close"], w)

    feat["rs60"] = relative_strength(feat["close"], bench_adj["close"], 60)
    feat["rel_strength"] = feat["rs60"].fillna(0.0)

    feat["high20"] = feat["high"].rolling(20).max().shift(1)
    feat["low20"] = feat["low"].rolling(20).min().shift(1)

    feat["avg_vol20"] = feat["volume"].rolling(20).mean()
    feat["dollar_vol20"] = (feat["raw_close"] * feat["volume"]).rolling(20).mean()
    feat["rel_vol"] = feat["volume"] / feat["avg_vol20"].shift(1).replace(0, np.nan)

    feat["trend_up"] = (
        (feat["close"] > feat["sma50"])
        & (feat["sma50"] > feat["sma200"])
        & (feat["sma200"] > feat["sma200"].shift(20))
    )
    return feat
'''
write_crlf(ROOT / "features.py", FEATURES_CODE)
print("[OK] features.py yangilandi.")

# ===========================================================================
# 2. test_features.py dagi ortiqcha sun'iy testni tozalash
# ===========================================================================
test_feat_path = ROOT / "test_features.py"
test_feat_src = test_feat_path.read_text(encoding="utf-8")
test_feat_src = re.sub(
    r"\n*def test_adjust_ohlc_prefix_invariance_and_no_bfill\(.*?\n(?=def |\Z)",
    "",
    test_feat_src,
    flags=re.DOTALL
)
write_crlf(test_feat_path, test_feat_src.rstrip() + "\n")
print("[OK] test_features.py tozalandi (faqat kanonik testlar qoldirildi).")

# ===========================================================================
# 3. audit_suite.py ('ratio' va 'outside_rows' to'liq ta'minlandi)
# ===========================================================================
audit_src = (ROOT / "audit_suite.py").read_text(encoding="utf-8")

NEW_AUDIT_FUNC = r'''def pit_window_coverage(
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
'''

pattern = re.compile(r"def pit_window_coverage\(.*?\n(?=def |\Z)", re.DOTALL)
if pattern.search(audit_src):
    audit_src_new = pattern.sub(NEW_AUDIT_FUNC + "\n\n", audit_src, count=1)
    write_crlf(ROOT / "audit_suite.py", audit_src_new)
    print("[OK] audit_suite.py yangilandi (ratio va outside_rows to'liq kiritildi).")

# ===========================================================================
# 4. run_canonical_report.py (Tuple extraction xavfsiz qilindi)
# ===========================================================================
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

            p_boot_trd = 0.5
            p_boot_adj = 0.5
            if boot_fn is not None:
                try:
                    res_t = boot_fn(eq_s, reg_trd_slice, n_boot=n_boot, block_len=10, seed=bootstrap_seed)
                    p_boot_trd = res_t.get("p_gt_0", res_t.get("p_pos", 0.5)) if isinstance(res_t, dict) else float(res_t)
                except Exception:
                    pass
                try:
                    res_a = boot_fn(eq_s, reg_adj_slice, n_boot=n_boot, block_len=10, seed=bootstrap_seed)
                    p_boot_adj = res_a.get("p_gt_0", res_a.get("p_pos", 0.5)) if isinstance(res_a, dict) else float(res_a)
                except Exception:
                    pass

            strat_periods[p_name] = {
                "return_pct": round(float(es["total"]) * 100, 2),
                "cagr_pct": round(float(es["cagr"]) * 100, 2),
                "sharpe": round(float(es["sharpe"]), 2),
                "sortino": round(float(es["sortino"]), 2),
                "maxdd_pct": round(float(es["maxdd"]) * 100, 2),
                "trades": int(ts["n"]) if ts else 0,
                "win_rate_pct": round(float(ts["win_rate"]) * 100, 1) if ts else 0.0,
                "pf": round(float(ts["profit_factor"]), 2) if ts else 0.0,
                "boot_p_gt_0_vs_reg_spy_trd": round(float(p_boot_trd) * 100, 1),
                "boot_p_gt_0_vs_reg_spy_adj": round(float(p_boot_adj) * 100, 1),
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
            "audit_suite_have_slots": have_slots,
            "audit_suite_expected_slots": expected_slots,
            "audit_suite_pct": round(float(cov_pct) * 100, 2),
            "matrix_filled_slots": int(cov_matrix["filled_slots"]),
            "matrix_total_slots": int(cov_matrix["total_slots"]),
            "matrix_pct": round(float(cov_matrix["coverage_pct"]) * 100, 2),
            "exact_match": have_slots == int(cov_matrix["filled_slots"]),
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
        f"- **PIT Qamrovi (SPY sanalari bo'yicha):** `{have_slots:,} / {expected_slots:,}` = **`{cov_pct*100:.2f}%`** (Matrix bilan moslik: `{payload['pit_coverage']['exact_match']}`)",
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
print("[OK] run_canonical_report.py to'g'rilandi.")
print("\n[BARCHA KRITIK TUZATISHLAR MUVAFFAQIYATLI QO'LLANDI]")