# CLAUDE.md

Context for Claude Code working in this repo.

## What this is

A paper-trading-first algorithmic trading framework for US equities. Backtest
engine, pluggable strategies, walk-forward validation, and an Alpaca paper
trading loop. Runs entirely on free APIs.

**Purpose is education and portfolio quality, not profit.** Optimize suggestions
for correctness, honesty, and clear engineering — not for making the backtest
numbers look better.

## Commands

```bash
pip install -r requirements.txt

python -m pytest tests/ -q                                  # tests (95, +2 slow)
python -m pytest -m slow                                    # re-runs real backtests (~1 min)
python scripts/refresh_docs.py                              # regenerate README's quoted numbers
python scripts/run_backtest.py --synthetic --benchmark      # no API keys needed
python scripts/run_backtest.py --benchmark                  # REAL data via Alpaca (.env required)
python scripts/run_backtest.py --benchmark --source stooq   # keyless daily bars (see robots.txt note)
python scripts/run_backtest.py --sensitivity                # parameter grid
python scripts/run_backtest.py --walk-forward               # out-of-sample test
python scripts/run_backtest.py --synthetic --noise-test     # null distribution
python -m src.live --once                                   # one dry-run cycle
python -m src.live --live-orders                            # submit to paper account
```

## Architecture

```
data.py      -> OHLCV bars (Alpaca IEX / Stooq CSV opt-in / disk cache / synthetic GBM)
data_quality -> Pre-backtest audit: splits, halts, calendar holes, ragged history
strategies/  -> Strategy ABC. Each returns target weights in [-1, 1] per bar.
backtest.py  -> Bar loop, next-open fills, cost model, walk-forward, sensitivity
metrics.py   -> Sharpe, Sortino, max drawdown, turnover, exposure
broker.py    -> Alpaca wrapper. Dry-run mode. Hard block on live trading.
live.py      -> Poll loop, risk checks, daily-loss kill switch, state persistence
config.py    -> Loads config.yaml + .env. Single source of truth for settings.
```

Data flows one direction: data -> strategy -> backtest/live -> broker. Strategies
never import the broker or place orders; they only emit weights. Keep it that way.

## Invariants — do not break these

1. **No lookahead.** A signal computed from bar `i`'s close fills at bar `i+1`'s
   open. `test_no_lookahead_bias` builds a series with a 100% overnight gap and
   asserts the engine captures ~0% of it. If a change makes that test show a
   profit, the change is wrong.

2. **Strategies must be causal.** `generate_weights` may not use centred windows,
   negative shifts, or any `iloc[i+1]` access. `test_signals_do_not_depend_on_future_bars`
   enforces this by truncating input and comparing earlier signals.

3. **Live trading stays blocked.** `AlpacaBroker.__init__` raises if
   `ALPACA_PAPER` is false, and `TradingClient(paper=True)` is hard-coded. Do not
   parameterise either unless the user explicitly asks and understands the risk.

4. **Costs stay pessimistic.** Do not lower default `slippage_bps` to improve
   results. If a strategy only works at zero slippage, it does not work.

5. **Risk limits belong to the engine, not the strategy.** Position caps, gross
   exposure, and the kill switch are enforced in `backtest.py` / `live.py`.

6. **Synthetic data must stay reproducible.** `stable_seed()` uses blake2b, not
   the builtin `hash()`, because Python randomises string hashing per process.
   `test_default_seed_is_stable_across_processes` pins exact values. Never
   reintroduce `hash()` for seeding.

7. **Any Sharpe reported out-of-sample must carry a benchmark.** `walk_forward`
   runs buy-and-hold on the same test window and reports `oos_edge`. A bare
   Sharpe describes the market, not the strategy.

8. **Null universes must be correlated.** `synthetic_universe` uses a one-factor
   model (rho ~ 0.8). Independent paths diversify away portfolio variance and
   make the noise band too narrow, biasing toward false "outside the band"
   conclusions.

9. **Position signs must match the strategy's mandate.** With `allow_short=False`
   no share count may go negative. Size orders in shares against the reference
   price, never the slipped fill price, or sells overshoot into short stubs.

10. **An insolvent book stops trading.** Once `equity_at_open <= 0`, every
    `target_w * equity` flips sign and the engine would trade an inverted book
    for the rest of the run. The guard in `backtest.py` halts instead. It looks
    unreachable — only an overnight gap reaches it, never a ramp — but it is
    reachable, and `test_insolvency_halts_trading_and_keeps_metrics_finite`
    proves it. Do not delete it as dead code.

