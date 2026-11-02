"""Bar-by-bar mirror of strategy/understanding_price_strategy.pine.

Every rule here exists 1:1 in the Pine file, in the same order, so the Python
backtest and TradingView's Strategy Tester follow the same logic. Fill rules
copy TradingView's broker emulator: orders decided at a bar's close are live
from the next bar; intrabar path is open->high->low->close when the high is
closer to the open, else open->low->high->close; limit orders need price to
trade 1 tick through (backtest_fill_limits_assumption=1); market and stop
fills pay 1 tick of slippage.

Two layers:
  * Mechanical rules from the course (structure, zones, imbalance, one tap,
    golden rule, R:R, break-even).
  * Context layer: the "discretionary" reads turned into measurable rules
      G1 pullback quality  - slow & choppy correction vs aggressive move
      G2 HTF narrative     - HTF trend clean (higher low / lower high) and the
                             entry sits in HTF discount (longs) / premium (shorts)
      G3 pre-fill cancel   - pull the limit order when price is close and
                             closing in with extreme opposing momentum
      G4 prop drawdown     - size so a full stop cannot breach the remaining
                             daily loss room; stop after N straight losses
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

TICK = 0.25
NY = "America/New_York"


@dataclass
class Params:
    # top-down: higher-timeframe bias
    htf: int = 60              # minutes
    htf_piv: int = 2           # HTF swing pivot strength (bars each side)
    htf2: int = 0              # second, higher timeframe that must agree with the HTF bias (0 = off)
    # execution timeframe structure
    piv: int = 3               # swing pivot strength on the chart TF
    int_piv: int = 2           # internal pivots (confirmation + equal highs/lows)
    entry_mode: str = "limit"  # "limit" = limit at fresh zone, "confirm" = LTF break after tap
    target_mode: str = "swing" # "swing" = impulse high/low, "fixed" = fixed R multiple
    fixed_rr: float = 2.5
    min_rr: float = 2.5        # checklist rule 12: skip anything under this
    require_fvg: bool = True   # zone needs an open imbalance in its impulse leg
    mom_k: float = 2.0         # opposing candle body >= mom_k*ATR kills the zone (0 = off)
    eq_tol_atr: float = 0.10   # two pivots within this*ATR count as "equal"
    eq_range_atr: float = 3.0  # golden rule looks this many ATR past the entry
    be_r: float = 0.0          # stop to break-even after +be_r R (0 = off)
    stop_buf_ticks: int = 4
    zone_max_age: int = 96     # bars a zone stays valid
    conf_max_bars: int = 24    # bars to wait for confirmation after the tap
    max_leg_bars: int = 300
    # ---- context layer ----
    pb_speed_max: float = 99.0 # G1: pullback speed / impulse speed must be <= this (99 = off)
    pb_er_max: float = 1.0     # G1: pullback efficiency ratio <= this (1 = off; low = choppy)
    htf_clean: bool = False    # G2: HTF needs a higher low (longs) / lower high (shorts)
    pd_level: float = 1.0      # G2: entry must sit in the lower pd_level of the HTF range (1 = off)
    appr_k: float = 0.0        # G3: cancel if price within appr_atr ATR and falling >= appr_k ATR/bar (0 = off)
    appr_n: int = 3
    appr_atr: float = 1.0
    sweep_entries: bool = False # internal-liquidity sweep setup: wick through an internal low/high, close back, then LTF break
    max_consec_losses: int = 2 # G4: stop for the day after this many straight losses (0 = off)
    room_frac: float = 0.9     # G4: risk <= this share of the remaining daily loss room
    # risk / prop-firm eval
    risk_usd: float = 300.0
    point_value: float = 2.0   # MNQ = $2/pt (NQ = $20)
    max_qty: int = 50
    comm_per_side: float = 0.50
    slip_ticks: int = 1
    fill_ticks: int = 1
    entry_start: int = 9 * 60 + 35   # minutes after midnight, New York, bar close time
    entry_end: int = 15 * 60
    flat_time: int = 15 * 60 + 50
    max_trades_day: int = 3
    daily_loss: float = 1000.0
    daily_profit_cap: float = 0.0    # 0 = off
    guard_on: bool = True            # coin-flip guard: half size when last N trades < 0R
    guard_n: int = 20

    def to_dict(self):
        return asdict(self)


# ----------------------------------------------------------------------------
# data
# ----------------------------------------------------------------------------
def load_1m(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["open", "high", "low", "close"]].astype(float)


def resample(df1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    r = df1m.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    return r.dropna()


@dataclass
class Bars:
    df: pd.DataFrame
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    close_min: np.ndarray   # NY minutes-of-day of each bar's close time
    day_key: np.ndarray     # CME session day (18:00 NY boundary)
    et_naive: pd.DatetimeIndex
    tf: int


def make_bars(df: pd.DataFrame, tf: int) -> Bars:
    et = df.index.tz_convert(NY)
    close_et = (df.index + pd.Timedelta(minutes=tf)).tz_convert(NY)
    close_min = (close_et.hour * 60 + close_et.minute).to_numpy()
    day_key = ((et + pd.Timedelta(hours=6)).normalize().tz_localize(None)
               .to_numpy().astype("datetime64[D]").astype(np.int64))
    return Bars(df, df.open.to_numpy(), df.high.to_numpy(), df.low.to_numpy(),
                df.close.to_numpy(), close_min, day_key, et.tz_localize(None), tf)


# ----------------------------------------------------------------------------
# indicators (exact Pine equivalents)
# ----------------------------------------------------------------------------
def atr(h, l, c, n=14):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    out[n - 1] = tr[:n].mean()
    a = 1.0 / n
    for i in range(n, len(c)):
        out[i] = out[i - 1] + a * (tr[i] - out[i - 1])
    return out


def pivots(h, l, n):
    """Pivot confirmed at bar i for center c=i-n.
    high: strictly above the n older bars, >= the n newer bars.
    low:  strictly below the n older bars, <= the n newer bars."""
    H, L = pd.Series(h), pd.Series(l)
    lmax = H.rolling(n).max().shift(1).to_numpy()
    rmax = H.rolling(n).max().shift(-n).to_numpy()
    lmin = L.rolling(n).min().shift(1).to_numpy()
    rmin = L.rolling(n).min().shift(-n).to_numpy()
    with np.errstate(invalid="ignore"):
        is_ph = (h > lmax) & (h >= rmax)
        is_pl = (l < lmin) & (l <= rmin)
    N = len(h)
    ph = np.full(N, np.nan)
    pl = np.full(N, np.nan)
    idx = np.nonzero(is_ph)[0]
    idx = idx[idx + n < N]
    ph[idx + n] = h[idx]
    idx = np.nonzero(is_pl)[0]
    idx = idx[idx + n < N]
    pl[idx + n] = l[idx]
    return ph, pl


def htf_state(h, l, c, n):
    """HTF structure: trend, last swing low + highest high since it, last swing
    high + lowest low since it, and whether the last swing made a higher low /
    lower high. Mirrors f_htf() in the Pine file."""
    ph, pl = pivots(h, l, n)
    N = len(c)
    out = dict(trend=np.zeros(N), pl=np.full(N, np.nan), rh=np.full(N, np.nan),
               ph=np.full(N, np.nan), rl=np.full(N, np.nan),
               up_clean=np.zeros(N, bool), dn_clean=np.zeros(N, bool))
    lph = lpl = pph = ppl = rh = rl = np.nan
    phb = plb = True
    t = 0
    for i in range(N):
        if not np.isnan(ph[i]):
            pph, lph, phb = lph, ph[i], False
            rl = l[i - n:i + 1].min()
        else:
            rl = l[i] if np.isnan(rl) else min(rl, l[i])
        if not np.isnan(pl[i]):
            ppl, lpl, plb = lpl, pl[i], False
            rh = h[i - n:i + 1].max()
        else:
            rh = h[i] if np.isnan(rh) else max(rh, h[i])
        if not phb and c[i] > lph:
            t, phb = 1, True
        if not plb and c[i] < lpl:
            t, plb = -1, True
        out["trend"][i] = t
        out["pl"][i], out["rh"][i], out["ph"][i], out["rl"][i] = lpl, rh, lph, rl
        out["up_clean"][i] = (not np.isnan(ppl)) and lpl > ppl
        out["dn_clean"][i] = (not np.isnan(pph)) and lph < pph
    return out


def htf_for_chart(bars: Bars, htf: int, htf_piv: int) -> dict:
    """request.security(tf, f_htf() returning [x[1], ...], lookahead_on):
    values of the last *closed* HTF bar. HTF buckets are anchored to the
    18:00 New York session open (TradingView CME alignment)."""
    origin = pd.Timestamp("2000-01-02 18:00")
    bucket = ((bars.et_naive - origin) // pd.Timedelta(minutes=htf)).to_numpy()
    g = pd.DataFrame({"b": bucket, "o": bars.o, "h": bars.h, "l": bars.l, "c": bars.c})
    agg = g.groupby("b", sort=True).agg(o=("o", "first"), h=("h", "max"),
                                         l=("l", "min"), c=("c", "last"))
    st = htf_state(agg.h.to_numpy(), agg.l.to_numpy(), agg.c.to_numpy(), htf_piv)
    pos = np.searchsorted(agg.index.to_numpy(), bucket)
    res = {}
    for k, v in st.items():
        fill = False if v.dtype == bool else (0 if k == "trend" else np.nan)
        prev = np.r_[np.array([fill], dtype=v.dtype), v[:-1]]
        res[k] = prev[pos]
    res["trend"] = res["trend"].astype(np.int8)
    return res


# ----------------------------------------------------------------------------
# engine
# ----------------------------------------------------------------------------
class Cache:
    """Per-dataset cache of indicator arrays shared across parameter sets."""

    def __init__(self, bars: Bars):
        self.bars = bars
        self._piv, self._htf = {}, {}
        self.atr = atr(bars.h, bars.l, bars.c, 14)

    def piv(self, n):
        if n not in self._piv:
            self._piv[n] = pivots(self.bars.h, self.bars.l, n)
        return self._piv[n]

    def htf(self, tf, n):
        k = (tf, n)
        if k not in self._htf:
            self._htf[k] = htf_for_chart(self.bars, tf, n)
        return self._htf[k]


def _path(o, h, l, c):
    return (o, h, l, c) if (h - o) <= (o - l) else (o, l, h, c)


def run(cache: Cache, p: Params, start: int = 0, end: int | None = None, funnel: dict | None = None):
    F = funnel if funnel is not None else {}

    def bump(k):
        F[k] = F.get(k, 0) + 1

    b = cache.bars
    o, h, l, c = b.o, b.h, b.l, b.c
    N = len(c) if end is None else end
    A = cache.atr
    ph_s, pl_s = cache.piv(p.piv)
    ph_i, pl_i = cache.piv(p.int_piv)
    H = cache.htf(p.htf, p.htf_piv)
    htf = H["trend"]
    if p.htf2 > 0:                       # top-down agreement: both timeframes must point the same way
        t2 = cache.htf(p.htf2, p.htf_piv)["trend"]
        htf = np.where(t2 == htf, htf, 0).astype(np.int8)
    pv, ft, slip = p.point_value, p.fill_ticks * TICK, p.slip_ticks * TICK
    buf = p.stop_buf_ticks * TICK

    # chart structure state
    lph = lpl = np.nan
    lph_bar = lpl_bar = -1
    phb = plb = True
    int_ph = int_pl = np.nan
    eq_lows: list[float] = []
    eq_highs: list[float] = []

    zone = None
    pos = 0
    qty = 0
    entry = stop = target = stop0 = 0.0
    be_done = False
    trade_risk = 0.0
    entry_bar = -1
    fill_seg, fill_x = -1, 0.0
    pend_limit = None     # (dir, price, qty, stop, target, risk)
    pend_mkt = None       # (dir, qty, stop, target, risk)
    pend_exit = False

    realized = 0.0
    trades = []
    seen = 0
    rlist: list[float] = []
    consec = 0
    cur_day = None
    day_start_net = 0.0
    trades_at_day_start = 0
    entries_total = 0
    day_halted = False

    def close_trade(px, i, why):
        nonlocal pos, realized
        pnl = pos * (px - entry) * qty * pv - 2 * p.comm_per_side * qty
        realized += pnl
        trades.append(dict(entry_bar=entry_bar, exit_bar=i, dir=pos, entry=entry,
                           exit=px, qty=qty, stop0=stop0, target=target,
                           risk_usd=trade_risk, pnl=pnl, R=pnl / trade_risk, why=why,
                           fill_seg=fill_seg, fill_x=fill_x))
        pos = 0

    def open_trade(d, px, q, s, t, risk, i, seg, x):
        nonlocal pos, qty, entry, stop, target, stop0, be_done, trade_risk, entry_bar, entries_total, fill_seg, fill_x
        pos, qty, entry, stop, target, stop0 = d, q, px, s, t, s
        be_done, trade_risk, entry_bar = False, risk, i
        fill_seg, fill_x = seg, x
        entries_total += 1

    for i in range(start, N):
        # ================= 1. broker emulator fills on bar i =================
        if i > start:
            oi, hi_, li, ci = o[i], h[i], l[i], c[i]
            if pend_exit and pos != 0:
                close_trade(oi - pos * slip, i, "flat")
            pend_exit = False
            if pend_mkt is not None and pos == 0:
                d, q, s, t, rk = pend_mkt
                open_trade(d, oi + d * slip, q, s, t, rk, i, -1, oi)
            pend_mkt = None
            lim = pend_limit
            pend_limit = None
            if pos == 1:
                if oi <= stop:
                    close_trade(oi - slip, i, "stop")
                elif oi >= target + ft:
                    close_trade(oi, i, "target")
            elif pos == -1:
                if oi >= stop:
                    close_trade(oi + slip, i, "stop")
                elif oi <= target - ft:
                    close_trade(oi, i, "target")
            if pos == 0 and lim is not None:
                d, E, q, s, t, rk = lim
                if (d == 1 and oi <= E - ft) or (d == -1 and oi >= E + ft):
                    open_trade(d, oi, q, s, t, rk, i, -1, oi)
                    lim = None
            pts = _path(oi, hi_, li, ci)
            x = pts[0]
            for sg, y in enumerate(pts[1:]):
                while True:
                    ev = None
                    if pos == 0 and lim is not None:
                        d, E = lim[0], lim[1]
                        lv = E - ft if d == 1 else E + ft
                        if (d == 1 and y < x and y <= lv < x) or (d == -1 and y > x and x < lv <= y):
                            ev = ("entry", lv)
                    elif pos == 1:
                        if y < x and y <= stop < x:
                            ev = ("stop", stop)
                        elif y > x and x < target + ft <= y:
                            ev = ("target", target + ft)
                    elif pos == -1:
                        if y > x and x < stop <= y:
                            ev = ("stop", stop)
                        elif y < x and y <= target - ft < x:
                            ev = ("target", target - ft)
                    if ev is None:
                        break
                    kind, lv = ev
                    if kind == "entry":
                        d, E, q, s, t, rk = lim
                        open_trade(d, E, q, s, t, rk, i, sg, lv)
                        lim = None
                    elif kind == "stop":
                        close_trade(stop - pos * slip, i, "be" if be_done else "stop")
                    else:
                        close_trade(target, i, "target")
                    x = lv
                x = y

        # ================= 2. script logic at the close of bar i =============
        oi, hi_, li, ci, atr_i = o[i], h[i], l[i], c[i], A[i]
        if b.day_key[i] != cur_day:
            cur_day = b.day_key[i]
            day_start_net = realized
            trades_at_day_start = entries_total
            day_halted = False
            consec = 0
        while seen < len(trades):          # newly closed trades
            r = trades[seen]["R"]
            rlist.append(r)
            consec = consec + 1 if trades[seen]["pnl"] < 0 else 0
            seen += 1
        open_pnl = pos * (ci - entry) * qty * pv if pos != 0 else 0.0
        day_pnl = realized - day_start_net + open_pnl
        if day_pnl <= -p.daily_loss or (p.daily_profit_cap > 0 and day_pnl >= p.daily_profit_cap):
            day_halted = True
        if p.max_consec_losses > 0 and consec >= p.max_consec_losses:
            day_halted = True
        guard_hit = (p.guard_on and len(rlist) >= p.guard_n
                     and sum(rlist[-p.guard_n:]) < 0)
        risk_eff = p.risk_usd * (0.5 if guard_hit else 1.0)
        risk_eff = min(risk_eff, (p.daily_loss + min(day_pnl, 0.0)) * p.room_frac)   # G4

        # pivots / structure
        if not np.isnan(ph_s[i]):
            lph, lph_bar, phb = ph_s[i], i - p.piv, False
        if not np.isnan(pl_s[i]):
            lpl, lpl_bar, plb = pl_s[i], i - p.piv, False
        swept_lo = [v for v in eq_lows if li < v]
        swept_hi = [v for v in eq_highs if hi_ > v]
        eq_lows = [v for v in eq_lows if not li < v]
        eq_highs = [v for v in eq_highs if not hi_ > v]
        if not np.isnan(ph_i[i]):
            int_ph = ph_i[i]
            eq_highs.append(ph_i[i])
            eq_highs = eq_highs[-10:]
        if not np.isnan(pl_i[i]):
            int_pl = pl_i[i]
            eq_lows.append(pl_i[i])
            eq_lows = eq_lows[-10:]
        ht = htf[i]

        # ---- zone maintenance (zones created on earlier bars) ----
        signal = None
        if zone is not None:
            z = zone
            d = z["dir"]
            kill = False
            if ht != d:
                kill = True
                bump("kill_htf_flip")
            elif i - z["bar"] > p.zone_max_age:
                kill = True
                bump("kill_age")
            elif not z["armed"]:
                body_against = (oi - ci) if d == 1 else (ci - oi)
                if p.entry_mode == "limit":
                    tapped = (li <= z["top"] - ft) if d == 1 else (hi_ >= z["bot"] + ft)
                else:
                    tapped = (li <= z["top"]) if d == 1 else (hi_ >= z["bot"])
                if p.mom_k > 0 and body_against >= p.mom_k * atr_i:
                    kill = True
                    bump("kill_momentum_candle")
                elif tapped:
                    bump("tap")
                    if p.entry_mode == "limit":
                        kill = True
                    else:
                        z["pb_x"] = min(z["pb_x"], li) if d == 1 else max(z["pb_x"], hi_)
                        z["pb_ok"] = _pb_ok(z, i, c, p)   # G1 measured on the way into the zone
                        z["armed"], z["tap_bar"] = True, i
                        z["tap_x"] = li if d == 1 else hi_
                else:
                    if (d == 1 and hi_ > z["leg_x"]) or (d == -1 and li < z["leg_x"]):
                        z["leg_x"], z["leg_bar"], z["pb_x"] = (hi_ if d == 1 else li), i, (hi_ if d == 1 else li)
                    else:
                        z["pb_x"] = min(z["pb_x"], li) if d == 1 else max(z["pb_x"], hi_)
            if not kill and z["armed"]:
                if d == 1:
                    z["tap_x"] = min(z["tap_x"], li)
                    if ci < z["bot"]:
                        kill = True
                        bump("kill_zone_failed")
                    elif not np.isnan(int_ph) and ci > int_ph:
                        signal = z
                        kill = True
                else:
                    z["tap_x"] = max(z["tap_x"], hi_)
                    if ci > z["top"]:
                        kill = True
                        bump("kill_zone_failed")
                    elif not np.isnan(int_pl) and ci < int_pl:
                        signal = z
                        kill = True
                if not kill and i - z["tap_bar"] >= p.conf_max_bars:
                    kill = True
                    bump("kill_conf_timeout")
            if kill:
                zone = None

        # ---- break of structure -> new zone ----
        if not phb and ci > lph:
            phb = True
            if ht == 1 and 0 < i - lph_bar <= p.max_leg_bars:
                nz = _make_zone(1, i, lph_bar, o, h, l, c, p)
                bump("zone_long" if nz else "bos_long_no_imbalance")
                zone = nz or zone
        if not plb and ci < lpl:
            plb = True
            if ht == -1 and 0 < i - lpl_bar <= p.max_leg_bars:
                nz = _make_zone(-1, i, lpl_bar, o, h, l, c, p)
                bump("zone_short" if nz else "bos_short_no_imbalance")
                zone = nz or zone

        # ---- internal liquidity sweep -> armed pseudo-zone (needs LTF confirmation) ----
        if p.sweep_entries and zone is None and signal is None:
            if ht == 1 and swept_lo and ci > max(swept_lo):
                zone = dict(dir=1, top=max(swept_lo), bot=li, leg_x=(lph if lph > ci else np.nan), leg_bar=i, pb_x=li, ext_bar=i,
                            bar=i, armed=True, tap_bar=i, tap_x=li, pb_ok=True, sweep=True)
                bump("sweep_long")
            elif ht == -1 and swept_hi and ci < min(swept_hi):
                zone = dict(dir=-1, top=hi_, bot=min(swept_hi), leg_x=(lpl if lpl < ci else np.nan), leg_bar=i, pb_x=hi_, ext_bar=i,
                            bar=i, armed=True, tap_bar=i, tap_x=hi_, pb_ok=True, sweep=True)
                bump("sweep_short")

        # ---- position management ----
        if pos != 0:
            risk_pts = abs(entry - stop0)
            if p.be_r > 0 and not be_done:
                fav = (hi_ - entry) if pos == 1 else (entry - li)
                if fav >= p.be_r * risk_pts:
                    stop = entry + pos * TICK
                    be_done = True
            eod = p.flat_time <= b.close_min[i] <= 17 * 60
            if eod or day_halted:
                pend_exit = True

        # ---- orders for the next bar ----
        in_window = p.entry_start <= b.close_min[i] < p.entry_end
        can_trade = (pos == 0 and not pend_exit and in_window and not day_halted
                     and entries_total - trades_at_day_start < p.max_trades_day)
        if signal is not None:
            bump("confirm_signal")
            if not can_trade:
                bump("blocked_session_or_daily_limits")
        if signal is not None and can_trade:
            d = signal["dir"]
            E = ci
            S = (min(signal["bot"], signal["tap_x"]) - buf) if d == 1 else (max(signal["top"], signal["tap_x"]) + buf)
            if not signal["pb_ok"]:
                bump("ctx_pullback_too_aggressive")
            elif _htf_ok(d, E, H, i, p, bump):
                order = _plan(d, E, S, signal["leg_x"], p, atr_i, eq_lows, eq_highs, risk_eff, bump)
                if order is not None:
                    bump("order_sent")
                    q, T, rk = order
                    pend_mkt = (d, q, S, T, rk)
        elif zone is not None and p.entry_mode == "limit" and not zone["armed"] and can_trade:
            d = zone["dir"]
            E = zone["top"] if d == 1 else zone["bot"]
            S = (zone["bot"] - buf) if d == 1 else (zone["top"] + buf)
            if not _pb_ok(zone, i, c, p):
                bump("ctx_pullback_too_aggressive")
            elif not _approach_ok(d, E, i, c, atr_i, p):
                bump("ctx_prefill_cancel_momentum")
            elif _htf_ok(d, E, H, i, p, bump):
                order = _plan(d, E, S, zone["leg_x"], p, atr_i, eq_lows, eq_highs, risk_eff, bump)
                if order is not None:
                    q, T, rk = order
                    pend_limit = (d, E, q, S, T, rk)

    if pos != 0:  # close any open trade at the last close (report only)
        close_trade(c[N - 1], N - 1, "end")
    return trades


# ---------------------------- context rules ---------------------------------
def _pb_ok(z, i, c, p: Params) -> bool:
    """G1: the correction into the zone must be slower and choppier than the impulse."""
    if p.pb_speed_max >= 99 and p.pb_er_max >= 1:
        return True
    d = z["dir"]
    pb_bars = i - z["leg_bar"]
    if pb_bars <= 0:
        return True
    imp_pts = (z["leg_x"] - z["bot"]) if d == 1 else (z["top"] - z["leg_x"])
    imp_bars = max(z["leg_bar"] - z["ext_bar"], 1)
    pb_pts = (z["leg_x"] - z["pb_x"]) if d == 1 else (z["pb_x"] - z["leg_x"])
    if imp_pts <= 0:
        return False
    ratio = (pb_pts / pb_bars) / (imp_pts / imp_bars)
    if ratio > p.pb_speed_max:
        return False
    if p.pb_er_max < 1:
        lb = z["leg_bar"]
        path = np.abs(np.diff(c[lb:i + 1])).sum()
        er = abs(c[i] - c[lb]) / path if path > 0 else 0.0
        if er > p.pb_er_max:
            return False
    return True


def _approach_ok(d, E, i, c, atr_i, p: Params) -> bool:
    """G3: pull the limit order when price is about to fill and is closing in fast."""
    if p.appr_k <= 0 or i < p.appr_n:
        return True
    near = (c[i] - E <= p.appr_atr * atr_i) if d == 1 else (E - c[i] <= p.appr_atr * atr_i)
    speed = ((c[i - p.appr_n] - c[i]) if d == 1 else (c[i] - c[i - p.appr_n])) / p.appr_n
    return not (near and speed >= p.appr_k * atr_i)


def _htf_ok(d, E, H, i, p: Params, bump) -> bool:
    """G2: HTF narrative. Clean HTF swing sequence and entry in HTF discount/premium."""
    if p.htf_clean and not (H["up_clean"][i] if d == 1 else H["dn_clean"][i]):
        bump("ctx_htf_not_clean")
        return False
    if p.pd_level < 1:
        if d == 1:
            lo, hi = H["pl"][i], H["rh"][i]
            if not np.isnan(lo) and not np.isnan(hi) and hi > lo and E > lo + p.pd_level * (hi - lo):
                bump("ctx_htf_premium_for_long")
                return False
        else:
            lo, hi = H["rl"][i], H["ph"][i]
            if not np.isnan(lo) and not np.isnan(hi) and hi > lo and E < hi - p.pd_level * (hi - lo):
                bump("ctx_htf_discount_for_short")
                return False
    return True


def _plan(d, E, S, leg_x, p: Params, atr_i, eq_lows, eq_highs, risk_eff, bump=lambda k: None):
    risk_pts = (E - S) * d
    if risk_pts <= 0 or np.isnan(atr_i):
        bump("plan_bad_risk")
        return None
    if p.target_mode == "fixed":
        T = E + d * p.fixed_rr * risk_pts
    else:
        T = leg_x
        if np.isnan(T) or (T - E) * d / risk_pts < p.min_rr:
            bump("plan_rr_too_low")
            return None
    # golden rule: never buy above equal lows / never sell below equal highs
    tol, rng = p.eq_tol_atr * atr_i, p.eq_range_atr * atr_i
    lv = eq_lows if d == 1 else eq_highs
    for a in range(len(lv)):
        for bb in range(a + 1, len(lv)):
            if abs(lv[a] - lv[bb]) <= tol:
                x = min(lv[a], lv[bb]) if d == 1 else max(lv[a], lv[bb])
                if (d == 1 and E - rng <= x < E) or (d == -1 and E < x <= E + rng):
                    bump("plan_equal_hl_golden_rule")
                    return None
    q = int(np.floor(risk_eff / (risk_pts * p.point_value))) if risk_eff > 0 else 0
    q = min(q, p.max_qty)
    if q < 1:
        bump("plan_stop_too_wide_or_no_daily_room")
        return None
    return q, T, q * risk_pts * p.point_value


def _make_zone(d, i, piv_bar, o, h, l, c, p: Params):
    lo_b = piv_bar + 1
    if d == 1:
        seg = l[lo_b:i + 1]
        ext_bar = lo_b + int(np.argmin(seg))          # oldest bar with the lowest low
        ext = l[ext_bar]
    else:
        seg = h[lo_b:i + 1]
        ext_bar = lo_b + int(np.argmax(seg))
        ext = h[ext_bar]
    w_lo, w_hi = max(ext_bar - 2, lo_b), min(ext_bar + 2, i - 1)
    zc = ext_bar
    for bb in range(w_hi, w_lo - 1, -1):              # last opposite candle before the impulse
        if (d == 1 and c[bb] < o[bb]) or (d == -1 and c[bb] > o[bb]):
            zc = bb
            break
    if d == 1:
        top, bot = h[zc], ext
        leg_bar = ext_bar + int(np.argmax(h[ext_bar:i + 1]))
        leg_x = h[leg_bar]
        pb_x = l[leg_bar + 1:i + 1].min() if leg_bar < i else leg_x
    else:
        top, bot = ext, l[zc]
        leg_bar = ext_bar + int(np.argmin(l[ext_bar:i + 1]))
        leg_x = l[leg_bar]
        pb_x = h[leg_bar + 1:i + 1].max() if leg_bar < i else leg_x
    if top <= bot:
        return None
    if p.require_fvg:
        ok = False
        run_ext = np.inf if d == 1 else -np.inf
        for k in range(i, ext_bar + 1, -1):           # bars ext_bar+2 .. i, newest first
            if d == 1:
                gap = l[k] > h[k - 2] and run_ext > h[k - 2]
                run_ext = min(run_ext, l[k])
            else:
                gap = h[k] < l[k - 2] and run_ext < l[k - 2]
                run_ext = max(run_ext, h[k])
            if gap:
                ok = True
                break
        if not ok:
            return None
    return dict(dir=d, top=top, bot=bot, leg_x=leg_x, leg_bar=leg_bar, pb_x=pb_x, ext_bar=ext_bar,
                bar=i, armed=False, tap_bar=-1, tap_x=np.nan, pb_ok=True)


# ----------------------------------------------------------------------------
# stats
# ----------------------------------------------------------------------------
def stats(trades, label=""):
    if not trades:
        return dict(label=label, n=0, net=0.0, pf=0.0, win=0.0, expR=0.0, sqn=0.0, maxdd=0.0)
    pnl = np.array([t["pnl"] for t in trades])
    R = np.array([t["R"] for t in trades])
    eq = np.cumsum(pnl)
    dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    gw, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    sd = R.std(ddof=1) if len(R) > 1 else 0.0
    return dict(label=label, n=len(trades), net=float(pnl.sum()),
                pf=float(gw / gl) if gl > 0 else float("inf"),
                win=float((pnl > 0).mean()), expR=float(R.mean()),
                sqn=float(R.mean() / sd * np.sqrt(len(R))) if sd > 0 else 0.0,
                maxdd=float(dd))


def daily_risk(trades, day_of_bar, account=50_000.0):
    """Worst days vs prop-firm daily drawdown limits."""
    if not trades:
        return dict(worst_day=0.0, worst_day_pct=0.0, days_traded=0, days_le_minus_1000=0, max_losing_streak_days=0)
    s = pd.Series([t["pnl"] for t in trades], index=[int(day_of_bar[t["exit_bar"]]) for t in trades])
    d = s.groupby(level=0).sum()
    streak = best = 0
    for v in d.values:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)
    return dict(worst_day=float(d.min()), worst_day_pct=float(d.min() / account * 100),
                days_traded=int(len(d)), days_le_minus_1000=int((d <= -1000).sum()),
                days_le_minus_4pct=int((d <= -0.04 * account).sum()),
                max_losing_streak_days=int(best))
