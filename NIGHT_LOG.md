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
