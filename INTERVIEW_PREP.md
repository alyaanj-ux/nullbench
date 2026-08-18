# Interview prep — algotrader

Personal notes. Not part of the project docs.

**Core principle: don't defend the strategy, sell the validation.** The strategy
losing is the expected result and saying so confidently is a strength. The
tooling that proves it's losing is the actual work.

---

## The pitch, at three lengths

### 10 seconds
"A backtesting framework for stock strategies, with tooling that measures
whether a result is distinguishable from random luck."

### 30 seconds
"It's a backtesting framework for stock strategies — but the interesting part
isn't the strategies, it's the tooling that tells you when a result is fake. I
kept getting backtests that looked good and turned out to be noise, so I built
a Monte Carlo tool that generates data with provably no signal in it and
measures what luck alone produces. A standard moving-average strategy beats its
benchmark 32% of the time on random data. So most good-looking backtests mean
nothing. I also had the code adversarially audited, four rounds — it found 17 real
defects in my own work, including bugs in my own fixes."

### 2 minutes
"The starting question was whether you could build an automated trading bot.
The answer is that the engineering is easy and the profitability is nearly
impossible, so I built it as a research tool instead of a money-making one.

There are three layers. Data — pulls historical bars from Alpaca's free tier,
caches them, and can generate synthetic data so the whole thing runs with no
API keys. Strategies — each one outputs a target portfolio weight per bar, and
structurally can't place orders, so all the risk logic lives in the engine.
And the engine — replays history bar by bar, fills orders at the *next* bar's
open, and models slippage and commission.

That next-bar fill is the important detail. If you fill at the same bar's
close, you're trading at a price you only knew after the bar ended. That's
lookahead bias and it's the number one reason backtests lie. I have a test that
builds a series with a 100% overnight gap and asserts the engine *fails* to
capture it.

The part I'd point to as the real contribution is the noise test. Any single
backtest gives you one number with no context. So I generate many universes of
correlated random-walk data — provably no exploitable structure — and run the
strategy against all of them. That gives a null distribution: the range of
results luck alone produces. On my setup that's roughly minus 0.50 to plus 0.12
in Sharpe versus benchmark. Any real result inside that band isn't evidence of
anything — including my own, which lands inside it. That's the correct answer
on random-walk data, and a tool that told me otherwise would be broken.

Then I had it adversarially audited by a read-only reviewer, repeatedly. Round
one found seven real bugs. I fixed them, re-ran the audit, and round two found
three more — in the fixes. Round three found the documentation had gone stale:
numbers I'd pasted in as 'actual output' no longer reproduced. Round four
caught the reproducibility mechanism itself not being reproducible across
platforms. Round five attacked the real-data path before it had ever run and
found, among other things, that the fetch never specified split adjustment.
Thirty-six defects across five rounds, each one documented in CLAUDE.md with
why the tests missed it, and each fix shipped with a regression test proven to
fail against the pre-fix code."

---

## Likely questions

### "Does it make money?"
No, and I'd be skeptical of any student project claiming it did. My strategies
lose to buy-and-hold after costs, which is the expected result — anything simple
enough to describe in a sentence was arbitraged away decades ago. The deliverable
isn't profit, it's a correct measurement. It reliably tells me when something
doesn't work, which is most of the time.

### "What was the hardest bug?"
The reproducibility one. My collaborator and I ran identical code and got -0.72
and -0.18. Root cause: I seeded random test data from Python's `hash()`, which is
randomized per process for security. So my "reproducible" data differed every
run, and no two results were comparable. Fixed with blake2b and a regression test
pinning exact values. The deeper lesson was that both numbers were inside the
noise band anyway — which is what pushed me to build the null-distribution tool.

### "How do you know your backtest is correct?"
Three ways, in increasing strength.

Unit tests on the accounting — a strategy that never trades must end with exactly
the starting cash, cash can never go negative, equity always equals cash plus
mark-to-market.

A dedicated lookahead test — a synthetic series with an untradeable overnight gap
where the engine must capture zero.

And adversarial review. I set up a reviewer with no edit access, deliberately,
because something that can both find and fix problems tends to fix them by
weakening the test. Round one found seven real bugs that had all passed a green
suite. Round two found three more in my fixes. Round three found five stale or
self-contradicting claims in my own documentation. Round four found that the
script I wrote to stop the docs going stale wasn't reproducible across
platforms — it compared formatted floats for exact equality, so docs generated
on Linux failed on Windows. Every one of those rounds started from a green suite.

