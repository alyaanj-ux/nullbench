# DAY_REPORT — universality run, 2026-08-18 (halted 12:3x, well before the 19:00 deadline)

**Halt reason: the spec's own stop condition, and it is a success condition.**
The zero test (T4) returned not-NOISE; the diagnosis is complete and written
up. Per NIGHT_SHIFT_2.md: "the instrument's honesty is the product." The tree
is green (115 fast + 3 slow, docs in sync, hook exit 0). Full detail in
NIGHT_LOG_2.md.

## The three verdicts

| Domain | Required | Measured | Band (200 trials) | Percentile |
|---|---|---|---|---|
| Market (SmaCross vs buy-and-hold) | NOISE | **NOISE** ✓ | boot [-0.508, +0.082] | 57% |
| Weather (persistence vs climatology) | REAL | **REAL** ✓ skill +0.344 | [-0.406, -0.399] | 100% — ~46 band-widths above |
| Synthetic noise (persistence) | NOISE | **"REAL" ✗ — see below** | [-0.408, -0.400] | statistic -0.385 |

**Market anchor: night_bands.json reproduced — yes, perfectly.** 400/400 null
deltas exact at 6dp through the new harness (200 bootstrap + 200 GBM),
identical bands, identical headline. Artifact: reports/anchor_200_check.json.

## The T4 finding (read this one)

The synthetic data is genuinely structureless — its skill (-0.385) sits
dead-centre in the honest no-skill distribution (30 fresh i.i.d. universes:
mean -0.384, sd 0.003) and persistence MAE matches the null's exactly. What
fails is the null's calibration: **an anomaly permutation makes the
climatology baseline ~1% stronger** (draws without replacement from the
demeaned pool → slightly tighter day-of-year bins), deflating every null
skill by **+0.016**. The band is only 0.008 wide (10 cities × 13k days), so
any structureless series lands "above" it. Diagnosis chain in NIGHT_LOG_2:
localized to baseline MAE (Δ +0.034 of 3.26), reproduced with the sine
fallback (rules out climatology jitter), confirmed with fresh draws.

Consequence: the instrument's measured resolution is ~0.02 skill against this
null. Weather's +0.344 exceeds the bias 46-fold — its REAL verdict stands on
substance. Verdicts on effects < ~0.02 cannot be trusted until the owner
decides how to treat the bias. Per hard rule 3, I did NOT redesign the null.

## Everything else worth knowing

- Weather data: all 10 cities, 17,032 days each (1980→2026-08-18), zero NaNs
  / gaps / impossible values; Sydney & São Paulo hemisphere check passed.
- Per-city persistence skill (all positive): Moscow +0.49, Karachi +0.46,
  Cairo +0.41, Denver +0.41, London +0.38, SaoPaulo +0.36, Singapore +0.27,
  Tokyo +0.26, NYC +0.25, Sydney +0.15. Trend k∈{1,3,5}: +0.08/+0.19/+0.25,
  all REAL, reported as measured.
- The null-trap is now an executable proof: the T3 test measures that an
  i.i.d. shuffle kills lag-1 ρ while a 20-day block shuffle leaves ρ=0.68,
  and the block-null mutant fails it — exactly as DESIGN_UNIVERSAL predicted.
- API behaviour: Open-Meteo ~0.5s/response; ONE real 429 (burst limit on the
  first 46-year request), cleared by the mandated 60s-backoff-retry-once;
  ~11 requests total, all with project UA, everything cached since.

## Commits

```
7cb8f5f night2 baseline (adds DESIGN_UNIVERSAL.md, NIGHT_SHIFT_2.md)
2122f05 night2: T1 harness; market anchor reproduces night_bands 400/400 exactly
dcb7cf2 night2: T2 weather adapter — 10 cities cached, audit clean, 429 handled
4bb4278 night2: T3 weather REAL +0.344 vs [-0.406,-0.399]; block-null trap proven
bf07bbe night2: T4 zero test NOT-NOISE, diagnosed: null baseline bias +0.016
<this>  night2: DAY_REPORT — halted on T4's documented stop condition
```
Last checkpoint for resume: the DAY_REPORT commit on top of `bf07bbe`.

## Remaining (not started, per the halt — resume from here)

- **T5** universality chart + README instrument rewrite (blocked on the T4
  verdict question anyway — the three-panel figure would today show a wrong
  panel).
- **T6** housekeeping incl. the **gambling-vocabulary sweep** (hard rule 7 —
  note: README/INTERVIEW_PREP still carry the old coin-flip framing; the
  replacement dyno language is specified in NIGHT_SHIFT_2 §T6).
- **T7** live forward-prediction log (predict_tomorrow.py).

## Three most valuable next actions

1. **Decide the T4 bias treatment** — the one decision that unblocks T5. The
   spec-conformant options: recentre the verdict on the null's own bias
   (measure shuffle-of-shuffle), or widen the REAL threshold to p95 + bias,
   or adopt fresh-draw parametric nulls for generated data. All three change
   DESIGN_UNIVERSAL.md, which is exactly why an unsupervised run must not
   choose.
2. Then T5's chart tells the whole story honestly — including the T4 panel
   as "the day the instrument measured its own resolution limit."
3. Run the gambling sweep (T6) — it is pure editing, no science, and rule 7
   is standing policy the current docs violate.

— day shift, signing off at the halt. Two verdicts right, one honest
miss that measured the instrument's resolution. That's a good day.
