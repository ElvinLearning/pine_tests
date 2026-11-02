"""Charts for reports/REPORT.md: equity curves and coin-flip distributions."""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REP = os.path.join(ROOT, "reports")
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S1, S2 = "#2a78d6", "#eb6834"   # validated categorical slots 1-2 (dataviz reference palette)


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def equity():
    r = json.load(open(os.path.join(REP, "results.json")))
    split = pd.Timestamp(r["data"]["split"])
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    _style(ax)
    for f, col, name in [("trades_run1_mechanical.csv", S1, "Mechanical rules only"),
                         ("trades.csv", S2, "With context layer (G1-G4)")]:
        t = pd.read_csv(os.path.join(REP, f))
        t["exit_time"] = pd.to_datetime(t.exit_time, utc=True)
        t = t.sort_values("exit_time")
        x = np.r_[np.datetime64(t.exit_time.iloc[0].tz_localize(None)), t.exit_time.dt.tz_localize(None).values]
        y = np.r_[0, t.pnl.cumsum().values]
        ax.step(x, y, where="post", color=col, linewidth=2)
        ax.annotate(f"{name}  ${y[-1]:,.0f}", (x[-1], y[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color=INK)
    sx = np.datetime64(split.tz_convert("UTC").tz_localize(None))
    ax.axvline(sx, color=INK2, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(sx, ax.get_ylim()[1], "  blind second half ->", color=INK2, fontsize=9, va="top")
    ax.text(sx, ax.get_ylim()[1], "<- tuned first half  ", color=INK2, fontsize=9, va="top", ha="right")
    ax.axhline(0, color=INK2, linewidth=0.8)
    ax.set_ylabel("Cumulative P&L in USD (MNQ, 300 USD risk per trade)", color=INK2, fontsize=9)
    ax.set_title("Equity: NQ futures 5-minute, Jan 2025 to Apr 2026", color=INK, fontsize=11, loc="left")
    fig.subplots_adjust(right=0.72)
    fig.savefig(os.path.join(REP, "equity.png"), dpi=150, facecolor=SURFACE)
    plt.close(fig)


def coinflip():
    d = json.load(open(os.path.join(REP, "coinflip_dist.json")))
    r = json.load(open(os.path.join(REP, "results.json")))["coin_flip"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), facecolor=SURFACE, sharey=False)
    for ax, key, title, pv in [(axes[0], "a", "A. Same entries, coin-flip direction", r["p_direction"]),
                               (axes[1], "b", "B. Random entry times, coin-flip direction", r["p_timing"])]:
        _style(ax)
        v = np.array(d[key])
        ax.hist(v, bins=40, color=S1, edgecolor=SURFACE, linewidth=1)
        ax.axvline(d["strategy"], color=S2, linewidth=2)
        ax.axvline(np.quantile(v, 0.95), color=INK2, linewidth=1, linestyle=(0, (4, 3)))
        ax.annotate(f"bot {d['strategy']:+.2f}R\np = {pv:.2f}", (d["strategy"], ax.get_ylim()[1] * 0.95),
                    xytext=(5, 0), textcoords="offset points", va="top", fontsize=9, color=INK)
        ax.annotate("95th pct", (np.quantile(v, 0.95), ax.get_ylim()[1] * 0.6), xytext=(5, 0),
                    textcoords="offset points", fontsize=8, color=INK2)
        ax.set_title(title, color=INK, fontsize=10, loc="left")
        ax.set_xlabel("Mean R per trade, second half", color=INK2, fontsize=9)
    axes[0].set_ylabel("Random runs", color=INK2, fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(REP, "coinflip.png"), dpi=150, facecolor=SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    equity()
    coinflip()
    print("wrote reports/equity.png, reports/coinflip.png")
