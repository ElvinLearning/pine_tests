"""Tune on the first half, test once on the second half, apply the coin-flip rule.

    python backtest/pipeline.py [--data data/nq_ohlcv_1m_2025-2026.parquet]

Steps
  1. Load 1-minute NQ bars, build 5-minute chart bars.
  2. Split at the CME session boundary nearest the halfway bar.
  3. Grid-search parameters on the FIRST HALF ONLY (in-sample).
     Pick the best by SQN (mean R / stdev R * sqrt(n)) with >= 30 trades.
  4. Lock those parameters. Run once over the whole history (indicators are
     causal) and keep only trades that enter in the SECOND HALF (out-of-sample).
  5. Coin-flip rule on the OOS trades (see coinflip.py).
  6. Prop-eval simulation on both halves.
  7. Write reports/ and params/, and rewrite the AUTO-PARAMS block of the
     Pine script (self-update). Parameters are promoted to the Pine file only
     when the coin-flip gate passes, unless --force is given; the gate status
     is always written so the chart shows it.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from engine import Params, Cache, load_1m, resample, make_bars, run, stats, daily_risk, NY  # noqa: E402
from coinflip import coin_flip_test  # noqa: E402
from prop_eval import simulate, EvalRules  # noqa: E402
import update_pine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART_TF = 5

# Mechanical rules x context layer. Fixed values (FVG on, momentum kill 2 ATR,
# no break-even) were chosen from first-half results only.
GRID = dict(
    model=[("limit", "swing"), ("limit", "fixed"), ("confirm", "fixed")],
    rr=[2.0, 2.5, 3.0],
    htf=[15, 60, 240],
    piv=[2, 3],
    pullback=["off", "moderate", "strict"],      # G1
    htf_clean=[False, True],                     # G2
    pd_level=[1.0, 0.5],                         # G2
    appr_k=[0.0, 0.5],                           # G3
)
PULLBACK = {"off": (99.0, 1.0), "moderate": (0.6, 0.7), "strict": (0.4, 0.5)}
MIN_IS_TRADES = 30

_cache = None


def _init(path, split_ts):
    global _cache
    df = resample(load_1m(path), CHART_TF)
    df = df[df.index < split_ts]
    _cache = Cache(make_bars(df, CHART_TF))


def _combo_params(combo):
    (mode, tgt), rr, htf, piv, pb, clean, pdl, appr = combo
    spd, er = PULLBACK[pb]
    return Params(entry_mode=mode, target_mode=tgt, fixed_rr=rr, min_rr=rr, htf=htf, piv=piv,
                  require_fvg=True, mom_k=2.0, be_r=0.0, pb_speed_max=spd, pb_er_max=er,
                  htf_clean=clean, pd_level=pdl, appr_k=appr)


def _eval_combo(combo):
    p = _combo_params(combo)
    tr = run(_cache, p)
    s = stats(tr)
    s.update(p.to_dict())
    return s


def split_point(df5: pd.DataFrame) -> pd.Timestamp:
    mid = df5.index[len(df5) // 2].tz_convert(NY)
    # back up to the 18:00 New York session open of that session day
    sess = (mid + pd.Timedelta(hours=6)).normalize() - pd.Timedelta(hours=6)
    return sess.tz_convert("UTC")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "nq_ohlcv_1m_2025-2026.parquet"))
    ap.add_argument("--force", action="store_true", help="write params to Pine even if the gate fails")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--no-pine", action="store_true", help="do not touch the Pine file")
    ap.add_argument("--note", default="", help="free text stored in results.json (e.g. how many times the OOS half was used)")
    args = ap.parse_args()

    t0 = time.time()
    df5 = resample(load_1m(args.data), CHART_TF)
    split_ts = split_point(df5)
    print(f"bars: {len(df5)}  {df5.index[0]} -> {df5.index[-1]}")
    print(f"split (session open): {split_ts.tz_convert(NY)}")

    # ---------------- 3. in-sample grid ----------------
    combos = list(itertools.product(*GRID.values()))
    print(f"grid: {len(combos)} parameter sets on the first half only ...")
    with Pool(args.workers, initializer=_init, initargs=(args.data, split_ts)) as pool:
        rows = pool.map(_eval_combo, combos, chunksize=8)
    grid = pd.DataFrame(rows)
    grid = grid.sort_values(["sqn", "net"], ascending=False)
    ok = grid[grid.n >= MIN_IS_TRADES]
    if ok.empty:
        raise SystemExit("no parameter set made 30 in-sample trades")
    best_row = ok.iloc[0]
    fields = Params().to_dict().keys()
    best = Params(**{k: (best_row[k].item() if hasattr(best_row[k], "item") else best_row[k]) for k in fields})
    print("best in-sample:", {k: best_row[k] for k in ["entry_mode", "target_mode", "fixed_rr", "htf", "piv",
                                                       "pb_speed_max", "pb_er_max", "htf_clean", "pd_level", "appr_k",
                                                       "n", "net", "pf", "win", "expR", "sqn"]})

    # ---------------- 4. locked run, split IS / OOS ----------------
    full = Cache(make_bars(df5, CHART_TF))
    split_i = int(np.searchsorted(df5.index.values, split_ts.to_datetime64()))
    funnel = {}
    trades = run(full, best, funnel=funnel)
    is_tr = [t for t in trades if t["entry_bar"] < split_i]
    oos_tr = [t for t in trades if t["entry_bar"] >= split_i]
    s_is, s_oos = stats(is_tr, "in-sample"), stats(oos_tr, "out-of-sample")
    print("IS :", s_is)
    print("OOS:", s_oos)

    # how did the other top in-sample sets do out of sample? (report only)
    top = []
    for _, r in ok.head(10).iterrows():
        p = Params(**{k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in fields})
        tr = [t for t in run(full, p) if t["entry_bar"] >= split_i]
        so = stats(tr)
        top.append(dict(model=f"{p.entry_mode}/{p.target_mode}", rr=p.fixed_rr, htf=p.htf, piv=p.piv,
                        pb=(p.pb_speed_max, p.pb_er_max), htf_clean=p.htf_clean, pd=p.pd_level, appr=p.appr_k,
                        is_n=int(r.n), is_sqn=round(r.sqn, 2),
                        oos_n=so["n"], oos_net=round(so["net"]), oos_expR=round(so["expR"], 3)))

    # ---------------- 5. coin-flip rule ----------------
    print("coin-flip test ...")
    cf = coin_flip_test(full.bars, best, oos_tr, split_i, len(df5))
    print("coin flip:", cf["verdict"], cf.get("checks"))

    # ---------------- 6. prop eval ----------------
    day = full.bars.day_key
    ev_is = simulate(is_tr, day, day[:split_i], EvalRules())
    ev_oos = simulate(oos_tr, day, day[split_i:], EvalRules())
    ev_oos_nocons = simulate(oos_tr, day, day[split_i:], EvalRules(consistency_pct=0))
    print("eval OOS:", ev_oos)

    # ---------------- 7. outputs ----------------
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "params"), exist_ok=True)
    grid.to_csv(os.path.join(ROOT, "reports", "is_grid.csv"), index=False)
    ts = full.bars.df.index
    tdf = pd.DataFrame(trades)
    tdf["entry_time"] = ts[tdf.entry_bar].tz_convert(NY)
    tdf["exit_time"] = ts[tdf.exit_bar].tz_convert(NY)
    tdf["half"] = np.where(tdf.entry_bar >= split_i, "OOS", "IS")
    tdf.to_csv(os.path.join(ROOT, "reports", "trades.csv"), index=False)

    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    meta = dict(
        version=stamp,
        data=dict(file=os.path.basename(args.data), first=str(df5.index[0].tz_convert(NY)),
                  last=str(df5.index[-1].tz_convert(NY)), split=str(split_ts.tz_convert(NY)),
                  chart_tf_min=CHART_TF),
        params=best.to_dict(),
        note=args.note,
        in_sample=s_is, out_of_sample=s_oos, funnel=funnel,
        daily_risk=dict(in_sample=daily_risk(is_tr, full.bars.day_key), out_of_sample=daily_risk(oos_tr, full.bars.day_key)),
        top10_is_sets_oos=top,
        coin_flip={k: v for k, v in cf.items() if not k.endswith("_dist")},
        prop_eval=dict(in_sample=ev_is, out_of_sample=ev_oos, out_of_sample_no_consistency=ev_oos_nocons),
        grid_size=len(combos), runtime_s=round(time.time() - t0, 1),
    )
    with open(os.path.join(ROOT, "reports", "results.json"), "w") as f:
        json.dump(meta, f, indent=2, default=float)
    with open(os.path.join(ROOT, "reports", "coinflip_dist.json"), "w") as f:
        json.dump(dict(strategy=cf.get("resim_expR"), a=cf.get("a_dist"), b=cf.get("b_dist")), f)

    gate = cf["verdict"] == "PASS"
    params_path = os.path.join(ROOT, "params", "params.json")
    prev_pass = False
    if os.path.exists(params_path):
        with open(params_path) as f:
            prev_pass = json.load(f).get("gate") == "PASS"
    # promote new params when they pass, or when the current ones never passed either
    if gate or args.force or not prev_pass:
        with open(params_path, "w") as f:
            json.dump(dict(version=stamp, gate=cf["verdict"], params=best.to_dict(),
                           oos=s_oos, p_direction=cf.get("p_direction"), p_timing=cf.get("p_timing")), f, indent=2)
        promoted = True
    else:
        promoted = False
        with open(params_path) as f:
            old = json.load(f)
        old["last_check"] = dict(version=stamp, gate=cf["verdict"])
        with open(params_path, "w") as f:
            json.dump(old, f, indent=2)
    pine = os.path.join(ROOT, "strategy", "understanding_price_strategy.pine")
    if os.path.exists(pine) and not args.no_pine:
        update_pine.write_block(pine, params_path)
        print(f"Pine AUTO-PARAMS block updated ({'new params' if promoted else 'gate status only'})")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
