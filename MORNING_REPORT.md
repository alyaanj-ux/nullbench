# MORNING_REPORT — night shift, 2026-08-17 → 08-18

**Queue: 8 of 8 complete, including the stretch task. Zero reverts. Tree is
green** (103 fast + 2 slow tests, docs in sync, hook exit 0). Full play-by-play
in NIGHT_LOG.md.

## The headline numbers

- **First real-data run in the project's history.** 15 large caps,
  2020-07 → 2026-08 (the IEX free tier ignores `start: 2015` — it serves
  ~2020-07 onward; the audit caught this, not me).
- **SmaCross 1.04 Sharpe vs buy-and-hold 1.22 → delta -0.18.** Walk-forward:
  0/4 folds positive, mean OOS edge -0.76. Mean-reversion: same verdict
  (edge -0.42, 0/4 folds). No edge anywhere, published as measured.
- **Both nulls at 200 trials** (~29 min compute), result pinned to committed
  `reports/night_bands.json`:
  - GBM: [-0.50, +0.24], win rate 24% → real result at 45th percentile.
  - Bootstrap (new): [-0.51, +0.08], win rate 13% → 57th percentile.
  - **Inside both. No demonstrated edge — the correct outcome.**
- **Adjustment knob, measured:** raw bars fire `suspected_split` on exactly
  NVDA 2021-07-20 / 2024-06-10 / AAPL 2020-08-31 (silent when adjusted) and
  move the strategy's Sharpe **1.04 → 0.46**. That one missing parameter would
  have dwarfed every real effect in the project.

## What surprised me (owner should read these)

1. **The bootstrap band is NOT wider than GBM's** — NIGHT_SHIFT.md expected
   wider; it came out narrower (0.59 vs 0.74) and shifted down, upper tail
   much tighter. Reshuffling real returns preserves buy-and-hold's drift while
   destroying the trends SmaCross needs, so lucky *positive* deltas get rarer.
   Consequence: a positive result would be MORE significant against the
   bootstrap null. Reported as it came out (rule 4).
2. **The detector's first real-world contact went perfectly**: META's two
   earnings crashes and NVDA's AI-guidance pop warned as `large_gap`, not
   split; AMZN/GOOGL/TSLA's in-window splits invisible on adjusted data.
3. **Two infra bugs found and fixed tonight** (both with regression cover):
   a Python child's pipe encoding follows the launching shell, so slow tests
   passed under PowerShell and failed under Git Bash (children now get
   PYTHONIOENCODING pinned); and the band scanner divided decimal bands by
   100 when the line contained any "%" cell.
4. **Live path ran clean on its very first execution** (dry run): 12 intended
   $6,666.67 buys logged, nothing submitted, state file correct. Note:
   `--once` rebalances even with the market closed — by design, but know it.

## Commits (baseline + 8)

```
2c55985 night-shift baseline
396def5 T1 first real-data run — adjusted-vs-raw verified empirically
eb9195e T2 universe -> 15 large caps (IEX serves 2020-07+); cache built
3aa0ef7 T3 stationary block bootstrap null; 7 mutation-checked tests
9df5389 T4 both nulls at 200 trials; headline -0.176 inside both bands
68b8f1b T6 null-distribution chart embedded at top of README
9198605 T5 full gauntlet both strategies — no edge anywhere
41d5aee T7 housekeeping — glossary, interview numbers, CLAUDE.md record
51759eb T8 first live dry-run cycle — clean
```
(T6 landed before T5's write-up because the chart only needed T4's artifact;
both tasks' work was done in queue order.)

## Three most valuable next actions

1. **Deeper history.** The band and the walk-forward rest on 6 years / one
   regime. IEX won't serve more; consider a one-off download of 2000-2020
   daily bars from an official free source (e.g. Tiingo's free tier) behind
   the existing source/cache mechanism, then re-run T4/T5.
2. **Regenerate `night_bands.json` monthly** (30 min, keys required) so the
   README's real-data rows stay current — the artifact test will tell you
   when it drifts. A tiny `scripts/refresh_bands.py` wrapping what T4 ran is
   in NIGHT_LOG if you want it scripted.
3. **Let the paper loop run a real market day** (`python -m src.live --once`
   during market hours, then the polling loop supervised). Tonight proved the
   plumbing; the interesting output is the fill-vs-backtest gap log the
   README promises.

— night shift, signing off. The strategy loses; the measurement works.
