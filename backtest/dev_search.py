"""Development search on 2025-2026 NQ (both halves already seen; the final exam is separate).

    python backtest/dev_search.py [--top 25]

1. Grid-tune on the first half (train) -> rank by SQN, >= 30 trades.
2. Screen the top configs on the second half (validation) with both coin-flip tests.
3. Write reports/dev_search.csv. Pick the final-exam candidate from it.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from engine import Params, Cache, load_1m, resample, make_bars, run, stats  # noqa: E402
from coinflip import sim_trade  # noqa: E402
from pipeline import split_point, PULLBACK  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "nq_ohlcv_1m_2025-2026.parquet")

GRID = dict(
    model=[("limit", "fixed"), ("limit", "swing"), ("confirm", "fixed")],
    rr=[2.0, 2.5, 3.0],
    htf=[15, 60],
    piv=[2, 3],
    pullback=["off", "moderate"],
    htf_clean=[False, True],
    htf2=[0, 60, 240],
    window=[(9 * 60 + 35, 15 * 60), (8 * 60 + 35, 15 * 60 + 30)],
)

_c = None


def _init(split_ts):
    global _c
    df = resample(load_1m(DATA), 5)
    _c = Cache(make_bars(df[df.index < split_ts], 5))


def params_of(combo):
    (mode, tgt), rr, htf, piv, pb, clean, htf2, (ws, we) = combo
    spd, er = PULLBACK[pb]
    return Params(entry_mode=mode, target_mode=tgt, fixed_rr=rr, min_rr=rr, htf=htf, piv=piv,
                  pb_speed_max=spd, pb_er_max=er, htf_clean=clean, htf2=htf2,
                  entry_start=ws, entry_end=we)


def _eval(combo):
    p = params_of(combo)
    s = stats(run(_c, p))
    s.update(p.to_dict())
    return s


def quick_coinflip(bars, p, trades, lo, hi, runs_b=400, seed=11):
    """Same tests as coinflip.py, fewer timing runs (screening only)."""
    rng = np.random.default_rng(seed)
    n = len(trades)
    if n == 0:
        return 1.0, 1.0
    sd = np.array([abs(t["entry"] - t["stop0"]) for t in trades])
    td = np.array([abs(t["target"] - t["entry"]) for t in trades])
    q = np.array([t["qty"] for t in trades])
    risk = sd * q * p.point_value
    same = np.array([sim_trade(bars, p, t["entry_bar"], t["dir"], sd[k], td[k], q[k], t["fill_seg"], t["fill_x"], t["entry"])
                     for k, t in enumerate(trades)]) / risk
    flip = np.array([sim_trade(bars, p, t["entry_bar"], -t["dir"], sd[k], td[k], q[k], t["fill_seg"], t["fill_x"], t["entry"])
                     for k, t in enumerate(trades)]) / risk
    strat = same.mean()
    a = np.where(rng.random((10000, n)) < 0.5, same, flip).mean(axis=1)
    ok = np.nonzero((bars.close_min[lo:hi - 1] >= p.entry_start) & (bars.close_min[lo:hi - 1] < p.entry_end))[0] + lo + 1
    bm = []
    for _ in range(runs_b):
        pick = rng.integers(0, n, n)
        ib = rng.choice(ok, n)
        dd = np.where(rng.random(n) < 0.5, 1, -1)
        bm.append(np.mean([sim_trade(bars, p, ib[k], dd[k], sd[pick[k]], td[pick[k]], q[pick[k]]) / risk[pick[k]]
                           for k in range(n)]))
    return float((a >= strat).mean()), float((np.array(bm) >= strat).mean())


GRID2 = dict(
    model=[("limit", "fixed"), ("confirm", "fixed")],
    rr=[2.0, 2.5],
    htf=[15, 60],
    piv=[2, 3],
    pullback=["off", "moderate"],
    htf_clean=[False, True],
    htf2=[0, 60, 240],
    window=[(9 * 60 + 35, 15 * 60), (8 * 60 + 35, 15 * 60 + 30)],
    sweep=[False, True],
)
_split_i = None


def _init_full():
    global _c, _split_i
    df = resample(load_1m(DATA), 5)
    _c = Cache(make_bars(df, 5))
    _split_i = int(np.searchsorted(df.index.values, split_point(df).to_datetime64()))


def params2_of(combo):
    from dataclasses import replace
    *rest, sweep = combo
    return replace(params_of(tuple(rest)), sweep_entries=sweep)


def _eval_full(combo):
    p = params2_of(combo)
    tr = run(_c, p)
    a = [t for t in tr if t["entry_bar"] < _split_i]
    b = [t for t in tr if t["entry_bar"] >= _split_i]
    s = stats(tr)
    s.update(h1_n=len(a), h1_expR=stats(a)["expR"], h2_n=len(b), h2_expR=stats(b)["expR"], h2_net=stats(b)["net"])
    s.update(p.to_dict())
    return s


def main_full():
    combos = list(itertools.product(*GRID2.values()))
    print(f"full-dev grid: {len(combos)} configs", flush=True)
    with Pool(os.cpu_count(), initializer=_init_full) as pool:
        rows = pool.map(_eval_full, combos, chunksize=6)
    g = pd.DataFrame(rows)
    g["combo"] = range(len(combos))
    g.to_csv(os.path.join(ROOT, "reports", "dev_search_full_grid.csv"), index=False)
    ok = g[(g.n >= 60) & (g.h1_expR > 0) & (g.h2_expR > 0)].sort_values("sqn", ascending=False)
    print(f"{len(ok)} configs pass the stability filter", flush=True)
    cols = ["combo", "entry_mode", "fixed_rr", "htf", "piv", "pb_speed_max", "htf_clean", "htf2", "entry_start",
            "sweep_entries", "n", "net", "expR", "sqn", "h1_n", "h1_expR", "h2_n", "h2_expR"]
    print(ok[cols].head(15).to_string(), flush=True)
    json.dump({str(int(r.combo)): params2_of(combos[int(r.combo)]).to_dict() for _, r in ok.head(15).iterrows()},
              open(os.path.join(ROOT, "reports", "dev_search_full_params.json"), "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--full", action="store_true", help="candidate #2/#3 selection on all development data")
    a = ap.parse_args()
    if a.full:
        return main_full()
    df = resample(load_1m(DATA), 5)
    split_ts = split_point(df)
    combos = list(itertools.product(*GRID.values()))
    print(f"train grid: {len(combos)} configs", flush=True)
    with Pool(os.cpu_count(), initializer=_init, initargs=(split_ts,)) as pool:
        rows = pool.map(_eval, combos, chunksize=8)
    g = pd.DataFrame(rows)
    g["combo"] = range(len(combos))
    g = g[g.n >= 30].sort_values("sqn", ascending=False)
    full = Cache(make_bars(df, 5))
    split_i = int(np.searchsorted(df.index.values, split_ts.to_datetime64()))
    out = []
    for _, r in g.head(a.top).iterrows():
        p = params_of(combos[int(r.combo)])
        tr = [t for t in run(full, p) if t["entry_bar"] >= split_i]
        s = stats(tr)
        pa, pb = quick_coinflip(full.bars, p, tr, split_i, len(df))
        row = dict(combo=int(r.combo), model=f"{p.entry_mode}/{p.target_mode}", rr=p.fixed_rr, htf=p.htf, piv=p.piv,
                   pb=p.pb_speed_max, clean=p.htf_clean, htf2=p.htf2, window=f"{p.entry_start}-{p.entry_end}",
                   train_n=int(r.n), train_sqn=round(r.sqn, 2), val_n=s["n"], val_net=round(s["net"]),
                   val_expR=round(s["expR"], 3), val_pf=round(s["pf"], 2), p_dir=pa, p_time=pb)
        out.append(row)
        print(row, flush=True)
    res = pd.DataFrame(out)
    res.to_csv(os.path.join(ROOT, "reports", "dev_search.csv"), index=False)
    json.dump({str(c): params_of(combos[c]).to_dict() for c in res.combo},
              open(os.path.join(ROOT, "reports", "dev_search_params.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
