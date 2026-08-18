# NIGHT_LOG — autonomous overnight run

All times local (America/… per machine clock). Every milestone, block, revert
and judgment call lands here. Written for the owner's morning coffee.

## Setup

- **S1 credentials** — `.env` keys are REAL (owner filled them in before
  sleeping). One minimal fetch: SPY, 6 daily bars, last close 772.62 on
  2026-08-17, paper=True, adjustment=all. Auth OK. All [needs-data] tasks are
  GO.
- **S2 git** — repo was not a git repo. `git init`, broadened `.gitignore`
  (`data/cache/` and `logs/` now cover whole dirs, not just extensions),
  committed everything as `night-shift baseline` → `2c55985`. `.env` verified
  absent from the index.
- **S3 baseline gate** — fast suite 95 passed / slow 2 passed /
  `refresh_docs --check` in sync / `check.py` exit 0. Green before any change.
- **S4** — this file.

## Task log

### T1 — first real-data validation run  ✅
- `--benchmark`: **SmaCross Sharpe 1.05 vs buy-and-hold 1.17 → delta -0.12.**
  Loses, honestly. CAGR 17.1% vs 28.6%. Costs 145.35 vs 15.37.
- Quality audit on real bars: 0 errors, 7 warnings. Key reads:
  - IEX free feed only serves ~2020-07 onward. SPY has a stray 2018 segment
    then a 634-day hole — audit caught it (calendar_gaps + flat_bar), engine's
    intersection discarded it. Effective window 2020-07-27 → 2026-08-17.
  - NVDA +24.3% on 2023-05-25 (the AI-guidance earnings pop) correctly warned
    as `large_gap` (real news), NOT flagged as a split. The volume-corroboration
    design worked on its first real-world contact.
- `--walk-forward` (real): mean OOS edge **-1.45**, 0/4 folds positive.
  IS 1.32 → OOS -0.11 while benchmark scored 1.34. Textbook overfit collapse.
- Cache: rerun logged `cache hit: SPY_alpaca_all_1Day_2018-01-01_2026-08-17.csv`
  etc for all 5; numbers byte-identical. Note: cache falls back to CSV — no
  parquet engine in the venv (known, accepted).
- Adjusted-vs-raw (the round-6 fix, verified EMPIRICALLY for the first time):
  - adjustment=all → audit SILENT on NVDA 2021-07-20, NVDA 2024-06-10,
    AAPL 2020-08-31.
  - adjustment=raw → `error:suspected_split` on EXACTLY those three dates
    (ratios 4.012 / 10.042 / 3.912, volume ×2.2 / ×6.0 / ×6.0 corroborating).
    Overnight-ratio detection mattered: none of the ratios is exactly 4/10/4
    because the stock also moved those days.
  - Sharpe impact: raw drops the strategy 1.05 → 0.46 and doubles the deficit
    vs benchmark (-0.12 → -0.24). Number recorded in README as the reason the
    knob exists.
- Spot-check vs Google Finance: SPY 772.62 vs 772.67, prev 776.30 vs 776.34;
  NVDA 225.16 exact on 08-14. Cents-level IEX-vs-SIP differences. Fine.
- README: `## Results on real data` section added. Fast suite green (95).

### T2 — real-data cache, 15 symbols  ✅
- Universe → 15 large caps across sectors, `start: 2015-01-01`, adjustment all.
  Fetched one at a time, ≥1.2s apart (~18s total, far under the rate limit).
- Reality check on the free feed: asked for 2015, got **2020-07-27 onward** for
  everything (SPY additionally carries a stray 2018-11→2018-? fragment then a
  634-day hole). The IEX free tier simply does not serve deeper daily history.
  Effective universe: 15 symbols × 1519–1523 bars, intersection 1519.