11. **Metrics stay finite on a blown-up account.** `cagr` returns -1.0 when the
    equity ratio is <= 0 rather than nan. "This blew up" is the single most
    important thing a backtest can tell you; it must not render as a blank cell.

## Bugs found by adversarial audit (all fixed, all regression-tested)

Kept here as a record of what this codebase's failure modes actually look like:

| Bug | Why tests missed it |
|---|---|
| Walk-forward omitted the benchmark | No test asserted a comparison existed |
| Sells overshot into negative shares | Equity netted out correctly |
| Cash could go negative by one commission | Shipped config has zero commissions |
| "Turnover" was an order-count rate (~6x off) | No test checked the metric's meaning |
| Exposure used abs(net) not sum(abs) | Long-only book makes them identical |
| `fill_on_next_open` defined, read nowhere | Dead config is invisible to tests |
| Null universes were uncorrelated | Nothing asserted a realistic basket |

Second-pass verification then found three defects in those *fixes*:

| Bug | Why it slipped through |
|---|---|
| Commission guard covered buys only; sells could drain cash, and once equity went negative every target weight flipped sign — shorts in a long-only book, 1.70x gross against a 1.0 cap | Test pinned `commission_min=1.0`, far below the ~150 detonation threshold |
| `test_no_dead_config_knobs` shelled out to `grep` — absent on Windows, and vacuous (empty output asserted nothing) | A test that passes for the wrong reason looks identical to one that passes |
| Headline synthetic run was uncorrelated (0.001) while the null was correlated (0.80), so the headline was not a draw from its own band | Nothing asserted the two used the same process |

A third pass audited the *documentation* and the fixes' test coverage:

| Bug | Why it slipped through |
|---|---|
| CLAUDE.md claimed 38 tests, README claimed 26, actual was 46 | Prose is not executable, so nothing ran it |
| README's walk-forward table and noise block, both labelled "actual output", no longer reproduced | They were real once; the generator changed underneath them |
| README said the null universes were "independent random-walk universes" — contradicting a correct sentence 13 lines below it, and the code | Two statements in one file, neither cross-checked |
| README used "+0.3 Sharpe" as an example of a result *inside* the band; widening the band to correlated data pushed +0.3 outside it | The illustration was hard-coded against an old band |
| INTERVIEW_PREP.md quoted the stale band twice, plus a stale test count, win rate and IS→OOS pair | Worst possible place for it — that file is scripted to be said out loud |
| `cagr` returned nan for a wiped-out account (fractional power of a negative ratio), poisoning Calmar and the summary table | No test ever drove equity below zero |
| The insolvency guard was untested — reverting it left the suite green | Every scenario tried was a *gradual* move, which the engine rebalances out of |

The last one is worth dwelling on. An audit concluded the guard was unreachable
dead code because no ramp — 20x, even 1e6x — could drive equity negative. That
was right about ramps and wrong about the conclusion: the engine rebalances
every bar, so a gradual move grinds the position down faster than the loss
accrues. A single 1e5x **overnight gap** against a short book reaches it
immediately, because there is no bar in between to react on. That is also the
realistic version — a squeeze, or a halted stock reopening on news.
`test_insolvency_halts_trading_and_keeps_metrics_finite` pins it.

Its first draft passed *with the guard disabled*, because it asserted "no
trades after `ruin_ts`" using `>` when the extra trade lands exactly on
`ruin_ts`. Off by one bar is how a regression test ends up guarding nothing.

A fourth pass audited the round-three fixes:

| Bug | Why it slipped through |
|---|---|
| `refresh_docs.py --check` compared formatted floats for **exact** equality. libm/BLAS are not bit-identical across platforms, so docs generated on Linux reported DRIFT on Windows | The check was only ever run on the machine that generated the docs |
| A near-tie in `walk_forward`'s parameter search resolved differently per platform (fold 4 picked `fast=5` on Linux, `fast=10` on Windows), changing every number in the row | `>` on floats treats a 1e-15 difference as a real winner |
| `--check` printed `DRIFT` and no diff — unactionable | Nothing asserted the failure message was useful |
| `refresh_docs.py` duplicated `run_backtest.py`'s print formatting; the copies had already drifted in label text, padding, and six missing lines | The whole verification chain was self-referential — README vs snapshot vs the same script again. Nothing compared README to the output of the command README tells you to run |
| `log.error` promised "halting all trading for the rest of the run" but the guard re-tested each bar; `insolvent` only suppressed duplicate logging, so a recovering book resumed | No test drove equity back above zero |
| The insolvency docstring claimed an inverted book "for the rest of the run"; the real damage is one order, bounded by the cash guard | Nobody measured the unguarded case after the cash guard was added |
| The nan-CAGR comment described nan; with the explicit `float()` cast it is a TypeError | The comment documented the pre-refactor failure mode |

