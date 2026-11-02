"""Exam entry gate for full-development candidates: coin-flip p-values on all 2025-26 data.

    python backtest/dev_gate.py [--k 5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from engine import Params, Cache, load_1m, resample, make_bars, run, stats  # noqa: E402
from dev_search import quick_coinflip, DATA, ROOT  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--k", type=int, default=5)
a = ap.parse_args()
cands = json.load(open(os.path.join(ROOT, "reports", "dev_search_full_params.json")))
df = resample(load_1m(DATA), 5)
cache = Cache(make_bars(df, 5))
out = []
for key, pd_ in list(cands.items())[:a.k]:
    p = Params(**pd_)
    tr = [t for t in run(cache, p) if t["why"] != "end"]
    pa, pb = quick_coinflip(cache.bars, p, tr, 0, len(df), runs_b=600)
    s = stats(tr)
    row = dict(combo=key, n=s["n"], net=round(s["net"]), expR=round(s["expR"], 3), p_dir=pa, p_time=pb,
               gate="ENTER EXAM" if max(pa, pb) < 0.10 else "skip")
    out.append(row)
    print(row, flush=True)
json.dump(out, open(os.path.join(ROOT, "reports", "dev_gate.json"), "w"), indent=1)