- Audit: **0 errors, 12 warnings.** My read on each warning:
  - META 2022-02-03 (-26.3%) and 2022-10-27 (-24.5%): the two famous earnings
    crashes. REAL, correctly warned as large_gap, correctly NOT called splits.
  - NVDA 2023-05-25 (+24.3%): real (AI-guidance quarter). Same.
  - TSLA 3 sessions >20% (largest +22.6% 2025-04-09), UNH -22.4% 2025-04-17:
    real vol / guidance shock. Warnings only.
  - SPY large_gap/flat_bar/calendar_gaps: artifacts of the 634-day feed hole,
    all on the discarded side of the intersection.
  - Notably SILENT: AMZN 20:1 (2022-06), GOOGL 20:1 (2022-07), TSLA 3:1
    (2022-08) — all inside the window, all invisible because adjustment=all
    is doing its job. More empirical confirmation of the T1 finding.
- Committed config change only; cache stays gitignored.

### T3 — stationary block bootstrap null  ✅
- New `src/bootstrap_null.py`: Politis–Romano stationary bootstrap
  (geometric blocks, expected 20 trading days, circular wrap).
  - ONE index sequence shared by all symbols per trial → cross-sectional
    correlation preserved by construction. Mutation-verified: per-symbol
    indices collapse pairwise corr 0.801 → -0.006.
  - (overnight, intraday) log-return PAIRS resampled jointly, bars rebuilt
    via `_ohlcv_from_components` → real gaps survive for next-open fills.
  - Seeds: blake2b of "bootstrap-<trial>", never hash().
- Wired `--null {gbm,bootstrap}` into the CLI (default unchanged: gbm).
  Bootstrap refuses --synthetic (needs real returns), tested end-to-end on
  the 15-symbol cache: 5-trial smoke run produced a sane band.
- 7 new tests, all 6 planned mutants killed (hash seeding, per-symbol
  indices, doubled returns, block length 1, zero initial price, gaps
  destroyed). Fast suite now 102 (+2 slow).
- Docs regenerated for the NEW 15-symbol shipped config (T2 changed what the
  synthetic universe is, so every pinned number legitimately moved):
  synthetic GBM band now [-0.50, +0.12], win 22%, IS→OOS -0.27 → -0.60,
  1/4 folds positive. INTERVIEW_PREP table rows synced from the snapshot.
- FOUND & FIXED a test-infra flake: a Python child's pipe encoding follows
  the launching shell (utf-8 under PowerShell, cp1252 under Git Bash), so the
  slow doc tests failed depending on WHICH TERMINAL ran pytest. Children now
  get PYTHONIOENCODING=utf-8 explicitly (`_utf8_env()` in test_docs.py).
  This was the reason the first T3 gate showed a mysterious slow failure.

### T4 — bands at 200 trials, both nulls, real 15-symbol universe  ✅
- ~29 min of compute (400 backtests + 200 resamples). Results:
  - Real headline (15 symbols): strat +1.043 vs B&H +1.219 → **delta -0.176**.
  - GBM null:       band **[-0.503, +0.238]**, mean -0.137, win 24%.
  - Bootstrap null: band **[-0.508, +0.082]**, mean -0.210, win 13%.
  - Headline lands INSIDE both (45th pctile of GBM, 57th of bootstrap).
- **The stated expectation was WRONG, and I am reporting what came out**: the
  bootstrap band is NOT wider — 5th–95th width 0.59 vs GBM's 0.74. It is
  shifted DOWN and its UPPER tail is much tighter (+0.08 vs +0.24). Reading:
  on reshuffled real returns, buy-and-hold keeps the full drift while block
  shuffling destroys the trends SmaCross needs, so luck almost never makes the
  strategy look good (win rate 13% vs 24%). Fat tails widen ABSOLUTE outcomes,
  but this statistic is a delta vs benchmark — the honest conclusion is that
  the bootstrap null makes a *positive* fluke rarer, i.e. a real positive edge
  would be MORE convincing against bootstrap, not less. Written into README
  as measured.
- Mechanism decision: the 200-trial real-data bands are NOT wired into
  refresh_docs' generated markers — that mechanism's contract is "reproducible
  on any machine from the shipped config", and these need API keys + 30 min.
  Instead `reports/night_bands.json` (raw deltas included) is committed as a
  dated artifact, README's two band rows are pinned to it by
  `test_readme_real_data_bands_match_the_artifact`, and the generic band
  scanner exempts exactly the artifact's values (delete the artifact and the
  exemption vanishes). Mutations verified: edited row caught twice, deleted
  artifact caught twice, baseline green.
