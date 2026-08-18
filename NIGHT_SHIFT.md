# NIGHT_SHIFT.md — autonomous overnight run

You are running unattended for several hours. The owner is asleep. Work the
queue below in order, one task at a time, until it is done or a stop condition
fires. Nobody will answer questions — make the conservative choice, write it
down, and keep moving.

## Hard rules — non-negotiable

1. **Paper only. Never live.** Do not touch the live-trading guards in
   `broker.py`. Do not run `python -m src.live --live-orders`. Markets are
   closed and nobody is watching; the only permitted live-loop command is
   `python -m src.live --once` (dry run, submits nothing).
2. **Never weaken a test to go green.** Deleting a failing test, loosening an
   assertion, or lowering `slippage_bps` to improve numbers are all failure,
   not progress. The repo exists to be honest.
3. **No secrets anywhere but `.env`.** Never print, log, or commit key values.
   `.env` stays gitignored.
4. **Results are reported as they come out.** A strategy losing to buy-and-hold
   is a correct, publishable result. Never massage a number.
5. **The tree ends green or reverted.** Never leave a task half-done with a red
   suite. `git` checkpoints (below) make reverting cheap — use them.

## Environment notes (learned the hard way — do not rediscover these)

- Windows. Use `.venv\Scripts\python.exe`. Always pass `encoding="utf-8"` to
  `subprocess.run(text=True)` — cp1252 mojibake broke the slow tests once.
- Alpaca free tier: 200 req/min. Stay far under it: ~1 request/second, one
  symbol at a time, cache-first. On HTTP 429 back off 60s. On an auth error,
  do NOT retry-loop — mark all data tasks blocked and continue with offline
  tasks.
- `pytest tests/ -q` is the fast gate (~5s). `pytest -m slow` (~2min) and
  `python scripts/refresh_docs.py --check` must both pass before any commit
  that touches `src/` or `scripts/`.
- The PostToolUse hook runs the fast suite after every edit and blocks on red.

## Setup phase (do once, in order)

S1. Verify credentials with ONE minimal fetch (SPY, last 5 days). If auth
    fails: write the exact error to `NIGHT_LOG.md`, skip every task marked
    [needs-data], and continue with the offline tasks. Do not fake data.
S2. Git safety net. If the repo is not a git repo: `git init`. Ensure
    `.gitignore` covers: `.env`, `data/cache/`, `logs/`, `.venv/`,
    `__pycache__/`, `.pytest_cache/`, `_to_delete/`,
    `alpaca-trade-api-python-master/`. Commit everything as
    `night-shift baseline`. From now on: commit after every completed task,
    message `night: <task id> <one line>`. Revert to the last commit instead
    of debugging a mess for more than 3 attempts.
S3. Confirm the full gate is green before changing anything: fast suite,
    `-m slow`, `refresh_docs --check`, `scripts/check.py`. If it is not green
    at baseline, fixing that IS task zero.
S4. Create `NIGHT_LOG.md`. Append a timestamped line at every milestone,
    block, revert, and decision. This is the owner's morning newspaper —
    write it for a human.

## Task queue

### T1 [needs-data] First real-data validation run — the project milestone
- `python scripts/run_backtest.py --benchmark`
- `python scripts/run_backtest.py --walk-forward`
- `python scripts/run_backtest.py --benchmark` again → confirm "cache hit" in logs.
- Adjusted-vs-raw check: fetch NVDA with `adjustment=all` then raw; the
  quality audit must be silent on 2021-07-20 / 2024-06-10 when adjusted and
  fire when raw. Same for AAPL 2020-08-31. Record the Sharpe delta between
  adjusted and raw runs.
- Spot-check two closes against a second source if reachable; else note it.
- Write a `## Results on real data` section into README with: quality-audit
  summary, Sharpe vs buy-and-hold, walk-forward table, and the honest caveat
  that the noise band is currently synthetic-GBM-based.
- Done when: README section exists, numbers reproduce on a rerun, committed.

### T2 [needs-data] Build the real-data cache, responsibly
- Extend `config.yaml` universe to ~15 liquid large caps across sectors
  (e.g. SPY QQQ AAPL MSFT NVDA AMZN GOOGL META TSLA JPM XOM JNJ PG UNH HD),
  `start: 2015-01-01`, adjustment all.
