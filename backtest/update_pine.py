"""Rewrite the AUTO-PARAMS block of the Pine strategy from params/params.json.

    python backtest/update_pine.py [pine_file] [params.json]

This is the "self-updating" step: the pipeline re-tunes on new data, runs the
coin-flip gate, and this script writes the winning parameters (plus the gate
result) into the Pine file. Paste the updated file into TradingView's Pine
Editor afterwards (TradingView has no API for pushing script code).
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import Params  # noqa: E402

START = "// ==== AUTO-PARAMS START"
END = "// ==== AUTO-PARAMS END ===="


def _hm(m):
    m = int(m)
    return f"{m // 60:02d}:{m % 60:02d}"


def _f(x):
    s = repr(float(x))
    return s


def render(meta: dict) -> str:
    p = {**Params().to_dict(), **meta["params"]}
    chk = meta.get("last_check")
    gate = meta.get("gate", "NOT RUN")
    if chk:  # newer check failed, params kept from the last passing run
        gate = f"{chk['gate']} on {chk['version'][:10]} (kept params from {meta['version'][:10]})"
    oos = meta.get("oos") or {}
    oos_txt = (f"OOS n={oos.get('n', 0)} net=${oos.get('net', 0):,.0f} PF={oos.get('pf', 0):.2f} "
               f"expR={oos.get('expR', 0):.2f} p_dir={meta.get('p_direction', 1):.3f} "
               f"p_time={meta.get('p_timing', 1):.3f}")
    entry = "Limit at zone" if p["entry_mode"] == "limit" else "Confirmation"
    target = "Swing high/low" if p["target_mode"] == "swing" else "Fixed R"
    lines = [
        f"{START} (written by backtest/update_pine.py - do not edit by hand) ====",
        f'const string AP_VERSION = "{meta["version"]}"',
        f'const string AP_GATE = "{gate}"',
        f'const string AP_OOS = "{oos_txt}"',
        f'const string AP_HTF = "{int(p["htf"])}"',
        f'const int AP_HTF_PIV = {int(p["htf_piv"])}',
        f'const int AP_PIV = {int(p["piv"])}',
        f'const int AP_INT_PIV = {int(p["int_piv"])}',
        f'const string AP_ENTRY = "{entry}"',
        f'const string AP_TARGET = "{target}"',
        f'const float AP_FIXED_RR = {_f(p["fixed_rr"])}',
        f'const float AP_MIN_RR = {_f(p["min_rr"])}',
        f'const bool AP_FVG = {"true" if p["require_fvg"] else "false"}',
        f'const float AP_MOM_K = {_f(p["mom_k"])}',
        f'const float AP_EQ_TOL = {_f(p["eq_tol_atr"])}',
        f'const float AP_EQ_RANGE = {_f(p["eq_range_atr"])}',
        f'const float AP_BE_R = {_f(p["be_r"])}',
        f'const int AP_BUF = {int(p["stop_buf_ticks"])}',
        f'const int AP_ZONE_AGE = {int(p["zone_max_age"])}',
        f'const int AP_CONF_BARS = {int(p["conf_max_bars"])}',
        f'const float AP_PB_SPEED = {_f(p["pb_speed_max"])}',
        f'const float AP_PB_ER = {_f(p["pb_er_max"])}',
        f'const bool AP_HTF_CLEAN = {"true" if p["htf_clean"] else "false"}',
        f'const float AP_PD = {_f(p["pd_level"])}',
        f'const float AP_APPR_K = {_f(p["appr_k"])}',
        f'const int AP_APPR_N = {int(p["appr_n"])}',
        f'const float AP_APPR_ATR = {_f(p["appr_atr"])}',
        f'const int AP_CONSEC = {int(p["max_consec_losses"])}',
        f'const float AP_ROOM = {_f(p["room_frac"])}',
        f'const bool AP_USE_HTF2 = {"true" if int(p["htf2"]) > 0 else "false"}',
        f'const string AP_HTF2 = "{int(p["htf2"]) if int(p["htf2"]) > 0 else 240}"',
        f'const string AP_ENTRY_START = "{_hm(p["entry_start"])}"',
        f'const string AP_ENTRY_END = "{_hm(p["entry_end"])}"',
        f'const bool AP_SWEEP = {"true" if p["sweep_entries"] else "false"}',
        END,
    ]
    return "\n".join(lines)


def write_block(pine_path: str, params_path: str):
    with open(params_path) as f:
        meta = json.load(f)
    src = open(pine_path).read()
    pat = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pat.search(src):
        raise SystemExit("AUTO-PARAMS markers not found in " + pine_path)
    new = pat.sub(lambda _: render(meta), src, count=1)
    with open(pine_path, "w") as f:
        f.write(new)


if __name__ == "__main__":
    pine = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "strategy", "understanding_price_strategy.pine")
    prm = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "params", "params.json")
    write_block(pine, prm)
    print("updated", pine)
