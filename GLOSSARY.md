# Glossary

Plain-English definitions of every term used in this project. No prior finance
knowledge assumed.

---

## The core idea

### Backtest
Running a strategy against historical price data to see what *would have*
happened. You replay history day by day; each day the strategy decides what to
hold, and at the end you see how the money did.

A backtest is a simulation, and simulations lie easily. Most of the engineering
in this project exists to make it lie less.

### Signal
A real, repeatable pattern — something that will probably keep happening.

### Noise
Random fluctuation that *looks* like a pattern but isn't.

Put the same engine on a dyno 100 times and somewhere you'll see three
rising pulls in a row. That "trend" is noise — nothing about the engine
changed. Prices are mostly noise, and human brains are very good at seeing
patterns in noise. Distinguishing the two is the whole game.

### Edge (or alpha)
A genuine, repeatable advantage — a real signal you can trade on. Extremely rare
and extremely hard to prove. Assume you don't have one.

---

## Measuring results

### Benchmark
The simple alternative you must beat. Here it's **buy-and-hold**: buy and never
touch it. A strategy that can't beat that after costs isn't worth running.

### Return
How much the money grew. `Total return` is over the whole period; `CAGR`
(compound annual growth rate) converts that to a per-year figure so periods of
different lengths can be compared.

### Volatility
How much the value bounces around. High volatility = a rough ride.

### Sharpe ratio
Return divided by volatility — *how much return per unit of stress.*

| Sharpe | Read as |
|---|---|
| < 0 | lost money |
| 0.5 | mediocre |
| 1.0 | good |
| 2.0+ | suspicious — look for a bug |

Why not just use return? 20% earned smoothly is much better than 20% that
included a 50% crash. Sharpe captures that; total return doesn't.

### Sortino ratio
Like Sharpe, but only counts *downward* volatility. The reasoning: upside
swings aren't a problem, so don't penalise them.

### Drawdown
How far you've fallen from your highest point. **Max drawdown** is the worst
such fall in the period.

This is the number that determines whether you can actually stick with a
strategy. A backtest showing great returns with a -60% max drawdown is not
tradeable by a human — most people quit near the bottom.

### Calmar ratio
Annual return divided by max drawdown. Return per unit of pain.

### Turnover
How much you trade, per year. High turnover means costs matter enormously.

### Exposure (time in market)
What fraction of the time you were actually holding something. A strategy that
is flat 90% of the time takes far less risk — and should be judged accordingly.

---

## The ways backtests lie

### Lookahead bias
Using information you couldn't have had at the time. **The most common and most
damaging error.**

Example: "buy at today's close if today closed higher than yesterday." You only
*know* today's close after the market shut — you can't buy at it. Easy to write
in code, and it makes worthless strategies look brilliant.

Guarded here by filling every order at the **next bar's open**, and by
`test_no_lookahead_bias`, which checks the engine *fails* to capture a price gap
it couldn't have traded.

### Overfitting
Tuning a strategy until it perfectly explains the past — including the random
parts.

Analogy: tuning a car on one dyno, on one day, at one temperature. Great numbers
there, worse than stock everywhere else. You fit the conditions, not the engine.

Detected here with `--walk-forward` and `--sensitivity`.

### In-sample vs out-of-sample
**In-sample** is the data you used to choose your settings. **Out-of-sample** is
data the strategy has never seen.

Like a practice exam versus the real one. Memorising the practice answers gets
you a great practice score and tells you nothing.

### Survivorship bias
Testing on today's successful companies. Build a list from today's S&P 500 and
you've quietly excluded every company that went bankrupt — so your backtest
only ever holds winners.

### Data snooping
Trying 500 ideas, finding one that works, and reporting only that one. With 500
tries, something will look good by chance. If you don't count the failures, the
winner is meaningless.

---

## Costs

### Slippage
The gap between the price you wanted and the price you got. You aim for $100.00
and fill at $100.05.

Small per trade, brutal at high turnover. Modelled here at 5 basis points per
side by default.

### Basis point (bps)
One hundredth of a percent. 5 bps = 0.05%. 100 bps = 1%.

### Commission
The broker's fee per trade. Zero for US stocks at Alpaca, which is why slippage
is the dominant cost here.

### Spread
The difference between the highest price a buyer will pay and the lowest a
seller will accept. You cross it every time you trade — a hidden cost.

---

## Validation tools in this project

### Walk-forward test
Pick your settings using only the first chunk of history, then score them on the
*next* chunk the strategy has never seen. Slide forward and repeat.

Reveals overfitting: if in-sample Sharpe is 1.17 and out-of-sample is 0.33, the
settings were fit to noise.

### Parameter sensitivity
Score a whole grid of settings and look at the *shape* of the results. A broad
plateau of similar outcomes is mildly reassuring. One brilliant setting
surrounded by bad ones means you found noise.

### Noise test (null distribution)
Run the strategy against many sets of **artificial random data** that contain no
exploitable pattern by construction. This shows the range of results **luck
alone** produces.

If your real result falls inside that range, it isn't evidence of anything. In
this project, a moving-average strategy still "beat" the benchmark in 22% of
random universes.

This is the most important tool here, and the one most retail projects lack.

### Null hypothesis
The assumption that your idea does nothing. You keep that assumption until the
evidence is strong enough to reject it. The noise test measures exactly how
strong "strong enough" has to be.