### "Tell me about one of those bugs."
Sells were sized against the slippage-adjusted fill price instead of the
reference price. Since a sell fills below the reference, that produced a slightly
larger share count than intended, and positions crossed just below zero — a short
book inside a long-only config, on roughly a third of all bars (I measured
1338 of 1500 on the original code; a later re-measure on a changed generator
gave 561, so I quote the magnitude, not a precise count). Every test passed because
equity still netted out correctly. The accounting was right, the positions were
wrong, and nothing was checking position sign.

### "What's overfitting, and how did you handle it?"
Tuning a strategy until it explains the past including the random parts. Like
tuning a car on one dyno on one day at one temperature — great numbers there,
worse than stock everywhere else.

Three defences. Walk-forward: pick parameters on one window, score them on the
next unseen one. Parameter sensitivity: look for a broad plateau of similar
results rather than one lucky spike. And the noise test, which measures how much
apparent edge randomness alone generates.

On my data, in-sample Sharpe of 0.63 collapses to -0.21 out-of-sample. That gap
is overfitting made visible.

### "Explain the noise test like I don't know finance."
Suppose you want to know whether a coin is biased. You flip it 100 times and get
55 heads. Is that bias, or luck? To answer, you need to know how often a *fair*
coin gives 55+ heads — that's your baseline.

Same idea. I generate market data that's provably random, run the strategy on it
hundreds of times, and record how well it does. That's the range luck produces.
If my real result lands inside that range, I've learned nothing.

The result that surprised me: on random data, the moving-average strategy still
"beat" the market 32% of the time.

### "Why not use an existing library like Backtrader?"
For a production system I would. I wrote my own because the point was
understanding the failure modes — and you can't learn where a backtest lies by
calling someone else's `run()`. Writing the fill logic myself is why I understand
next-bar execution well enough to have a test for it. I did use pandas and numpy;
I didn't reinvent those.

### "What would you do differently?"
Two things. I'd have written the noise test first — I built strategies, got
excited about results, and only then built the tool that told me they were
meaningless. Backwards.

And I'd have tested position state, not just equity. Every one of the position
bugs survived because I only ever asserted on the equity curve. Green tests gave
me false confidence.

### "What's not tested?"
The live trading loop and the broker wrapper. They're written and the live path
is hard-blocked, but I haven't run them against the real API. I wouldn't claim
they work. Also nothing is validated against real market data end to end — all
my numbers are from synthetic data.

### "Walk me through the architecture."
One-directional: data → strategy → engine → broker.

Strategies only emit target weights, in the range -1 to 1. They can't import the
broker or place orders. That means position caps, gross exposure limits, and the
daily-loss kill switch all live in the engine and apply to every strategy
automatically, including any I add later.

Config is a single YAML file, so no experiment requires a code change. Secrets
live in a gitignored `.env`. The broker refuses to construct against a live
endpoint at all.

### "Is there any ML in this?"
No. I considered gradient-boosted trees on engineered features, but I'd have been
building a model before I had a trustworthy way to evaluate one — which is how
people convince themselves overfit models work. The evaluation harness had to
come first. It's the obvious next step now that it exists.

### "How long did it take?"
Roughly a day for the core framework, plus the audit-and-fix cycle. The code was
fast; understanding why the results were wrong was the slow part.

---

## Numbers to know cold

These are regenerated by `scripts/refresh_docs.py` and pinned by
`tests/test_docs.py`. If you change the engine, re-run it before quoting them.

| Metric | Value |
|---|---|
| Tests | 102, including audit regressions |
| Noise band (Sharpe vs benchmark) | -0.50 to +0.12 |
| Strategy "wins" on random data | 22% of trials |
| In-sample → out-of-sample Sharpe | -0.27 → -0.60 |
| Walk-forward folds beating benchmark | 1 of 4 |
| Where my own result lands | inside the band, near the null mean |
| Defects found by audit | 36 across five rounds — engine, tooling, tests, docs; each in CLAUDE.md's tables |
| Default slippage assumption | 5 bps per side |

---

## Traps

- **Never call it an "AI trading bot."** Say "backtesting framework with
  statistical validation." The first sounds naive.
- **Never claim it's profitable.** Instant credibility loss with anyone who
  knows the field.
- **Never overstate coverage.** If asked about something you didn't test, say
  so. "I haven't validated that" is a strong answer.
- **Don't oversell the AI/ML angle.** There isn't one.
- **Don't get defensive if they poke holes.** They're testing whether you already
  know the weaknesses. You do — the README has a "Known gaps" section. Point at it.

## If they push hard on a weakness

Good answer shape: acknowledge, show you'd already identified it, say what you'd
do about it.

> "Right — my null universes are Gaussian random walks, and real returns have
> fat tails and volatility clustering. That makes my noise band somewhat
> optimistic. The fix would be block-bootstrapping actual historical returns
> instead of simulating them, which preserves those properties. I'd do that
> before trusting the band on real data."

That answer is worth more than the project.
