# NIGHT_LOG_2 — the universality proof (day run, hard stop 19:00)

Owner's overrides: deadline 19:00 local (checked via Get-Date before each
task; stop starting new work at 18:20); report goes to DAY_REPORT.md; T1–T4
are the core, T5–T7 skippable; hard rules of NIGHT_SHIFT_2.md unchanged.

## Setup

- **Clock at start: 11:25.** ~7.5h of runway.
- **S1 weather source** — VERIFIED. One minimal call:
  `https://archive-api.open-meteo.com/v1/archive?latitude=40.71&longitude=-74.01&start_date=2026-08-10&end_date=2026-08-16&daily=temperature_2m_mean&timezone=UTC`
  → HTTP 200 in 0.49s. Response shape exactly as the spec predicted:
  `{"daily": {"time": [7 ISO dates], "temperature_2m_mean": [27.4 … 23.3]}}`
  (NYC, August, plausible values). UA sent:
  `algotrader-universality/1.0 (personal research; cache-first; low volume)`.
  All [needs-weather] tasks are GO.
- **S2 baseline** — commit `7cb8f5f`. Full gate green before any change:
  fast 103 / slow 2 / docs in sync / hook exit 0.
- **S3** — this file.

## Task log

### T2 — weather data adapter, 10 cities  ✅  (11:43)
- `src/domains/weather.py`: Open-Meteo archive fetch → daily mean series,
  through the existing disk-cache machinery (source key `open-meteo`, daily
  refetch discipline). UA names the project. Etiquette per rule 4 baked in.
- **A real 429 fired on the first big fetch** (46-year archive request right
  after the S1 probe — burst limiter). Handled per rule 4: back off, retry
  once — the identical request succeeded 60s later. Backoff now lives in the
  fetch itself. Remaining 9 cities fetched at 2s spacing, zero further
  incidents. Total ~11 requests for the whole task.
- Also fixed pre-flight: my first pacing logic slept only when the cache
  existed (inverted); now the will-this-fetch decision happens BEFORE the
  call and only real fetches are paced.
- Audit: **all 10 cities clean** — 17,032 days each (1980-01-01→2026-08-18),
  zero NaNs, zero calendar gaps, zero |T|>60°C, and the hemisphere check
  passed (Sydney & São Paulo hottest Dec–Feb). Frankly better data quality
  than the market feed gave us.

### T3 — weather validation: the load-bearing REAL  ✅  (11:52)
- `src/domains/weather_domain.py`: climatology baseline (train-only, per
  expanding fold), persistence + trend k∈{1,3,5}, MAE skill pooled across
  cities, anomaly-shuffle null exactly per spec (blake2b
  f"weather-null-{trial}", cities in sorted order off one stream).
- Interpretation logged per rule 3: the spec's null step 1 says climatology
  "(training data only)" but null construction has no single split across 37
  folds — decomposition uses full-series climatology; the SCORING path
  recomputes train-only climatology per fold regardless, so no forecast ever
  sees the future.
- 200 trials in 17.4s (the panel evaluator is vectorized; no backtest engine
  involved). Results:
  - **persistence: skill +0.344 vs null band [-0.406, -0.399] → REAL, 100th
    percentile.** The required direction, by ~46 band-widths.
  - trend_1 +0.079, trend_3 +0.189, trend_5 +0.251 — all REAL vs their own
    bands. Reported as measured.
  - Per-city persistence skill all positive: Moscow +0.49 (continental) down
    to Sydney +0.15 (maritime). Physically sensible ordering.
- Tests (5) all mutation-checked. **The trap proof is now executable**: the
  test measures that an i.i.d. shuffle kills lag-1 autocorr (|ρ|<0.05) while
  a 20-day block shuffle leaves ρ=0.68 — and the mutant that swaps the null
  for the market-style block shuffle FAILS the test exactly as
  DESIGN_UNIVERSAL.md's trap section predicted. Also killed: forgot-the-
  climatology, hash() seeding, and lookahead climatology (train <= year).

### T4 — synthetic-noise calibration: NOT-NOISE, diagnosed  ⚠️ HALT-CONDITION
- Generator (`src/domains/synthetic_noise.py`): climatology + i.i.d. Gaussian
  anomalies, σ-matched per city, blake2b f"synthetic-noise-{city}". Hygiene
  tests (cross-process determinism; anomaly lag-1 |ρ|<0.05) green and
  mutation-checked (hash() seed; AR(1) noise injection).
