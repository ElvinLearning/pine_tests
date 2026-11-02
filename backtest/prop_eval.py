"""Prop-firm evaluation simulator (Topstep 50K Combine style by default).

Starts a fresh eval on every trading day of a period and replays the
strategy's trades from that day on:
  PASS  balance reaches start + profit_target (and, if enabled, the best day
        is < consistency_pct of total profit at that moment)
  FAIL  balance touches the trailing max loss limit. The limit trails the
        highest end-of-day balance by max_loss and stops at the start balance.
The daily loss limit is enforced inside the strategy itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class EvalRules:
    start: float = 50_000.0
    profit_target: float = 3_000.0
    max_loss: float = 2_000.0
    consistency_pct: float = 0.50   # best day must be < 50% of profit (0 = off)


def simulate(trades: list, day_of_bar: np.ndarray, days: np.ndarray, rules: EvalRules = EvalRules()):
    by_day: dict[int, list[float]] = {}
    for t in trades:
        by_day.setdefault(int(day_of_bar[t["exit_bar"]]), []).append(t["pnl"])
    days = np.array(sorted(set(int(d) for d in days)))
    out = []
    for s in range(len(days)):
        bal, peak_eod, best_day = rules.start, rules.start, 0.0
        result, n_days = "UNRESOLVED", 0
        for d in days[s:]:
            n_days += 1
            mll = min(peak_eod - rules.max_loss, rules.start)
            day_pnl = 0.0
            for pnl in by_day.get(int(d), []):
                bal += pnl
                day_pnl += pnl
                if bal <= mll:
                    result = "FAIL"
                    break
            if result == "FAIL":
                break
            best_day = max(best_day, day_pnl)
            peak_eod = max(peak_eod, bal)
            profit = bal - rules.start
            if profit >= rules.profit_target:
                if rules.consistency_pct <= 0 or best_day < rules.consistency_pct * profit:
                    result = "PASS"
                    break
        out.append((result, n_days))
    res = np.array([r for r, _ in out])
    resolved = res != "UNRESOLVED"
    pass_days = [n for r, n in out if r == "PASS"]
    return dict(
        evals=len(out),
        passed=int((res == "PASS").sum()),
        failed=int((res == "FAIL").sum()),
        unresolved=int((~resolved).sum()),
        pass_rate_resolved=float((res == "PASS").sum() / resolved.sum()) if resolved.any() else 0.0,
        median_days_to_pass=float(np.median(pass_days)) if pass_days else None,
        rules=rules.__dict__,
    )
