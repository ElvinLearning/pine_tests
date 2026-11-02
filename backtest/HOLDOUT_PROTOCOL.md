# Final-exam protocol (written before any holdout run)

Development data: NQ 1-minute, Jan 2025 - Apr 2026. Both halves have been looked at; free to iterate.
Final exam (never seen by the bot or by tuning):
  * NQ 1-minute, Apr 11 - Aug 2 2022 (TradingView export, nhuff90/mbt), New York timestamps. MNQ sizing ($2/pt).
  * MES 1-minute, all of 2020 (Snehpatel101/Research), Chicago timestamps, back-adjusted. MES sizing ($5/pt).

Rules
1. At most 3 candidates may take the final exam. Each candidate's parameters are locked from development data.
2. Trades from both exam datasets are pooled.
3. PASS needs all of: >= 30 pooled trades, pooled net profit > 0, p < 0.05/3 = 0.0167 against coin-flip
   direction (same entries) AND p < 0.0167 against coin-flip timing (random entries, same risk management).
4. Results are reported for every attempt, pass or fail. No re-tuning on exam data.

Candidate selection (written before the development screen finished)
  From the top 25 first-half configs (by SQN, >= 30 trades), pick the one with the smallest
  max(p_direction, p_timing) on the second half, among configs with >= 20 second-half trades and
  second-half net > 0. Ties: higher first-half SQN.

Candidate #2/#3 selection (written before running it)
  Grid on ALL development data (Jan 2025 - Apr 2026), now including liquidity-sweep entries, a second
  agreeing HTF and a wider entry window. Keep configs with >= 60 trades and positive expectancy in EACH half.
  Rank by full-period SQN. The exam stays untouched, so selection on all development data is allowed.

Exam entry gate: a candidate is only sent to the exam if its development coin-flip p-values are both < 0.10.
(Skipping the exam never weakens it; the 0.0167 exam threshold is unchanged.)

Results so far: attempt 1 (combo 36) FAIL, attempt 2 (combo 209) FAIL. Both made money on NQ 2022 and lost on MES 2020.

Attempt 3 (written before any attempt-3 data is located or run)
  * NQ 2022 and MES 2020 are now development data. Hypothesis from them: the edge is NQ-specific.
  * Candidate: combo 209 (reports/candidate_B_combo209.json), unchanged.
  * Exam: NQ 1-minute data from periods never used anywhere above (not Jan 2025 - Apr 2026, not Apr 11 - Aug 2 2022).
  * Same pass rule: >= 30 trades, net > 0, p < 0.0167 on both coin-flip tests.
  * Family-wise: three attempts at 0.0167 each keep the chance of a fluke pass at or below 5%.
  * This is the last attempt. If it fails, the verdict is FAIL.

Attempt 3 data: NQ=F 1-minute Yahoo dumps from nhuff90/mbt (historical-data/nq/1min/nq-1min-*), merged:
Oct 5 2022 - Mar 30 2023, 164,866 bars, no overlap with any earlier data. Result: FAIL (51 trades, +$4,172,
p_direction 0.065, p_timing 0.034). All three attempts used; the verdict is FAIL.