- Fetch one symbol at a time, ≥1s apart. Run the quality audit on the full
  universe; log every finding with your read on whether it is data or real.
- Done when: all symbols cached, audit output in NIGHT_LOG, committed
  (config change only — cache stays gitignored).

### T3 The stationary block bootstrap null — the big feature of the night
Why: the GBM null has no fat tails and no volatility clustering, so the band
is too narrow on real data — biased toward declaring fake edges real. This
replaces it with resampled REAL returns.
- New `src/bootstrap_null.py`:
  - Politis–Romano stationary bootstrap over the universe's log returns.
    Geometric block lengths, expected length ~20 trading days.
  - CRITICAL: resample time indices for ALL symbols jointly (same blocks for
    every symbol) so cross-sectional correlation is preserved by
    construction. This is the same property the GBM null got via the
    one-factor model; losing it re-narrows the band.
  - Resample (overnight, intraday) return PAIRS jointly and rebuild OHLC via
    the existing `_ohlcv_from_components`, so the next-open fill discipline
    still has real gaps to bite on.
  - Deterministic: seeds via blake2b (`stable_seed`-style), never `hash()`.
    Trial t seed derived from f"bootstrap-{t}"; headline comparisons must be
    independent draws, same discipline as the GBM null.
- Wire `--null bootstrap` (default stays gbm) into the noise test CLI.
- Tests, each proven to fail against a broken implementation before shipping:
  determinism across processes; measured pairwise correlation of resampled
  returns within ±0.1 of the source data's; resampled mean/vol within
  tolerance of source; block-length geometry sanity; no NaN/inf; and the
  no-lookahead property still holds end-to-end on resampled bars.
- Docs: README subsection explaining GBM null vs bootstrap null in plain
  language (coin analogy already exists — extend it).
- Done when: suite green including new tests, mutation-checked, committed.

### T4 [needs-data] Bands, properly powered
- Run the noise test at 200 trials on BOTH nulls against the real-data
  universe. Record both bands. Expectation: bootstrap band is wider; say by
  how much.
- Where does the real headline result land in each? Write it into README.
- If refresh_docs' generated blocks need a second block for the bootstrap
  band, extend the mechanism (shared formatter in `src/reporting.py`, same
  markers pattern) rather than pasting numbers.
- Done when: README shows both bands with generated blocks, `-m slow` green,
  committed.

### T5 [needs-data] Both strategies, full gauntlet, real data
- `--sensitivity` and `--walk-forward` for `sma_cross` AND `mean_reversion`
  on the real universe. Read the edge column only. No cherry-picking: report
  the config.yaml defaults' row regardless of what the grid's best row says.
- Done when: results in NIGHT_LOG + a short honest paragraph in README,
  committed.

### T6 The chart that sells the whole project
- `reports/null_distribution.png`: histogram of the bootstrap null deltas,
  the GBM band marked, and the real result as a labelled vertical line.
  Embed at the TOP of README. This one image is the project's thesis.
- Done when: image renders, README embeds it, committed.

### T7 Housekeeping sweep
- INTERVIEW_PREP: update every number that changed tonight; keep the
  conservative counting rules. CLAUDE.md: add tonight's work to the record.
  Test counts everywhere (the doc-count test enforces this anyway).
- GLOSSARY.md: add bootstrap, block resampling, fat tails, vol clustering.
- Done when: doc tests green, committed.

### T8 [needs-data, stretch] First-ever execution of the live dry-run path
- `python -m src.live --once` (dry run — submits nothing). This module has
  NEVER executed. Whatever happens — success, crash, config gap — document
  it exactly. If it crashes, fix only what is needed for a clean dry-run
  cycle, with a test.
- Done when: one clean dry-run cycle logged, or the failure documented with
  a reproduction, committed.

## Stop conditions → write MORNING_REPORT.md and halt

- Queue complete.
- A task has been reverted 3 times.
- Auth/network dead and only [needs-data] tasks remain.
- Any sign you are editing tests to force green — stop immediately and write
  down what happened; that instinct is the failure mode this repo documents.

## MORNING_REPORT.md must contain

Tasks completed vs skipped (with why), every commit hash + message, the key
numbers (real Sharpe vs benchmark, both noise bands, where the result lands),
audit findings on real data, anything that surprised you, and the three most
valuable next actions for the owner. Two screens max. Write it like a handoff
to a colleague, because it is one.