- **The zero test came out "REAL", and per the spec that is a stop-and-
  diagnose, so here is the diagnosis:**
  1. The measured statistic is skill = -0.385 — persistence is WORSE than
     climatology on i.i.d. data, almost exactly the theoretical no-skill
     value 1-√2 = -0.414 adjusted for climatology estimation error. The
     spec's "skill ≈ 0" expectation was loosely worded: zero PREDICTIVE
     skill lands at ≈ -0.4 under this scoring, not 0. No signal was
     manufactured in the statistic itself.
  2. The verdict flipped to "REAL" because the statistic sits ~+0.016 above
     the null band, and the band is razor-thin (width 0.008 — pooling 10
     cities × 13k days makes the shuffle-null variance tiny).
  3. Localization: persistence MAE is IDENTICAL real-vs-null (Δ -0.0006).
     The entire gap is the BASELINE: climatology fits the shuffled series
     ~1% better than the original (3.228 vs 3.263 MAE). A permutation
     scatters per-day-of-year residuals as draws WITHOUT replacement from
     the demeaned pool — slightly tighter doy bins than i.i.d. sampling.
     The shuffle makes the baseline stronger, deflating every null skill by
     ~0.016 regardless of the data.
  4. Confirmation: 30 FRESH i.i.d. universes (new seeds — the honest
     no-skill distribution) have mean skill -0.3841, sd 0.0025, and the T4
     statistic (-0.3851) sits dead-centre inside that range. **The data is
     structureless and the pipeline knows it; the anomaly-shuffle null is
     mis-calibrated by +0.016 for this statistic.**
  5. Same check with the spec's sine-climatology fallback: gap +0.015 —
     rules out the empirical-climatology jitter as the cause.
- Consequence for T3: the weather REAL verdict stands on substance — its
  margin (+0.73 skill-units above the null mean) is ~46× the bias. But the
  instrument's RESOLUTION is now measured: against this null, effects
  smaller than ~0.02 skill cannot be trusted. That number is the product of
  tonight's honesty.
- Per hard rule 3 I did NOT redesign the null (the tempting fixes — fresh-
  draw parametric null, or centring the band on shuffle-of-shuffle — are
  exactly the unsupervised improvisation the rule forbids). Verdict recorded
  as measured: **not-NOISE, cause identified, task blocked-as-diagnosed.**
  Per the stop conditions this is a documented halt — "the instrument's
  honesty is the product."

### T1 — harness extracted, market anchor PERFECT  ✅  (12:15)
- `src/validation/harness.py`: the five-part Domain interface, domain-blind
  200-trial band, percentile, verdict. `src/validation/market.py` wraps the
  market as instance #1 (make_null = night 1's block bootstrap / GBM basket).
- **Anchor: 400/400 null deltas reproduced EXACTLY at 6dp through the new
  path** (200 bootstrap + 200 GBM, ~28 min compute), identical band values,
  identical headline (-0.1759), verdict NOISE both. Artifact:
  reports/anchor_200_check.json (committed).
- Pinning tests: statistics layer reproduces the artifact's summary numbers
  from its raw deltas (tolerance = the artifact's own 6dp rounding bound,
  documented); evaluation layer pins headline + bootstrap trial 0 in the
  fast suite and GBM/extra trials in slow. 5 mutants killed (seed shift,
  evaluate drift, verdict-at-p5, flipped percentile, stuck trial index).
- All 105 pre-existing tests untouched and green.

### Post-halt housekeeping note (12:5x)
- Found while landing the final commit: `reports/night_bands.json` and
  `null_distribution.png` were NEVER actually tracked — night 1's gitignore
  used `reports/` (a directory pattern), and git cannot re-include children
  of an excluded directory; `git add -A` skips ignored files silently, so
  the "committed artifact" claim was false until today. The pattern is now
  `reports/*` (children excluded individually, negations work) and all five
  artifacts are verifiably in `git ls-files`. The doc tests never caught it
  because they read the file from DISK, which existed — worth a future test
  asserting pinning artifacts are tracked, noted for the owner.
- `scripts/check.py` appears to hang when invoked in the same shell command
  as a git commit (observed twice, both report commits; runs clean alone,
  18s). Suspect PostToolUse-hook contention on the pytest cache. Benign but
  noted.
- Final state: tree green (115 fast + 3 slow), all artifacts tracked, halt
  per stop condition. DAY_REPORT.md committed as `7bbf11e`, this entry as
  the follow-up.

## Session 2 (resumed 12:59, same deadline 19:00 / soft stop 18:20)

- Owner's T4 ruling received: **zero the instrument** (option 1). The
  synthetic domain is promoted to a permanent calibration standard;
  DESIGN_UNIVERSAL.md amended to record the ruling. Queue continues: T5, T6
  (incl. the rule-7 gambling sweep), T7 if time allows.

### Calibration — the instrument is zeroed  ✅  (13:20)
- DESIGN_UNIVERSAL.md amended with the owner's ruling verbatim-in-substance.
- `src/validation/calibration.py`: K=30 independent seeded structureless
  universes (blake2b "calibration-{k}-{city}", sigma-matched to the real
  panel) through the identical pipeline → `reports/calibration.json`
  (committed) with zero_offset + resolution per predictor and the full skill
  vectors + seed tag.