- Also fixed while here: the band scanner divided a decimal band by 100 if the
  LINE contained any % (a table row has a 24% win-rate cell) — the percent
  conversion is now scoped to the matched pair. noise_test now returns raw
  per-trial deltas for the T6 chart. Suite 103 (+2 slow), all gates green.

### T6 — the thesis chart  ✅
- `reports/null_distribution.png` (committed via gitignore negation):
  histogram of the 200 bootstrap deltas, GBM 5–95% band shaded, real result
  (-0.18) as a red line sitting at the 57th percentile of the null mass.
  Pleasing detail: the fat right tail is VISIBLE — isolated flukes at +0.3
  to +0.5 beyond the GBM band edge, even though the bootstrap p95 is tighter.
  Rendered from reports/night_bands.json, so chart and README rows share one
  source of truth. Embedded at the top of README with a one-line caption.

### T5 — both strategies, full gauntlet, real data  ✅
- Benchmark buy-and-hold Sharpe on the 15-symbol universe: +1.219.
- sma_cross sensitivity: **1 of 15 cells beats the benchmark** (50/100 at
  +0.07 — a single standout cell, the classic overfit shape). The config
  default (20/100) has edge -0.176, consistent with T4's headline ✓.
  Walk-forward: mean OOS edge **-0.76**, 0/4 folds positive.
- mean_reversion sensitivity: **1 of 16 cells positive** (20/0.5 at +0.08,
  same single-cell shape). Config default (20/1.0): edge -0.415.
  Walk-forward: mean OOS edge **-0.49**, 0/4 folds positive. Interesting: MR
  degrades less OOS than the trend strategy, but still loses every fold.
- Both "winning" cells sit deep inside both T4 null bands — even the best
  in-sample cherry-pick is indistinguishable from luck.
- No cherry-picking: config-default rows reported in README regardless of the
  grid's best row. Full tables preserved here and in tmp t5_out.

### T7 — housekeeping  ✅
- GLOSSARY: added fat tails, volatility clustering, bootstrap, block
  resampling (stationary bootstrap) — done earlier while compute ran.
- INTERVIEW_PREP: stale prose numbers refreshed (32% → "a fifth"; the old
  synthetic IS→OOS example replaced with the real-data collapse 1.41 → 0.61
  vs benchmark 1.37); new Q&A "What happened when you finally ran it on real
  data?" with the adjustment Sharpe delta (1.04 → 0.46) as the punchline.
- CLAUDE.md: architecture + commands updated for bootstrap_null / --null;
  night-shift record table added (7 rows, incl. the wrong-width expectation
  and both infra bugs found tonight). Note: the record's band values pass the
  scanner because they match the committed artifact — the exemption mechanism
  working exactly as designed.
- Counts: 103 fast + 2 slow, enforced by test_documented_test_count everywhere.

### T8 (stretch) — first-ever execution of the live dry-run path  ✅
- `python -m src.live --once` ran CLEAN on its first execution ever. No fix
  was needed, so no fix-test was added.
  - Connected to the paper account: equity $100,000, day P/L 0.00%.
  - Fetched fresh bars for all 15 symbols (cache deliberately bypassed).
  - SmaCross targets: 12 names long at 6.7% each (the 1/15 slot), GOOGL /
    META / TSLA flat — consistent with the backtester's sizing rules.
  - Dry-run broker logged 12 intended $6,666.67 buys and SUBMITTED NOTHING.
  - `state/positions.json` written correctly; confirmed gitignored; no
    secrets in it.
- Two observations for the owner, documented not "fixed":
  1. `--once` executes the rebalance even while the market is CLOSED (the
     open-check lives in the polling loop, not in rebalance()). For a dry-run
     smoke test at 1am that is arguably the point, but if you ever script
     `--once` on a schedule, know that it computes targets off the last
     session's close.
  2. Live mode refetches all 15 symbols every cycle (use_cache=False). At the
     default 60s poll that is 15 req/min — well under the 200/min limit, but
     worth remembering before shrinking poll_seconds or growing the universe.
