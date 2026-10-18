"""
run_robustness.py - Strategiya natijalarining mustahkamlik tekshiruvlari (PIT tarkib, jonli qoidalar).

A) Oddiy rejim-filtrli SPY bilan taqqoslash ("SPY > SMA200 bo'lsa SPY, bo'lmasa naqd")
B) Xarajatlarga sezgirlik (0.1% / 0.2% / 0.5% har tomonga)
C) Parametr sezgirligi (bittadan o'zgartirish: stop ATR, target, ushlash muddati)
D) Block-bootstrap: Sharpe va rejim-SPY ga nisbatan ortiqcha daromad uchun ishonch oralig'i

Ishga tushirish:  python run_robustness.py          (to'liq, bir necha daqiqa)
                  python run_robustness.py --fast   (kichik panjara, 500 bootstrap)

ESLATMA: hech qanday parametr TEST davriga qarab tanlanmaydi. Panjara faqat sezgirlikni ko'rsatadi;
ko'p variant sinalganda tasodifan yaxshi ko'ringan katak chiqishi mumkin (multiple testing).
"""
import sqlite3
import sys

import numpy as np
import pandas as pd

from engine import Params, run_backtest
import strategies as st
from sp500_history import SP500HistoryManager
from run_backtest import load_all, equity_stats, PERIODS

BASE = dict(tp_pct=0.35, max_hold=35)


def regime_timed_spy(bench_close: pd.Series, regime: pd.Series):
    """SPY ni faqat OLDINGI kun yopilishida rejim bull bo'lsa ushlaydi, aks holda naqd (0%)."""
    ret = bench_close.pct_change().fillna(0.0)
    pos = regime.reindex(ret.index).fillna(False).astype(float).shift(1).fillna(0.0)
    eq = 10_000.0 * (1.0 + ret * pos).cumprod()
    return eq, pos


def _boot_idx(n: int, n_boot: int, block: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    block = max(1, min(block, n))
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, nb))
    return (starts[:, :, None] + np.arange(block)).reshape(n_boot, -1)[:, :n]


def bootstrap_sharpe(rets, n_boot=2000, block=10, seed=0) -> np.ndarray:
    r = np.asarray(rets, dtype=float)
    s = r[_boot_idx(len(r), n_boot, block, seed)]
    sd = s.std(axis=1, ddof=1)
    return np.where(sd > 0, s.mean(axis=1) / np.where(sd > 0, sd, 1.0) * np.sqrt(252), 0.0)


def bootstrap_excess(rets_a, rets_b, n_boot=2000, block=10, seed=0) -> np.ndarray:
    """Yillik ortiqcha daromad (a - b) o'rtachasi, juft block-bootstrap."""
    d = np.asarray(rets_a, dtype=float) - np.asarray(rets_b, dtype=float)
    return d[_boot_idx(len(d), n_boot, block, seed)].mean(axis=1) * 252


def pit_signals(feats, regime, hm, fn):
    out = {}
    for t, f in feats.items():
        s = fn(f, regime).copy()
        if hm.complete or t in hm.membership_rules:
            s["signal"] = s["signal"] & hm.membership_mask(t, s.index)
        out[t] = s
    return out


def _stats(curve, a, b):
    es = equity_stats(curve["equity"].loc[a:b])
    return es


def _fmt_row(label, vals):
    return f"{label:<22}" + "".join(f"{r*100:8.1f}% {s:6.2f} |" for r, s in vals)