- Measured: persistence zero_offset **-0.3888**, resolution **0.0091**
  (trend_1 -1.4022/0.0201, trend_3 -0.6689/0.0114, trend_5 -0.5461/0.0096).
- **Analytic cross-check, logged not tuned**: analytic no-skill for
  persistence on iid Gaussian = 1-√2 = -0.4142. Measured -0.3888, gap
  +0.0254 — the size and sign of the known fold-climatology baseline
  inflation (+2.2% on the denominator MAE ≈ +0.029 on skill). Consistent;
  no disagreement beyond the explainable effect.
- Verdicts restated under the new rule (band AND |calibrated| > resolution):
  - weather persistence: raw +0.344, **calibrated +0.733**, 80× resolution →
    **REAL**. Trends +1.48/+0.86/+0.80 calibrated, all REAL.
  - synthetic noise: calibrated **-0.001…-0.002**, all inside resolution →
    **NOISE**. The zero test now PASSES its new condition on all four
    predictors.
  - market path untouched: uncalibrated ValidationResult behaves exactly as
    before (anchor tests still pin it).
- 4 new tests, 4 mutants killed (hash seeding; K-collapse; verdict gate
  removed; zero_offset zeroed). Suite 119 fast + 3 slow, docs in sync.

### T5 — the universality chart and README rewrite  ✅  (14:0x)
- `reports/universality.json`: all three domains' statistics, bands,
  verdicts, calibration reference — one committed artifact, the pattern
  night_bands established.
- `reports/universality.png`: three panels. Design note: the first draft's
  synthetic panel was visually misleading (a 0.02-wide axis made the NOISE
  result LOOK like separation), so panels 2-3 are drawn in ZERO-CORRECTED
  units with the ±resolution zone shaded — the verdict rule is literally
  visible. Bonus: the synthetic panel now shows the shuffle-null's own
  measured bias (-0.015 histogram vs the amber zero zone), i.e. the whole T4
  story in one picture, annotated.
- README: new top section — the instrument claim, the chart, the three-row
  ground-truth table, both design principles (null design; zeroing), the
  Open-Meteo CC BY 4.0 attribution. Trading is now "Domain #1", its section
  intact below. The new section written in measurement language from the
  start (rule 7).
- `test_readme_universality_table_matches_the_artifact` pins the table:
  4 mutants killed (verdict edited, number edited, artifact deleted,
  artifact self-contradicting required != verdict). Suite 120 (+3 slow),
  docs in sync.

### T6 — housekeeping + the rule-7 sweep  ✅  (14:2x)
- **Gambling sweep**: four genuine offenders found and replaced with dyno
  measurement language, logged here per spec:
  1. README "Extending the coin analogy" (two-nulls section) → generic-engine
     vs my-engine run-to-run spread.
  2. GLOSSARY "Noise" entry coin-streak → dyno pulls streak.
  3. INTERVIEW_PREP "Explain the noise test" coin-bias story → the +15hp
     claim vs stock run-to-run spread.
  4. tests/test_backtest.py docstring "just a coin flip" → "a 50/50 guess".
  Everything else the grep surfaced was mechanical ("sign flips",
  "hemisphere flips") or substring noise ("triu_inDICEs"). Final grep hits
  nothing but the policy statement in CLAUDE.md that names the banned words.
- GLOSSARY: +8 entries (climatology, anomaly, persistence, skill score, null
  design, calibration/zeroing, ground truth, white noise).
- INTERVIEW_PREP: pitch rewritten to the owner's arc at all three lengths —
  aimed for profit, hit 1.04 Sharpe, distrusted it, proved it noise, proved
  the prover on known ground truth. Numbers synced (36 defects, 120 tests).
- CLAUDE.md: hard rule 7 recorded as a STANDING POLICY for all future
  sessions, and the universality run's six-row record appended.

### T7 (stretch) — the live forward-prediction log  ✅  (13:40)
- `scripts/predict_tomorrow.py`: forecast endpoint (past_days=8), predicts
  TOMORROW for all 10 cities with persistence + trend k∈{1,3,5}. Honesty
  detail: today's partial day is never used as history — persistence runs
  off the last COMPLETE day, and each line records that base_date. One
  timestamped JSON line per city+predictor, APPEND-ONLY.
- **First real cycle executed**: 40 lines for target 2026-08-19, made_at
  2026-08-18T17:34:54Z, committed — falsifiable before the fact.
- `scripts/score_predictions.py`: grades due lines (archive lag 2 days)
  against actuals, climatology built strictly from data BEFORE the target
  (the no-lookahead rule follows the prediction to its grave), appends to
  predictions_scored.jsonl, never rewrites. Dry run correctly reports
  "40 pending, none due yet".
- 5 scorer tests on synthetic lines; 3 mutants killed (skill sign flipped,
  scores-before-due, lookahead climatology). Suite 125 (+3 slow).
- Owner's morning ritual from tomorrow: `python scripts/score_predictions.py`.
