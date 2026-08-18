# NIGHT_SHIFT_2.md — autonomous overnight run #2: the universality proof

You are running unattended for several hours. The owner is asleep. Work the
queue in order until done or a stop condition fires. Nobody will answer
questions — make the conservative choice, write it down, keep moving.

Mission in one line: prove the validator is domain-agnostic by pointing it at
weather (must find REAL signal) and self-generated synthetic noise (must find
NOISE), alongside the market result (already NOISE). `DESIGN_UNIVERSAL.md` is the spec — read it
completely before touching anything. Where this file and the spec disagree,
the spec wins.

## Hard rules — non-negotiable

1. **Never weaken a test to go green.** Deleting a failing test, loosening an
   assertion, or widening a tolerance to pass are failure, not progress.
2. **Results are reported as they come out.** A trend predictor with no skill
   is a publishable result. Never massage a number.
3. **Do NOT redesign the null.** The anomaly-shuffle weather null is fixed by
   DESIGN_UNIVERSAL.md, and the synthetic calibration reuses it unchanged. If
   implementation reveals a genuine flaw, write it to NIGHT_LOG_2.md, mark
   the task blocked, and move on. Improvising a different null unsupervised
   is the one mistake that can silently invalidate the whole night.
4. **API etiquette.** ≥1s between requests, one symbol/city at a time,
   cache-first. On HTTP 429/5xx: back off 60s, retry ONCE, then mark the
   domain blocked and continue. Include a User-Agent naming the project.
   Never hammer.
5. **No secrets, no trading.** This mission never reads `.env` and never
   touches `broker.py` or `src/live`. The only API (Open-Meteo) is keyless.
6. **The tree ends green or reverted.** Commit after every completed task,
   message `night2: <task id> <one line>`. Revert instead of debugging a mess
   for more than 3 attempts.
7. **No gambling content, ever.** The owner's standing rule for this project:
   nothing lottery-, betting-, or casino-flavored may enter it — no such
   datasets, examples, features, or analogies, tonight or in any future work.
   When chance needs an analogy, use measurement language (dyno run-to-run
   spread), never wagering.

## Environment notes (hard-won — do not rediscover)

- Windows. `.venv\Scripts\python.exe`. Every `subprocess.run(text=True)` gets
  `encoding="utf-8"`, and child Pythons get `PYTHONIOENCODING=utf-8` (the
  Git-Bash-vs-PowerShell pipe bug from night 1).
- `pytest tests/ -q` fast gate; `pytest -m slow` and
  `python scripts/refresh_docs.py --check` before any commit touching `src/`
  or `scripts/`. The PostToolUse hook blocks on red after every edit.
- `requests` ships with the alpaca dependency; if the import fails,
  `pip install requests` into the venv and note it.
- 200-trial runs took ~29 min total on this machine last night. Budget
  accordingly; do not shrink trial counts to save time without logging it.

## Setup phase

S1. Verify the weather source with one minimal call, and log the exact URL +
    response shape to NIGHT_LOG_2.md:
    - Open-Meteo archive: NYC, 7 days of `temperature_2m_mean`. This endpoint
      was NOT pre-verified — if the URL shape in the spec is wrong, consult
      the response error text and fix the parameter names, but if the service
      itself is unreachable/refusing, mark [needs-weather] tasks blocked. Do
      not fake data. Do not substitute a different weather provider tonight.
S2. Git checkpoint `night2 baseline`. Confirm the full gate is green before
    changing anything: fast, slow, `refresh_docs --check`, `scripts/check.py`.
    Not green at baseline → fixing that is task zero.
S3. Create NIGHT_LOG_2.md, timestamped line per milestone/block/decision.
    It is the owner's morning newspaper.

## Task queue

### T1 Extract the harness — FIRST, because it needs no network
- `src/validation/harness.py`: the five-part Domain interface from the spec
  (series / predict / baseline / score / make_null) and domain-blind
  walk-forward, 200-trial null band, percentile, verdict.
- Wrap the existing market noise test as `MarketDomain` (make_null = the
  block bootstrap from night 1).
- **Anchor: a market run through the NEW harness reproduces
  `reports/night_bands.json` exactly.** Identical band values. A pinning test
  enforces this from tonight on.
- All 105 existing tests stay green, unmodified.
- Done when: anchor test green, suite green, committed.

### T2 [needs-weather] Weather data adapter
- `src/domains/weather.py`: Open-Meteo archive fetch → daily mean series per
  city, through the existing cache mechanism (source key `open-meteo`).
- The 10 cities and coordinates are in the spec. 1980-01-01 → present. One
  request per city, ≥1s apart.
