# DESIGN_UNIVERSAL — repurposing the validator as a domain-agnostic instrument

Owner's framing, verbatim: *"prove that this can take any sort of live data and
differentiate the real results from noise."*

## The claim, stated so it can fail

A detector that always answers "noise" is `return False` with extra steps. To
prove the instrument works it must read correctly in BOTH directions, on data
where the truth is already known:

| Domain | Ground truth | Required verdict | Why we know the truth |
|---|---|---|---|
| Markets (done) | efficient, no simple edge | NOISE ✓ (already measured) | decades of academic evidence + our own two nulls |
| Weather | real structure exists | **REAL** | physics: temperature has day-to-day persistence |
| Synthetic noise | no structure, by construction | **NOISE** | we generate it ourselves from a seeded RNG |

Same harness, three domains, three correct verdicts. If weather comes out
NOISE or the synthetic data comes out REAL, the instrument is broken and that
finding — not a fudged rerun — is the result.

Precisely one outcome is load-bearing: persistence-vs-climatology on weather
MUST test REAL. Everything else (trend predictors, per-city variation) is
reported as measured, whatever it says.

## The core abstraction

Extract the validation logic into `src/validation/harness.py`, domain-agnostic.
A domain supplies five things:

    series      dict[name -> pd.Series]        the real data, daily index
    predict     (history) -> forecast          the rule being tested
    baseline    (history) -> forecast          the naive reference
    score       (forecasts, actuals) -> float  skill number, >0 = beats baseline
    make_null   (series, seed) -> series       structure-destroyed copy

The harness owns, domain-blind: walk-forward folds, the 200-trial null band,
percentile placement, and the REAL/NOISE verdict sentence. The existing market
noise test becomes instance #1 of this interface — nothing about walk-forward
or band construction is finance-specific and never was.

## The null trap — the one decision that must not be improvised

The market null uses ~20-day block bootstrap: it preserves the *texture* of
returns (fat tails, vol clustering) while destroying the multi-week trends
SmaCross tries to exploit. Correct there.

**Applying the same null to weather would be a silent catastrophe.** The
weather signal IS short-range structure (today predicts tomorrow). Twenty-day
blocks preserve day-to-day persistence inside every block — the null would
still contain the signal, the band would inflate to include the real result,
and the tool would wrongly answer NOISE. Green tests, plausible chart,
completely wrong experiment.

Correct weather null, fixed by this document:

1. Climatology per city: mean temperature per day-of-year (training data only).
2. Anomaly series: actual − climatology.
3. Permute the anomalies i.i.d. (destroys persistence; keeps the marginal
   distribution). Seeded blake2b, `f"weather-null-{trial}"`, same discipline
   as the market nulls.
4. Null series = climatology + permuted anomalies (seasonality preserved).

The synthetic-noise domain needs no null design of its own: it reuses the
weather machinery UNCHANGED — only the data is swapped for generated noise.
That reuse is the point: same code path, opposite ground truth.

The principle, which goes in the README because it is the intellectual core:
**designing a null means declaring exactly which structure you accuse the
result of exploiting, then destroying only that.** Blocks for trends,
shuffles for persistence.

## Domain specifications

### Weather
- Data: Open-Meteo archive API (keyless, free non-commercial, CC BY 4.0 —
  attribution line goes in README). Endpoint shape:
  `https://archive-api.open-meteo.com/v1/archive?latitude=..&longitude=..&start_date=1980-01-01&end_date=..&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean&timezone=UTC`
  Response: `{"daily": {"time": [...], "temperature_2m_mean": [...], ...}}`.
  NOT verified from the design environment (its robots rules block crawlers;
  API clients are the documented, sanctioned use) — mission S1 verifies with
  one minimal call before anything depends on it.
- Universe, 10 cities, climate-diverse on purpose (southern hemisphere flips
  seasonality — a real stress test for the climatology code):
  NYC 40.71,-74.01 · London 51.51,-0.13 · Tokyo 35.68,139.69 ·
  Karachi 24.86,67.01 · Sydney -33.87,151.21 · São Paulo -23.55,-46.63 ·
  Cairo 30.04,31.24 · Moscow 55.76,37.62 · Denver 39.74,-104.99 ·
  Singapore 1.35,103.82
- Series: daily mean temperature, 1980-01-01 onward (~46 years, one request
  per city).
- Predictors: persistence `T̂(t+1) = T(t)`; trend `T̂(t+1) = T(t) + (T(t) −
  T(t−k))/k`, k ∈ {1,3,5} for the sensitivity grid.
- Baseline: climatology `T̂(t+1) = clim(doy(t+1))` — the buy-and-hold of
  weather.
- Score: `skill = 1 − MAE(predictor)/MAE(climatology)`, pooled equal-weight
  across cities; per-city table to the log. Baseline's own skill is 0 by
  construction.
- No-lookahead rule carried over: climatology and any tuned k come only from
  data before each test fold. Expanding folds, 1990→2026 tested, 1980+ train.

### Synthetic noise (self-calibration)
- Data: generated, not fetched. Ten series shaped exactly like the weather
  panel: `series = climatology + ε` with ε i.i.d. Gaussian, σ matched to each
  city's real anomaly standard deviation, seeded blake2b
  (`f"synthetic-noise-{city}"`). Persistence has zero skill on i.i.d.
  anomalies by construction — that is the required NOISE ground truth.
- Fallback so this stays fully offline-runnable: if real weather data is
  unavailable, use a sine-wave climatology (365.25-day period, 10°C
  amplitude) instead of the measured one.
- Runs through the weather domain code UNCHANGED — same predictors, same
  anomaly-shuffle null, same trial count. Only `series` differs. This doubles
  as an end-to-end test that the pipeline cannot manufacture signal from
  nothing.
- Hygiene test: generated anomalies' lag-1 autocorrelation |ρ| < 0.05.

### Market (already built)
Wrapped as a `Domain` with `make_null` = existing block bootstrap. Its only
new job: **reproduce `reports/night_bands.json` exactly through the new
harness path.** That reproduction is the refactor's regression anchor.

## Refactor safety anchors — non-negotiable

1. All existing tests stay green, untouched (105 as of last night).
2. Market run through the NEW harness reproduces night_bands.json numbers
   exactly. Byte-identical band values or the refactor is wrong.
3. Every new test mutation-checked (proven to fail against a broken
   implementation) before it counts — house rule since round one.
4. New weather-null test must MEASURE the destruction: lag-1 autocorrelation
   of null anomalies |ρ| < 0.05 while the source anomalies' lag-1 ρ is
   materially positive (expect ~0.6–0.8). That test failing on a block
   bootstrap is what proves the trap in this document is real.

## Expected outcomes (write down before running — this is the point)

| Test | Expectation | If it comes out otherwise |
|---|---|---|
| Weather persistence vs climatology | REAL, far above band | instrument or null is broken — stop, do not tune until it passes |
| Weather trend k∈{1,3,5} | weakly positive or NOISE | report as measured |
| Synthetic noise, persistence rule | NOISE, inside band, skill ≈ 0 | instrument or pipeline is broken — same rule |
| Market anchor | identical numbers via new path | refactor bug — fix or revert |

## Cost

Zero. The only external API (Open-Meteo) is keyless. No secrets touch this
mission — `.env` is not read.
