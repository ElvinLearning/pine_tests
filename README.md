# Structure S/D Day Bot (Pine Script v6 + Python twin)

A day-trading strategy for MNQ/NQ futures built from the "Understanding Price" technical analysis master course:
market structure with body-close breaks, supply and demand zones with an open imbalance, one tap only, the
equal highs/lows golden rule, momentum, and top-down bias. It adds a context layer that turns the course's
"discretionary" reads into measurable rules, plus prop-firm drawdown guards.

**Status: the coin-flip gate FAILS. Paper trade only, NQ/MNQ only.** Best result on never-seen NQ data:
51 trades, +$4,172, +0.36R per trade, p = 0.065 / 0.034 (needed < 0.0167). It lost on S&P (MES) data.
Full numbers: [reports/REPORT.md](reports/REPORT.md). Exam rules: [backtest/HOLDOUT_PROTOCOL.md](backtest/HOLDOUT_PROTOCOL.md).

| Path | What it is |
|---|---|
| `strategy/understanding_price_strategy.pine` | The TradingView strategy. Paste into the Pine Editor. |
| `backtest/engine.py` | Bar-for-bar Python mirror of the Pine logic and TradingView's fill rules. |
| `backtest/pipeline.py` | Tune on the first half, test once on the second half, coin-flip gate, prop-eval sim, self-update. |
| `backtest/coinflip.py` | The coin-flip rule. |
| `backtest/dev_search.py`, `dev_gate.py`, `final_exam.py` | Development search, exam entry gate, final exam on untouched data. |
| `backtest/prop_eval.py` | Topstep-style 50K eval simulator. |
| `backtest/update_pine.py` | Rewrites the AUTO-PARAMS block in the Pine file. |
| `backtest/refresh_data.py` | Appends new bars (TradingView CSV export or Yahoo) for re-tuning. |
| `.github/workflows/self-update.yml` | Weekly re-tune + gate + commit. |
| `params/params.json` | Current parameters and their gate result. |
| `reports/` | Report, charts, trade lists, full grid. |

## Rules in the bot

Mechanical (from the course):
1. Trend from swing highs and lows. A break of structure needs a candle body close.
2. Higher-timeframe (HTF) structure sets the bias. Buys only in HTF uptrends, sells only in downtrends.
3. Demand zone = last bearish candle before the impulse that broke structure. Supply = last bullish candle before the drop.
4. The impulse must leave an open imbalance (fair value gap).
5. One tap only.
6. Never buy above equal lows, never sell below equal highs.
7. A big candle slamming into the zone kills it.
8. Stop just past the zone. Target the impulse extreme or a fixed R. Minimum R:R.

Context layer:
* G1 pullback quality: the correction must be slower than the impulse and choppy.
* G2 HTF narrative: clean HTF swing sequence (and optional discount/premium location).
* G3 pre-fill cancel: pull the limit when price is closing in fast with opposing momentum.
* G4 prop drawdown: size every trade to fit the remaining daily loss room, stop after 2 straight losses,
  daily loss limit, max 3 trades a day, flat by 15:50 New York time.

Current parameters (combo 209, see the AUTO-PARAMS block): 5-minute chart, 15-minute bias that a 60-minute
timeframe must agree with, clean HTF structure required, limit entry at fresh zones plus internal-liquidity
sweep entries (wick through an internal low/high, close back, then a lower-timeframe break), fixed 2.5R target,
entries 09:35-15:00 New York. Use it on NQ/MNQ only.

## Paper trading on TradingView

1. Open a chart of `CME_MINI:MNQ1!` on the **5-minute** timeframe.
2. Pine Editor: paste `strategy/understanding_price_strategy.pine`, Save, Add to chart.
   This file has not been compiled in TradingView yet (this build environment cannot reach tradingview.com).
   If the editor shows an error, send it over and it will be fixed.
3. Settings, Inputs, "Risk / prop-firm eval": set your risk per trade, daily loss limit, and the eval start date.
   The panel on the chart shows the coin-flip gate, HTF bias, active zone, last skip reason, today's P&L and
   the eval tracker.
4. Forward test: leave the strategy on the chart. The Strategy Tester adds trades in real time.
5. Optional automation to a paper/sim account: Create Alert, Condition "SD Day Bot", "Order fills only",
   Message `{{strategy.order.alert_message}}`, and a webhook to a bridge that supports your sim account.
   TradingView's own Paper Trading account does not take strategy orders automatically.

Notes:
* Without a real-time CME data add-on, TradingView futures data is delayed.
* The Strategy Tester only shows as much 5-minute history as your plan loads. The full 16-month backtest is in `reports/`.

## Re-running the backtest and the self-update

```bash
python3 -m venv .venv && .venv/bin/pip install pandas pyarrow numpy matplotlib
```

```bash
bash backtest/fetch_data.sh
```

```bash
.venv/bin/python backtest/pipeline.py
```

```bash
.venv/bin/python backtest/make_report.py
```

The pipeline tunes on the first half, tests once on the second half, applies the coin-flip gate, and rewrites
the AUTO-PARAMS block of the Pine file. New parameters replace the current ones only when they pass the gate,
or when the current ones never passed either. Paste the updated Pine file into TradingView afterwards
(TradingView has no API for updating script code).

To add newer bars first, export chart data from TradingView (or use `--yahoo`):

```bash
.venv/bin/python backtest/refresh_data.py --csv path/to/export.csv --out data/nq_merged.parquet
```

```bash
.venv/bin/python backtest/pipeline.py --data data/nq_merged.parquet
```

The GitHub workflow does the same every Saturday using Yahoo's NQ=F 5-minute bars. That data step was not
testable from this build environment.

Data: NQ 1-minute bars from a public GitHub repository (`teaceo-debug/deep6`). The file is third-party market data,
so it is downloaded by `fetch_data.sh` and not committed.

Not financial advice. Backtests are not live results.
