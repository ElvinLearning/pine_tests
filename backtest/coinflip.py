"""Coin-flip rule on the out-of-sample half.

A strategy passes only if its entries beat random coin flips that use the
exact same risk management (stop distance, target distance, size, break-even
rule, end-of-day flat, costs). Two baselines:

  A. Same entry bars as the strategy, direction decided by a coin flip.
     Tests whether the direction call has an edge.
  B. Random entry bars inside the same entry window, coin-flip direction,
     stop/target/size drawn from the strategy's own trades.
     Tests whether picking *when* to trade has an edge.

Baseline A re-simulates both directions from the strategy's exact fill
(same bar, same price, same point of the intrabar path), so the only thing
that differs is the direction. Baseline B uses market entries at bar opens.

PASS (decided before the OOS run, see RULES below) =
    OOS trades >= 30
    AND OOS net profit > 0 after costs
    AND strategy beats >= 95% of baseline A runs (p < 0.05)
    AND strategy beats >= 95% of baseline B runs (p < 0.05)
"""
from __future__ import annotations

import numpy as np
from engine import Bars, Params, TICK, _path

RULES = dict(min_trades=30, alpha=0.05, runs_a=10000, runs_b=2000)


def sim_trade(b: Bars, p: Params, i0: int, d: int, stop_dist: float, tgt_dist: float, qty: int,
              seg: int = -1, x0: float | None = None, E: float | None = None):
    """Open a trade on bar i0 and manage it with the strategy's exit rules.
    seg=-1: market entry at the open (+slippage). seg>=0: entry at price E at
    the point x0 of intrabar path segment seg (the strategy's own fill point).
    Returns net pnl in $."""
    o, h, l, c = b.o, b.h, b.l, b.c
    slip, ft = p.slip_ticks * TICK, p.fill_ticks * TICK
    if E is None:
        E = o[i0] + d * slip
    S = E - d * stop_dist
    T = E + d * tgt_dist
    be = False
    pend_exit = False
    N = len(c)
    exit_px = None
    i = i0
    while i < N:
        if i > i0:
            oi = o[i]
            if pend_exit:
                exit_px = oi - d * slip
                break
            if (d == 1 and oi <= S) or (d == -1 and oi >= S):
                exit_px = oi - d * slip
                break
            if (d == 1 and oi >= T + ft) or (d == -1 and oi <= T - ft):
                exit_px = oi
                break
        pts = _path(o[i], h[i], l[i], c[i])
        x = pts[0]
        segs = list(enumerate(pts[1:]))
        if i == i0 and seg >= 0:
            segs, x = segs[seg:], x0
        for _, y in segs:
            if d == 1:
                if y < x and y <= S < x:
                    exit_px = S - slip
                elif y > x and x < T + ft <= y:
                    exit_px = T
            else:
                if y > x and x < S <= y:
                    exit_px = S + slip
                elif y < x and y <= T - ft < x:
                    exit_px = T
            if exit_px is not None:
                break
            x = y
        if exit_px is not None:
            break
        # close-of-bar management
        if p.be_r > 0 and not be:
            fav = (h[i] - E) if d == 1 else (E - l[i])
            if fav >= p.be_r * stop_dist:
                S, be = E + d * TICK, True
        pend_exit = p.flat_time <= b.close_min[i] <= 17 * 60
        i += 1
    if exit_px is None:
        exit_px = c[N - 1]
    return d * (exit_px - E) * qty * p.point_value - 2 * p.comm_per_side * qty


def coin_flip_test(b: Bars, p: Params, trades: list, lo: int, hi: int, seed: int = 7):
    """trades: strategy trades inside [lo, hi). Returns a result dict."""
    rng = np.random.default_rng(seed)
    n = len(trades)
    res = dict(rules=RULES, n=n)
    if n == 0:
        res.update(verdict="FAIL", reason="no trades")
        return res
    sd = np.array([abs(t["entry"] - t["stop0"]) for t in trades])
    td = np.array([abs(t["target"] - t["entry"]) for t in trades])
    q = np.array([t["qty"] for t in trades])
    risk = sd * q * p.point_value
    bars = np.array([t["entry_bar"] for t in trades])
    dirs = np.array([t["dir"] for t in trades])

    # resimulate both directions from the strategy's exact fill (same bar, price, moment)
    segs = [t.get("fill_seg", -1) for t in trades]
    xs = [t.get("fill_x", None) for t in trades]
    ents = [t["entry"] for t in trades]
    pnl_same = np.array([sim_trade(b, p, bars[k], dirs[k], sd[k], td[k], q[k], segs[k], xs[k], ents[k]) for k in range(n)])
    pnl_flip = np.array([sim_trade(b, p, bars[k], -dirs[k], sd[k], td[k], q[k], segs[k], xs[k], ents[k]) for k in range(n)])
    r_same, r_flip = pnl_same / risk, pnl_flip / risk
    strat_R = r_same.mean()
    actual_R = np.mean([t["R"] for t in trades])

    # A: coin flip per trade
    coins = rng.random((RULES["runs_a"], n)) < 0.5
    a_means = np.where(coins, r_same, r_flip).mean(axis=1)
    p_a = float((a_means >= strat_R).mean())

    # B: random entry bars in the entry window, random direction
    ok = np.nonzero((b.close_min[lo:hi - 1] >= p.entry_start) & (b.close_min[lo:hi - 1] < p.entry_end))[0] + lo + 1
    b_means = np.empty(RULES["runs_b"])
    for r in range(RULES["runs_b"]):
        pick = rng.integers(0, n, n)
        ib = rng.choice(ok, n)
        dd = np.where(rng.random(n) < 0.5, 1, -1)
        pr = [sim_trade(b, p, ib[k], dd[k], sd[pick[k]], td[pick[k]], q[pick[k]]) / risk[pick[k]] for k in range(n)]
        b_means[r] = np.mean(pr)
    p_b = float((b_means >= strat_R).mean())

    net = float(sum(t["pnl"] for t in trades))
    checks = {
        f"trades >= {RULES['min_trades']}": n >= RULES["min_trades"],
        "OOS net profit > 0": net > 0,
        f"beats coin-flip direction (p < {RULES['alpha']})": p_a < RULES["alpha"],
        f"beats coin-flip timing (p < {RULES['alpha']})": p_b < RULES["alpha"],
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    if n < RULES["min_trades"] and verdict == "FAIL":
        verdict = "FAIL (too few trades to prove an edge)"
    res.update(
        verdict=verdict, checks=checks, net=net,
        actual_expR=float(actual_R), resim_expR=float(strat_R),
        p_direction=p_a, p_timing=p_b,
        a_pctl=dict(p50=float(np.median(a_means)), p95=float(np.quantile(a_means, 0.95))),
        b_pctl=dict(p50=float(np.median(b_means)), p95=float(np.quantile(b_means, 0.95))),
        a_dist=a_means[:2000].tolist(), b_dist=b_means.tolist(),
        win_rate=float(np.mean([t["pnl"] > 0 for t in trades])),
    )
    return res
