"""Final exam on untouched data (see backtest/HOLDOUT_PROTOCOL.md).

    python backtest/final_exam.py --params reports/candidate.json --attempt 1

Runs the locked parameters on NQ Apr-Aug 2022 (MNQ sizing) and MES 2020 (MES sizing),
pools the trades and applies the coin-flip rule at alpha = 0.05 / 3 (max 3 attempts).
Each attempt is appended to reports/final_exam_log.json, pass or fail.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from engine import Params, Cache, load_1m, resample, make_bars, run, stats, daily_risk  # noqa: E402
from coinflip import sim_trade  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_ATTEMPTS = 3
ALPHA = 0.05 / MAX_ATTEMPTS
EXAMS = [("NQ Apr-Aug 2022", "holdout_nq2022.parquet", 2.0),
         ("MES 2020", "holdout_mes2020.parquet", 5.0)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--attempt", type=int, required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--exam3", action="store_true", help="attempt 3: fresh NQ Oct 2022 - Mar 2023 only (see protocol)")
    a = ap.parse_args()
    exams = [("NQ Oct 2022 - Mar 2023", "exam3_nq_2022q4_2023q1.parquet", 2.0)] if a.exam3 else EXAMS
    if a.attempt > MAX_ATTEMPTS:
        raise SystemExit("protocol allows at most 3 attempts")
    log_path = os.path.join(ROOT, "reports", "final_exam_log.json")
    if os.path.exists(log_path) and any(x["attempt"] == a.attempt for x in json.load(open(log_path))):
        raise SystemExit(f"attempt {a.attempt} already used")
    base = Params(**{k: v for k, v in json.load(open(a.params)).items() if k in Params().to_dict()})
    rng = np.random.default_rng(2026)

    per, same, flip, risk_all, all_tr = [], [], [], [], []
    b_inputs = []
    for name, f, pv in exams:
        df = resample(load_1m(os.path.join(ROOT, "data", f)), 5)
        cache = Cache(make_bars(df, 5))
        p = replace(base, point_value=pv)
        tr = [t for t in run(cache, p) if t["why"] != "end"]
        s = stats(tr, name)
        s["daily"] = daily_risk(tr, cache.bars.day_key)
        per.append(s)
        print(name, s, flush=True)
        if not tr:
            continue
        sd = np.array([abs(t["entry"] - t["stop0"]) for t in tr])
        td = np.array([abs(t["target"] - t["entry"]) for t in tr])
        q = np.array([t["qty"] for t in tr])
        rk = sd * q * pv
        same.append(np.array([sim_trade(cache.bars, p, t["entry_bar"], t["dir"], sd[k], td[k], q[k], t["fill_seg"], t["fill_x"], t["entry"])
                              for k, t in enumerate(tr)]) / rk)
        flip.append(np.array([sim_trade(cache.bars, p, t["entry_bar"], -t["dir"], sd[k], td[k], q[k], t["fill_seg"], t["fill_x"], t["entry"])
                              for k, t in enumerate(tr)]) / rk)
        ok = np.nonzero((cache.bars.close_min[:-1] >= p.entry_start) & (cache.bars.close_min[:-1] < p.entry_end))[0] + 1
        b_inputs.append((cache.bars, p, sd, td, q, rk, ok, len(tr)))
        all_tr += tr

    n = len(all_tr)
    res = dict(attempt=a.attempt, label=a.label, exams=[e[0] for e in exams], alpha=ALPHA, params=base.to_dict(), per_dataset=per, n=n)
    if n == 0:
        res.update(verdict="FAIL", reason="no trades")
    else:
        same_c, flip_c = np.concatenate(same), np.concatenate(flip)
        strat = same_c.mean()
        A = np.where(rng.random((10000, n)) < 0.5, same_c, flip_c).mean(axis=1)
        B = np.empty(2000)
        for r in range(2000):
            vals = []
            for bars, p, sd, td, q, rk, ok, m in b_inputs:
                pick = rng.integers(0, m, m)
                ib = rng.choice(ok, m)
                dd = np.where(rng.random(m) < 0.5, 1, -1)
                vals += [sim_trade(bars, p, ib[k], dd[k], sd[pick[k]], td[pick[k]], q[pick[k]]) / rk[pick[k]] for k in range(m)]
            B[r] = np.mean(vals)
        p_a, p_b = float((A >= strat).mean()), float((B >= strat).mean())
        net = float(sum(t["pnl"] for t in all_tr))
        checks = {"trades >= 30": n >= 30, "pooled net > 0": net > 0,
                  f"p_direction < {ALPHA:.4f}": p_a < ALPHA, f"p_timing < {ALPHA:.4f}": p_b < ALPHA}
        res.update(verdict="PASS" if all(checks.values()) else "FAIL", checks=checks, net=net,
                   expR=float(np.mean([t["R"] for t in all_tr])), resim_expR=float(strat),
                   win=float(np.mean([t["pnl"] > 0 for t in all_tr])),
                   p_direction=p_a, p_timing=p_b,
                   a_p95=float(np.quantile(A, 0.95)), b_p95=float(np.quantile(B, 0.95)))
    print(json.dumps({k: v for k, v in res.items() if k not in ("params", "per_dataset")}, indent=1))
    log = json.load(open(log_path)) if os.path.exists(log_path) else []
    log.append(res)
    json.dump(log, open(log_path, "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
