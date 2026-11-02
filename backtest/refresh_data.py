"""Append fresh NQ bars to the base dataset so the pipeline can re-tune.

    python backtest/refresh_data.py --out data/nq_merged.parquet [--csv tv_export.csv] [--yahoo]

Sources (newest wins where they overlap):
  --csv    a TradingView "Export chart data" CSV of CME_MINI:NQ1! or MNQ1!
           (columns time,open,high,low,close; time as unix seconds or ISO).
  --yahoo  the last ~60 days of NQ=F 5-minute bars from Yahoo Finance via
           yfinance (pip install yfinance). Needs internet access to
           query1.finance.yahoo.com, which GitHub Actions runners have.
The result keeps a 1-minute-or-coarser UTC index; the engine resamples to 5m.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def from_tv_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    t = df["time"]
    idx = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t, utc=True)
    out = df[["open", "high", "low", "close"]].astype(float)
    out.index = idx
    return out


def from_yahoo() -> pd.DataFrame:
    import yfinance as yf  # optional dependency
    df = yf.download("NQ=F", period="60d", interval="5m", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close"]].astype(float).dropna()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.join(ROOT, "data", "nq_ohlcv_1m_2025-2026.parquet"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "nq_merged.parquet"))
    ap.add_argument("--csv", action="append", default=[])
    ap.add_argument("--yahoo", action="store_true")
    a = ap.parse_args()

    base = pd.read_parquet(a.base)[["open", "high", "low", "close"]].astype(float)
    if base.index.tz is None:
        base.index = base.index.tz_localize("UTC")
    parts = [base]
    for c in a.csv:
        parts.append(from_tv_csv(c))
    if a.yahoo:
        parts.append(from_yahoo())
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(a.out)
    print(f"{len(df)} bars  {df.index[0]} -> {df.index[-1]}  -> {a.out}")


if __name__ == "__main__":
    main()