- Quality checks, logged per city: NaN runs, calendar gaps, impossible values
  (|T| > 60°C), and a seasonality sanity check (Sydney's hottest month must
  be Dec–Feb — if it's Jun–Aug the hemisphere handling is broken).
- Done when: 10 cities cached, audit findings in NIGHT_LOG_2, committed
  (cache stays gitignored).

### T3 [needs-weather] The weather validation — the load-bearing result
- `WeatherDomain` per the spec: climatology baseline (training data only —
  the no-lookahead rule applies to climatology exactly as it did to
  parameters), persistence + trend k∈{1,3,5} predictors, MAE skill score
  pooled across cities, anomaly-shuffle null.
- 200 trials, expanding walk-forward folds (train from 1980, test 1990→2026).
- Required new tests, each mutation-checked:
  - Null destroys persistence: lag-1 autocorr of null anomalies |ρ| < 0.05
    while source anomalies' lag-1 ρ > 0.3. This test MUST fail if make_null
    is swapped for the market block bootstrap — that failure is the proof the
    spec's trap section is real. Record that check in the log.
  - Null preserves seasonality: monthly means of null ≈ climatology.
  - Determinism across processes; no NaN/inf on ragged city histories.
- Expected: persistence REAL (far above band). If it is NOT: stop tuning,
  write down everything, mark blocked — per spec, that outcome means the
  instrument is broken and diagnosing it is the night's most important work.
  Trend results: report as measured, whatever they are.
- Done when: verdicts + bands + per-city skill table in NIGHT_LOG_2, tests
  green, committed.

### T4 Synthetic-noise calibration — the zero test
- Generate the noise universe per the spec: ten series = climatology + i.i.d.
  Gaussian anomalies, σ matched per city, blake2b-seeded
  (`f"synthetic-noise-{city}"`). If [needs-weather] is blocked, use the
  spec's sine-wave fallback climatology — this task stays fully
  offline-runnable no matter what.
- Run it through the weather domain code UNCHANGED: same predictors, same
  anomaly-shuffle null, same 200 trials. The only difference is the data.
- Tests, mutation-checked: generation deterministic across processes;
  generated anomalies' lag-1 autocorr |ρ| < 0.05.
- Expected: NOISE, inside band, skill ≈ 0. If REAL: stop, document, blocked —
  the pipeline is manufacturing signal from structureless data, and finding
  where is the night's most important work.
- Done when: verdict + band in NIGHT_LOG_2, tests green, committed.

### T5 The universality chart and the README rewrite
- `reports/universality.png`: three panels (market / weather / synthetic
  noise), each a null histogram with the real result as a labelled vertical
  line and the verdict. One figure that states the whole thesis.
- README: new top section — the instrument claim, the chart, the three-row
  ground-truth table from the spec, and the null-design principle ("a null
  declares which structure you accuse the result of exploiting, then destroys
  only that"). Trading becomes "domain #1", its section kept intact below.
- Attribution line: Open-Meteo (CC BY 4.0). Keep the honest caveats style —
  every claim traceable to a committed artifact (`reports/universality.json`
  with all three bands, same pattern as night_bands.json).
- Done when: chart renders, README embeds it, refresh_docs mechanism extended
  if generated blocks are touched (shared formatter, never pasted numbers),
  committed.

### T6 Housekeeping
- INTERVIEW_PREP.md: the pitch now opens with the owner's own arc — aimed for
  profit, hit the target Sharpe, distrusted it, proved it was noise, then
  proved the prover on domains with known ground truth. Update every number
  that changed tonight (the doc-count test enforces test counts anyway).
- **Gambling sweep (hard rule 7 applied retroactively):** search every
  tracked doc — README, GLOSSARY, INTERVIEW_PREP, CLAUDE.md, docstrings —
  for coin-flip / betting / gambling / odds framing and replace it with
  measurement language. The README's and INTERVIEW_PREP's coin-bias story
  for the noise test becomes the dyno analogy: how much horsepower varies
  run-to-run on the same engine is the baseline you need before believing a
  +15hp claim. Same statistics, no wagering imagery. Log every replacement.
- GLOSSARY.md: climatology, anomaly, persistence, skill score, null design,
  calibration, ground truth, white noise.
- CLAUDE.md: tonight's record appended, including hard rule 7 as a standing
  project policy for all future sessions.
- Done when: doc tests green, a grep for gambling vocabulary across tracked
  files comes back empty, committed.

### T7 [needs-weather, stretch] The live forward-prediction log
- `scripts/predict_tomorrow.py`: fetches the latest bars (forecast API,
  `past_days=2`, current temperature), emits tomorrow's persistence + trend
  predictions for all 10 cities, appends one timestamped JSON line per city
  to `reports/predictions.jsonl`. Append-only; never rewrites history.
- This is the "live data" proof: falsifiable predictions, timestamped before
  the fact, scoreable against reality tomorrow. Include
  `scripts/score_predictions.py` that scores any due lines (skill vs
  climatology) so the owner can run it any morning.
- Done when: one clean prediction cycle logged with real timestamps, scoring
  script has a test on synthetic lines, committed.

## Stop conditions → write MORNING_REPORT_2.md and halt

- Queue complete.
- A task reverted 3 times.
- Weather API dead and only [needs-weather] tasks remain.
- A required-direction result came out wrong (T3 not-REAL / T4 not-NOISE) and
  the diagnosis is written up — that halt is a success condition, not a
  failure: the instrument's honesty is the product.
- Any impulse to weaken a test or redesign a null to force the expected
  verdict — stop immediately and write down exactly what happened.

## MORNING_REPORT_2.md must contain

The three verdicts with bands and percentiles; the market anchor statement
(night_bands.json reproduced: yes/no); per-city weather skill table; every
commit hash + message; API behavior notes (latency, any 429s); what surprised
you; the three most valuable next actions. Two screens max, written like a
handoff to a colleague — because it is one.