---

## Trading mechanics

### Bar (or candle)
One period of price data: open, high, low, close, volume. A "1Day" bar covers a
full trading day.

### OHLCV
Open, High, Low, Close, Volume — the five numbers in a bar.

### Long / short / flat
**Long** = you own it and profit if it rises. **Short** = you borrowed and sold
it, profiting if it falls. **Flat** = you hold nothing.

### Position
What you currently hold in one symbol.

### Weight
What fraction of your portfolio a position should be. `1.0` = everything,
`0.25` = a quarter, `0` = nothing, `-1.0` = fully short. Strategies in this
project output weights, not buy/sell orders.

### Rebalance
Adjusting holdings back toward their target weights.

### Rebalance threshold
Only trade when the gap between target and actual exceeds some amount. Without
one, tiny drifts trigger constant trading and costs eat everything.

### Leverage
Trading with borrowed money. Amplifies gains and losses. Disabled here
(`max_gross_exposure: 1.0`).

### Market order
Buy or sell immediately at whatever the current price is. Fast, but you accept
slippage.

### Fill
An order actually executing. The **fill price** is what you really paid.

---

## Infrastructure

### Paper trading
Trading with fake money against real live market data. Free, risk-free, and the
only responsible way to test a bot before real capital.

### Dry run
One step safer than paper trading — the bot logs the orders it *would* place and
submits nothing at all. The default here.

### API key
A password that lets your code talk to a service. Kept in `.env`, never
committed to git.

### IEX vs SIP
Two market data feeds. **SIP** is the full consolidated tape (all exchanges,
paid). **IEX** is one exchange only — free, but sees a small slice of true
volume. This project uses IEX, which is fine for daily bars and poor for
intraday.

### Virtual environment (venv)
A private folder holding one project's Python libraries so they don't collide
with anything else on your machine. Why commands here use
`.venv\Scripts\python.exe` rather than plain `python`.

### Kill switch
An automatic stop. Here: if the account drops more than 3% in a day, the bot
closes everything and halts.

---

## Statistics

### Standard deviation
How spread out a set of numbers is. Small = tightly clustered; large = all over
the place.

### Percentile
The value below which a given share of results fall. The 95th percentile is the
value 95% of results come in under.

### Monte Carlo simulation
Running something many times with random inputs to see the distribution of
outcomes. That's what the noise test does.

### Statistical power
Whether you have enough data to detect an effect. A strategy with 12 trades has
essentially none — any result is luck. Roughly 100+ trades before the numbers
start meaning much.

### Regression to the mean
Extreme results tend to be followed by more ordinary ones. A strategy's best
year is usually part luck, and the next year is usually worse.

### Fat tails
Real market returns produce extreme days far more often than a bell curve
predicts — moves a Gaussian model calls once-in-a-millennium happen every few
years. A null distribution without fat tails understates what luck can do.

### Volatility clustering
Calm days follow calm days and wild days follow wild days; crashes arrive in
bunches, not scattered uniformly. Another property real returns have and
simple random walks do not.

### Bootstrap (resampling)
Instead of assuming a distribution for your data, reuse the data itself:
draw from the observed returns, many times, to see the range of outcomes they
could have produced. The null becomes "your own market, reshuffled" rather
than "an idealised random walk".

### Block resampling (stationary bootstrap)
Resampling one day at a time destroys volatility clustering. Resampling in
blocks — here, runs of geometrically distributed length averaging ~20 trading
days, per Politis–Romano — keeps short-range structure intact while still
scrambling any longer sequence a strategy could exploit. This project also
uses the SAME blocks for every symbol in a trial, so the universe keeps
moving together and cross-sectional correlation survives.

## Weather & calibration (the universality work)

### Climatology
The long-run average for a given day of the year — "what mid-August usually
feels like here". The weather equivalent of buy-and-hold: the naive baseline
every predictor must beat.

### Anomaly
Today's value minus the climatology. Weather forecasting lives entirely in
the anomalies; the seasonal cycle is the easy part everyone gets for free.

### Persistence
The simplest real forecast: tomorrow will be like today. On temperature it
genuinely works (anomalies carry over day to day) — which is what makes it a
perfect known-REAL test signal.

### Skill score
`1 − MAE(predictor) / MAE(baseline)`. Positive means the predictor beats the
baseline; the baseline scores exactly 0 against itself. Unit-free, so it
pools across cities.

### Null design
Choosing what the shuffled "no signal" data preserves and what it destroys.
The principle: declare exactly which structure you accuse a result of
exploiting, then destroy only that. Blocks for trends, shuffles for
persistence — swapping them silently invalidates the experiment.

### Calibration (zeroing)
Running the instrument on data with a known answer and correcting for what
it reads. Here: structureless data should score zero skill; it reads -0.389
through this pipeline, so -0.389 is the zero point and gets subtracted from
every reading. Standard practice on any bench instrument, and the reason a
tiny "signal" inside the zero's own spread proves nothing.

### Ground truth
A case where the right answer is known before the measurement: markets have
no simple edge, temperature persists, generated noise contains nothing. An
instrument you can't test against ground truth is an opinion generator.

### White noise
A series with no correlation between one step and the next — each value is a
fresh draw. The synthetic calibration data's anomalies are white noise by
construction, which is what makes "the instrument must read zero on it" a
fair demand.