`test_insolvency_latches_even_if_equity_recovers` was written for the fifth of
those and **passed with the latch removed** on its first draft — the recovery
price was set to the entry price, where the unlatched engine also places no
order. Choosing the scenario by eye is not enough; it has to be checked against
the mutant. That is now the third time in this project a regression test has
initially guarded nothing.

A fifth pass attacked the never-executed real-data path and the round-four
fixes:

| Bug | Why it slipped through |
|---|---|
| `StockBarsRequest` was built with **no `adjustment` parameter** — whatever the API defaults to decided whether every split in the window was a fake crash | The real-data path had never been executed against a live endpoint |
| The split detector read close/close, so a 4:1 split on a day the stock moved +5% (ratio 3.81) was invisible | Every probe split landed on an otherwise-flat day |
| A genuine -34% crash and a +50% takeover pop were labelled `suspected_split` at ERROR severity | Price alone cannot tell a split from news; volume can, and volume was never read |
| A 5:4 stock dividend (ratio 1.25, -20% gap) produced NO finding at all | 1.25 was in no ratio list and -20% was not `> 0.20` |
| `ragged_history` used strict `< 0.9`: exactly 90% retention was silent, and 91–99% left no trace of up to ~7 months of discarded data | Boundary never tested; sub-warn losses had no info channel |
| `TIE = 1e-9` was six orders of magnitude below real selection margins (0.0037–0.14) and platform noise (~0.001–0.01), so fold selection still flipped across OSes | The tie-break was tested for determinism on one machine, not across two |
| `TOLERANCE = 0.02` passed a +40% `slippage_bps` change (moved pinned numbers ~0.0075) while baseline platform noise consumed 0.0145 of the budget | The tolerance was sized to platform noise, and cost changes move the numbers *less* than that |
| `refresh_docs --check`'s structural check split labels on `":"`, capturing params-dict table rows; one value getting a digit wider broke it with a misleading "missing lines" message | The label heuristic was never run against a table row |
| Doc-test misses: a stale band on a "null distribution spread" line; "independent, random-walk" (comma); a stale band planted in GLOSSARY.md; and INTERVIEW_PREP's win rate, IS→OOS pair and slippage rows were unpinned (a ninety-nine-percent win rate, an absurd IS/OOS pair, and a one-bp slippage claim all passed) | Keyword and subject lists knew specific phrasings, not the claim; only the band row was pinned |
| Duplicate tickers (`[SPY, SPY, QQQ]`) silently loaded 2 of "3" symbols — the partial-load guard never fired because nothing *failed*, the dict deduped | The guard tested fetch failures, not config nonsense |
| `end: null` cached as a literal `"latest"` that never expires — the next day's run silently backtested yesterday's file | Nothing keyed the cache to a calendar day |
| CSV cache round-trip perturbs values ~6e-14, so cached vs uncached equity is not byte-equal (accepted; pinned to 1e-9 on Sharpe) | Only surfaced once the CSV fallback became the live path |

All five rounds reinforce the same lesson: **fixes need auditing too**, and so
do the tests that certify them — and so does the tooling built to stop the
first two problems recurring.

## Real data

Default source is **Alpaca**: an official API whose terms permit programmatic
access, free keys, and the only option for intraday.

`--source stooq` is a keyless daily-CSV alternative and is deliberately NOT the
default: stooq.com's robots.txt disallows all user-agents except Googlebot and
Bingbot (verified). Pulling a few daily bars for personal research is a gray
area plenty of tooling lives in; shipping it as a public repo's default points
every stranger who clones it at an endpoint whose policy asks not to be
crawled — and it must never be automated (no cron, no CI, no cacheless loops).
`test_default_source_is_the_officially_sanctioned_api`
pins this, because "flip it back, it's easier for users" is a tempting change.

Three rules that come with real data:

12. **Audit before you backtest.** `audit_universe` runs automatically on real
    data. An unadjusted split is a -90% single-bar crash that manufactures
    profit, and it is reported as an *error*, not a warning. Do not add
    `--skip-audit` to any documented command.

13. **A partial universe is a different backtest.** `get_universe` raises if any
    configured symbol fails to load. It used to skip silently, which with real
    data means a delisted or fat-fingered ticker quietly changes the experiment.
    `allow_partial=True` / `--allow-partial` is the explicit opt-in. Duplicate
    tickers are rejected at config load for the same reason — a dict deduped
    them silently.

14. **Bars are total-return adjusted by default.** `data.adjustment: "all"`
    (splits + dividends) is passed explicitly to Alpaca — the request used to
    send nothing and inherit whatever the API defaulted to. Buy-and-hold is a
    total-return benchmark; comparing a strategy against it on raw bars
    flatters the strategy. `"raw"` exists to demonstrate the failure mode,
    never to produce quotable results.

The cache key includes the source AND the adjustment. Two providers disagree on
splits, dividends and session times; adjusted and raw bars must never share an
entry either, or a raw file reintroduces the fake-crash bug from disk. When
`end:` is null, the key resolves it to today's date — it used to say a literal
`"latest"`, which never expires, so tomorrow's run silently backtested today's
file. One refetch per symbol per day is the price of the window being what it
claims.

Cache format falls back to CSV when no parquet engine is installed. It used to
write parquet unconditionally and swallow the ImportError, so the cache silently
never worked — pyarrow is not in requirements.txt.

## Numbers quoted in docs are generated, not typed

`scripts/refresh_docs.py` regenerates the walk-forward and noise-test blocks in
README between `<!-- generated:NAME -->` markers, and writes `docs_snapshot.json`.
Do not hand-edit inside those markers.

Two tests enforce it, deliberately split by cost:

| Test | Cost | Catches |
|---|---|---|
| `test_readme_generated_blocks_are_in_sync` | free | README hand-edited away from the snapshot |
| `test_generated_blocks_actually_reproduce` (`@pytest.mark.slow`) | ~1 min | the snapshot no longer matching the code |

`pytest.ini` sets `addopts = -m "not slow"` so the PostToolUse hook stays fast.
**Consequence worth knowing: an engine change that moves the published numbers
passes the default suite.** Run `python -m pytest -m slow` before publishing any
number, and re-run `refresh_docs.py` after touching the engine.

Pattern worth internalising: **every one of these passed a green suite.** Tests
catch what they were written to check. Adversarial review catches the rest.

## Subagents

Three specialists live in `.claude/agents/`. Delegate to them rather than doing
this work inline.

| Agent | Use for | Can edit? |
|---|---|---|
| `backtest-auditor` | Adversarially checking any result before believing it | **No** — read-only by design |
| `test-engineer` | Adding coverage, regression tests, edge cases | `tests/` only |
| `strategy-dev` | Implementing a new strategy end to end | Yes, runs the full gauntlet |

Invoke explicitly with `@agent-backtest-auditor`, or describe the task and let
delegation pick.

**Standing rule: any positive backtest result goes to `backtest-auditor` before
it is reported as real.** The auditor has no edit tools on purpose — an agent
that can both find and "fix" problems tends to fix them by weakening the test.

## Automated checks

`.claude/settings.json` registers a `PostToolUse` hook that runs
`scripts/check.py` after every `Edit`/`Write`. It runs the test suite when a
`.py` file changed, skips otherwise, and exits 2 on failure — which blocks
further work until the suite is green. Run it by hand with
`python scripts/check.py`.

## Conventions

- All tunables go in `config.yaml`, never hard-coded in modules.
- New strategies: subclass `Strategy`, return `clip_weights(...)`, register in
  `src/strategies/__init__.py`. The shared contract tests pick it up automatically.
- Secrets only in `.env` (gitignored). Never commit keys or paste them into code.
- Log via `get_logger(...)`, not `print`, outside of `scripts/`.

## Known gaps (intentional — see README)

Not modelled: market impact, partial fills, dividends, corporate actions, short
borrow costs, taxes, survivorship bias. Alpaca's paper fills are optimistic. Do
not claim backtest results are realistic without noting these.

## When suggesting strategy changes

Be skeptical. Most strategies that look good are overfit. Before endorsing a
result, check it against `--benchmark` (buy-and-hold) and `--walk-forward`
(out-of-sample). A strategy that cannot beat buy-and-hold on risk-adjusted
returns after costs is not worth running.