def main(fast: bool = False):
    conn = sqlite3.connect("data/screener.db")
    feats, ohlc, regime, bench_f = load_all(conn)
    conn.close()
    dates = bench_f.index
    bench_close = bench_f["close"]
    hm = SP500HistoryManager()
    n_boot = 500 if fast else 2000
    print(f"PIT tarkib: {'TO`LIQ' if hm.complete else 'TO`LIQ EMAS'} | tickerlar: {len(feats)} | oxirgi sana: {dates[-1].date()}")

    rt_eq, rt_pos = regime_timed_spy(bench_close, regime)
    hdr = "".join(f"{p:^17}|" for p in PERIODS)
    sub = "".join(f"{'ret':>9} {'Shrp':>6} |" for _ in PERIODS)

    for strat in ["breakout", "pullback"]:
        sig = pit_signals(feats, regime, hm, st.STRATEGIES[strat])
        runs = {}

        def run(**kw):
            key = tuple(sorted(kw.items()))
            if key not in runs:
                runs[key] = run_backtest(ohlc, sig, dates, Params.live(**{**BASE, **kw}))
            return runs[key]

        tr, base_curve = run()
        print("\n" + "=" * 90 + f"\nSTRATEGIYA: {strat.upper()}\n" + "=" * 90)

        # A) oddiy rejim-filtrli SPY bilan taqqoslash
        print("\nA) Taqqoslash (alfa strategiyadanmi yoki faqat SPY>SMA200 rejim filtridanmi?)")
        print(f"{'':<22}{hdr}\n{'':<22}{sub}")
        rows = {
            "Strategiya": [(lambda e: (e['total'], e['sharpe']))(_stats(base_curve, a, b)) for a, b in PERIODS.values()],
            "Rejim-filtrli SPY": [(lambda e: (e['total'], e['sharpe']))(equity_stats(rt_eq.loc[a:b])) for a, b in PERIODS.values()],
            "SPY buy&hold": [(lambda e: (e['total'], e['sharpe']))(equity_stats(bench_close.loc[a:b])) for a, b in PERIODS.values()],
        }
        for k, v in rows.items():
            print(_fmt_row(k, v))
        print("Rejim-filtrli SPY bozorda bo'lgan kunlar ulushi: " +
              ", ".join(f"{p} {rt_pos.loc[a:b].mean()*100:.0f}%" for p, (a, b) in PERIODS.items()))

        # B) xarajatlar
        print("\nB) Xarajatlarga sezgirlik (har tomonga)")
        print(f"{'':<22}{hdr}\n{'':<22}{sub}")
        for c in (0.001, 0.002, 0.005):
            _, cv = run(cost_per_side=c)
            print(_fmt_row(f"{c*100:.1f}%" + (" (baza)" if c == 0.001 else ""),
                           [(lambda e: (e['total'], e['sharpe']))(_stats(cv, a, b)) for a, b in PERIODS.values()]))

        # C) parametr sezgirligi
        print("\nC) Parametr sezgirligi (bittadan o'zgartiriladi; baza: stop 2.0 ATR, target 35%, 35 kun)")
        print(f"{'':<22}{hdr}\n{'':<22}{sub}")
        grid = [("stop_atr", [2.5, 3.0] if fast else [1.5, 2.0, 2.5, 3.0]),
                ("tp_pct", [0.20, 0.50] if fast else [0.20, 0.35, 0.50]),
                ("max_hold", [20, 50] if fast else [20, 35, 50])]
        cells = []
        for name, vals in grid:
            for v in vals:
                _, cv = run(**{name: v})
                res = [(lambda e: (e['total'], e['sharpe']))(_stats(cv, a, b)) for a, b in PERIODS.values()]
                cells.append(res)
                is_base = (name == "stop_atr" and v == 2.0) or (BASE.get(name) == v)
                print(_fmt_row(f"{name}={v}" + (" *" if is_base else ""), res))
        sh = np.array([[s for _, s in r] for r in cells])
        print("Sharpe > 0 bo'lgan kataklar ulushi: " +
              ", ".join(f"{p} {(sh[:, i] > 0).mean()*100:.0f}%" for i, p in enumerate(PERIODS)) +
              " | Sharpe oralig'i: " + ", ".join(f"{p} [{sh[:, i].min():.2f}; {sh[:, i].max():.2f}]" for i, p in enumerate(PERIODS)))

        # D) bootstrap
        print(f"\nD) Block-bootstrap ({n_boot} qayta tanlash, blok 10 kun): 90% ishonch oralig'i")
        print(f"{'Davr':<8}{'Sharpe [5%; 95%]':>24}{'P(Sharpe>0)':>13}{'Ortiqcha yillik vs rejim-SPY [5%; 95%]':>46}{'P(>0)':>8}")
        s_ret = base_curve["equity"].pct_change().fillna(0.0)
        b_ret = rt_eq.pct_change().reindex(s_ret.index).fillna(0.0)
        for p, (a, b) in PERIODS.items():
            r, rb = s_ret.loc[a:b].values, b_ret.loc[a:b].values
            if len(r) < 30:
                continue
            bs = bootstrap_sharpe(r, n_boot)
            be = bootstrap_excess(r, rb, n_boot)
            print(f"{p:<8}{f'{np.mean(bs):5.2f} [{np.percentile(bs,5):5.2f}; {np.percentile(bs,95):5.2f}]':>24}"
                  f"{(bs > 0).mean()*100:12.0f}%"
                  f"{f'{np.mean(be)*100:6.1f}% [{np.percentile(be,5)*100:6.1f}%; {np.percentile(be,95)*100:6.1f}%]':>46}"
                  f"{(be > 0).mean()*100:7.0f}%")

    print("\nTalqin: ortiqcha daromad oralig'i 0 ni o'z ichiga olsa yoki P(>0) < 95% bo'lsa, rejim-filtrli SPY dan "
          "statistik jihatdan ustunlik isbotlanmagan. Bu tarixiy tahlil, kelajak natijasining kafolati emas.")


if __name__ == "__main__":
    main(fast="--fast" in sys.argv)
