"""Tests for the backtest engine.

The most valuable test here is `test_no_lookahead_bias` — it constructs a
price series where a same-bar-fill engine would print an impossible profit,
and asserts that ours does not.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from src.backtest import Backtester, walk_forward
from src.config import BacktestConfig, Config, DataConfig, RiskConfig
from src.data import synthetic_bars, synthetic_universe, _clean
from src.metrics import compute_metrics, max_drawdown
from src.strategies import BuyAndHold, MeanReversion, SmaCross, build_strategy
from src.strategies.base import Strategy


@pytest.fixture
def cfg():
    return Config(
        symbols=["TEST"],
        data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0, slippage_bps=0.0),
        risk=RiskConfig(max_position_pct=1.0, max_gross_exposure=1.0),
    )


@pytest.fixture
def bars():
    return _clean(synthetic_bars("TEST", n=600, seed=42))


# ---------------------------------------------------------------------------
# Core engine behaviour
# ---------------------------------------------------------------------------

def test_buy_and_hold_tracks_the_asset(cfg, bars):
    """With zero costs, buy-and-hold should closely track the underlying."""
    result = Backtester(cfg).run({"TEST": bars}, BuyAndHold())

    asset_return = bars["close"].iloc[-1] / bars["open"].iloc[1] - 1.0
    strat_return = result.metrics.total_return

    # Not exact: we enter on bar 1's open, and the rebalance threshold lets
    # the weight drift. Within a few percent is correct behaviour.
    assert abs(strat_return - asset_return) < 0.10
    assert result.metrics.n_trades >= 1
    assert result.equity.iloc[0] == pytest.approx(cfg.backtest.initial_cash)


def test_equity_is_conserved_with_no_trades(cfg, bars):
    """A strategy that never takes a position must end with exactly the
    starting cash. Any drift means the accounting leaks."""

    class NeverTrade(Strategy):
        def generate_weights(self, b):
            return pd.Series(0.0, index=b.index)

    result = Backtester(cfg).run({"TEST": bars}, NeverTrade())
    assert result.equity.iloc[-1] == pytest.approx(cfg.backtest.initial_cash)
    assert result.metrics.n_trades == 0
    assert result.metrics.total_costs == 0.0


def test_no_lookahead_bias(cfg):
    """The critical test.

    Build a series with one enormous single-bar jump. Give the strategy a
    signal that is only "on" for the bar where the jump happens. Because the
    signal is computed from bar i's close and filled at bar i+1's open, the
    engine must NOT capture the jump. An engine with same-bar fills would.
    """
    n = 100
    close = np.full(n, 100.0)
    close[50:] = 200.0            # +100% overnight gap at bar 50
    idx = pd.bdate_range("2020-01-01", periods=n)
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close,
         "volume": np.full(n, 1e6)},
        index=idx,
    )

    class OracleOnJumpBar(Strategy):
        """Goes long exactly on the bar the jump prints — i.e. it 'knows'."""
        def generate_weights(self, b):
            w = pd.Series(0.0, index=b.index)
            w.iloc[49] = 1.0      # signal at the close BEFORE the gap prints
            return w

    result = Backtester(cfg).run({"TEST": df}, OracleOnJumpBar())

    # Signal at bar 49 -> fill at bar 50's OPEN, which is already 200.
    # So the trade buys at the post-gap price and captures nothing.
    assert result.metrics.total_return == pytest.approx(0.0, abs=0.02), (
        "Engine captured a gap it could not have traded — lookahead bias!"
    )


def test_costs_reduce_returns(bars):
    """Higher slippage must monotonically reduce returns for the same signal."""
    base = dict(symbols=["TEST"], data=DataConfig(timeframe="1Day"),
                risk=RiskConfig(max_position_pct=1.0))

    returns = []
    for bps in (0.0, 10.0, 50.0):
        cfg = Config(**base, backtest=BacktestConfig(initial_cash=10_000.0,
                                                     slippage_bps=bps))
        r = Backtester(cfg, rebalance_threshold=0.01).run(
            {"TEST": bars}, SmaCross(fast=5, slow=20)
        )
        returns.append(r.metrics.total_return)

    assert returns[0] > returns[1] > returns[2], f"costs not applied: {returns}"


def test_no_leverage_beyond_limit(bars):
    cfg = Config(
        symbols=["TEST"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0),
        risk=RiskConfig(max_position_pct=1.0, max_gross_exposure=1.0),
    )
    result = Backtester(cfg).run({"TEST": bars}, BuyAndHold())
    # Allow a little slack for intrabar marking, but nothing like 2x.
    assert result.exposure.max() < 1.15
    assert (result.cash >= -1e-6).all(), "cash went negative — overdrew the account"


def test_multi_symbol_splits_capital():
    cfg = Config(
        symbols=["A", "B", "C", "D"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0),
        risk=RiskConfig(max_position_pct=0.25, max_gross_exposure=1.0),
    )
    data = {s: _clean(synthetic_bars(s, n=300, seed=i)) for i, s in enumerate("ABCD")}
    result = Backtester(cfg).run(data, BuyAndHold())

    assert set(result.positions.columns) == {"A", "B", "C", "D"}
    # Each name capped at 25% -> gross should land near 100%, not above.
    assert result.exposure.max() < 1.15


def test_rebalance_threshold_reduces_trading(bars):
    cfg = Config(
        symbols=["TEST"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0),
        risk=RiskConfig(max_position_pct=1.0),
    )
    strat = MeanReversion(lookback=10, entry_z=0.5)
    chatty = Backtester(cfg, rebalance_threshold=0.001).run({"TEST": bars}, strat)
    calm = Backtester(cfg, rebalance_threshold=0.20).run({"TEST": bars}, strat)
    assert calm.metrics.n_trades < chatty.metrics.n_trades


# ---------------------------------------------------------------------------
# Regression tests for bugs found by adversarial audit
# ---------------------------------------------------------------------------

def test_long_only_never_holds_negative_shares(bars):
    """Regression: sells used to overshoot and leave short stubs.

    `delta_shares` was computed as `delta_val / fill_px`. For a sell,
    fill_px < px, so the share count came out larger than intended and the
    position crossed slightly below zero — a short book inside a long-only
    config, on 561 of 1500 bars.

    Equity still netted out correctly, which is exactly why every other test
    passed. Only an explicit check on the position sign catches it.
    """
    cfg = Config(
        symbols=["TEST"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0, slippage_bps=25.0),
        risk=RiskConfig(max_position_pct=1.0),
    )
    result = Backtester(cfg).run(
        {"TEST": bars}, SmaCross(fast=20, slow=100, allow_short=False)
    )
    worst = result.positions["TEST"].min()
    assert worst >= -1e-9, f"long-only strategy held {worst} shares (short stub)"


def test_cash_never_goes_negative_with_commissions(bars):
    """Regression: cash could end a hair below zero from commission.

    Affordability was checked against `cash / fill_px`, ignoring commission.
    Buying the maximum then paying commission pushed cash negative — after
    which `max(cash, 0.0) / fill_px == 0` silently blocked every future buy.
    Latent with the shipped zero-commission config, live the moment anyone
    sets one.
    """
    cfg = Config(
        symbols=["TEST"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0, slippage_bps=5.0,
                                commission_per_share=0.01, commission_min=1.0),
        risk=RiskConfig(max_position_pct=1.0),
    )
    result = Backtester(cfg, rebalance_threshold=0.01).run(
        {"TEST": bars}, SmaCross(fast=10, slow=50)
    )
    assert result.cash.min() >= -1e-9, f"cash went to {result.cash.min():.6f}"
    # And buying must still be possible late in the run.
    assert result.positions["TEST"].iloc[len(result.positions) // 2:].max() > 0


def test_exposure_uses_gross_not_net():
    """Regression: exposure was abs(sum of position values), so a long/short
    book at 2x gross reported ~0% exposure. Must be sum of absolute values."""
    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    flat = np.full(n, 100.0)
    df = pd.DataFrame({"open": flat, "high": flat, "low": flat,
                       "close": flat, "volume": np.full(n, 1e6)}, index=idx)

    class LongShort(Strategy):
        """+1 on A, -1 on B — nets to zero, but 2x gross."""
        def generate_weights(self, b):
            return pd.Series(1.0, index=b.index)

    cfg = Config(
        symbols=["A", "B"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0, slippage_bps=0.0),
        risk=RiskConfig(max_position_pct=1.0, max_gross_exposure=1.0),
    )
    result = Backtester(cfg).run({"A": df.copy(), "B": df.copy()}, LongShort())
    # Both legs long here, so gross == net; the point is that exposure counts
    # both positions rather than reporting a single netted figure.
    assert result.exposure.max() > 0.5, "gross exposure under-reported"


def test_walk_forward_reports_a_benchmark():
    """Regression: walk-forward reported an out-of-sample Sharpe with nothing
    to compare it against, so a losing result read as a pass."""
    cfg = Config(
        symbols=["TEST"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0),
        risk=RiskConfig(max_position_pct=1.0),
    )
    data = {"TEST": _clean(synthetic_bars("TEST", n=1200, seed=3))}
    wf = walk_forward(cfg, data, "sma_cross",
                      [{"fast": 10, "slow": 50}, {"fast": 20, "slow": 100}],
                      n_splits=3)

    for col in ("oos_sharpe", "oos_benchmark_sharpe", "oos_edge"):
        assert col in wf.columns, f"walk-forward must report {col}"
    # Edge must be the real difference, not a placeholder. Tolerance is 2e-3
    # because each column is rounded to 3dp independently, so
    # round(a,3) - round(b,3) can differ from round(a-b,3) by up to 0.001.
    computed = wf["oos_sharpe"] - wf["oos_benchmark_sharpe"]
    assert np.allclose(wf["oos_edge"], computed, atol=2e-3)


def test_turnover_is_notional_not_order_count(bars):
    """Regression: 'Turnover' printed n_trades/years, an order-count rate,
    which overstated true notional turnover by roughly 6x."""
    cfg = Config(
        symbols=["TEST"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0),
        risk=RiskConfig(max_position_pct=1.0),
    )
    result = Backtester(cfg).run({"TEST": bars}, SmaCross(fast=20, slow=100))
    m = result.metrics

    assert m.rebalance_rate > 0
    assert m.turnover > 0
    # They measure different things and must not be the same number.
    assert m.turnover != pytest.approx(m.rebalance_rate)
    # Buy-and-hold: ~1x notional turnover total, so well under 1x/yr.
    bh = Backtester(cfg).run({"TEST": bars}, BuyAndHold())
    assert bh.metrics.turnover < 1.0


def test_synthetic_bars_have_realistic_overnight_gaps():
    """Regression: opens sat ~0.1% from the prior close, so gap risk was ~7%
    of daily vol versus 40-70% in real large caps. That made the engine's
    next-open fill discipline free, leaving it untested in P&L terms."""
    df = synthetic_bars("SPY", n=3000)
    gap = np.log(df["open"] / df["close"].shift(1)).dropna()
    daily = np.log(df["close"] / df["close"].shift(1)).dropna()
    ratio = gap.std() / daily.std()
    assert 0.40 < ratio < 0.65, f"overnight gap vol ratio {ratio:.1%} outside 40-60% target"


def test_synthetic_universe_is_correlated():
    """The null distribution must be built from a correlated basket.

    Independent paths diversify away nearly all variance, making the noise
    band too narrow — which biases toward false 'outside the band' claims,
    the exact error the noise test exists to prevent.
    """
    from src.data import synthetic_universe

    syms = ["A", "B", "C", "D", "E"]
    uni = synthetic_universe(syms, n=1200, seed=1, rho=0.8)
    assert set(uni) == set(syms)

    rets = pd.DataFrame({k: np.log(v["close"]).diff() for k, v in uni.items()}).dropna()
    corr = rets.corr().values
    off_diagonal = corr[np.triu_indices_from(corr, 1)]
    assert 0.6 < off_diagonal.mean() < 0.95, (
        f"mean pairwise correlation {off_diagonal.mean():.2f} — "
        "null universe is not realistically correlated"
    )


def test_shipped_cost_model_is_pinned():
    """"Costs stay pessimistic" — enforced, not hoped.

    The docs-drift tolerance (refresh_docs.TOLERANCE = 0.02) cannot catch a
    cost-model change: cross-platform float noise moves the pinned numbers by
    up to ~0.0145 while a measured +40% slippage change moved them by only
    ~0.0075. So the docs check will stay green through a change that
    invalidates every published number. This test closes that hole at the
    source instead.

    If you change any of these values ON PURPOSE, you must in the same
    commit: update this test, update the README's cost-model section, and
    re-run scripts/refresh_docs.py — every published number depends on them.
    """
    from src.config import load_config

    cfg = load_config()
    assert cfg.backtest.slippage_bps == 5.0, (
        "shipped slippage_bps changed — see this test's docstring for what "
        "else must change with it"
    )
    assert cfg.backtest.commission_per_share == 0.0
    assert cfg.backtest.commission_min == 0.0
    # The dataclass defaults must match the shipped YAML, or deleting a line
    # from config.yaml silently changes the cost model.
    assert BacktestConfig().slippage_bps == 5.0
    assert BacktestConfig().commission_per_share == 0.0
    assert BacktestConfig().commission_min == 0.0


def test_no_dead_config_knobs():
    """Regression: `fill_on_next_open` was defined in config.yaml and
    BacktestConfig but read nowhere — a knob implying that lookahead
    protection was optional. Next-open fills are an invariant, not a setting.

    This test previously shelled out to `grep`, which (a) does not exist on
    Windows, breaking the suite and the edit hook, and (b) was VACUOUS: it
    looped over grep's stdout asserting each line was a comment, so an empty
    result — including a failed search — passed trivially. Now it reads files
    in Python and asserts the scan actually covered something.
    """
    from src.config import PROJECT_ROOT

    targets = [
        PROJECT_ROOT / "config.yaml",
        *sorted((PROJECT_ROOT / "src").rglob("*.py")),
        *sorted((PROJECT_ROOT / "scripts").rglob("*.py")),
    ]
    existing = [p for p in targets if p.exists()]

    # Guard against the vacuous-pass failure mode: if we scanned nothing, the
    # assertions below are meaningless and the test must fail loudly.
    assert len(existing) >= 5, f"only scanned {len(existing)} files — bad glob"
    assert any(p.name == "config.yaml" for p in existing)
    assert any(p.name == "backtest.py" for p in existing)

    offenders = []
    for path in existing:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "fill_on_next_open" not in raw:
                continue
            stripped = raw.strip()
            # A mention is only acceptable inside a comment.
            if not stripped.startswith("#"):
                offenders.append(f"{path.name}:{lineno}: {stripped}")

    assert not offenders, "dead knob reintroduced:\n  " + "\n  ".join(offenders)


def test_extreme_commissions_never_break_core_invariants():
    """Regression for a cascade found by second-pass verification.

    The first commission fix guarded buys only (`if side > 0 and ...`). Sells
    were unprotected: for a sell, trade_cost = -proceeds + commission, so a
    commission exceeding the proceeds is a net cash OUTFLOW.

    That alone was minor. The cascade was not. Once cash and then equity went
    negative, position sizing (`target_val = target_w * equity`) FLIPPED SIGN
    — so a long-only strategy opened shorts (observed -158 shares) and gross
    exposure hit 1.70x against a 1.0 cap, with CAGR = nan.

    Latent in the shipped config (commissions are zero), live the moment
    anyone sets one. The old test pinned only commission_min=1.0, far below
    the ~150 threshold where it detonated — which is exactly why it passed.
    """
    syms = ["A", "B", "C"]
    data = synthetic_universe(syms, n=800, seed=7)

    for commission_min in (0.0, 100.0, 150.0, 250.0, 500.0):
        cfg = Config(
            symbols=syms, data=DataConfig(timeframe="1Day"),
            backtest=BacktestConfig(initial_cash=10_000.0, slippage_bps=5.0,
                                    commission_min=commission_min),
            risk=RiskConfig(max_position_pct=0.34, max_gross_exposure=1.0),
        )
        r = Backtester(cfg).run(data, SmaCross(fast=20, slow=100))
        tag = f"commission_min={commission_min}"

        assert r.cash.min() >= -1e-9, f"{tag}: cash {r.cash.min():.2f}"
        assert r.equity.min() > 0, f"{tag}: equity {r.equity.min():.2f}"
        assert (r.positions.values >= -1e-9).all(), f"{tag}: short in long-only book"
        assert r.exposure.max() <= 1.0 + 1e-6, f"{tag}: gross {r.exposure.max():.3f}"
        assert np.isfinite(r.metrics.cagr), f"{tag}: CAGR not finite"


def test_synthetic_headline_and_null_use_the_same_process():
    """Regression: the headline synthetic backtest used uncorrelated
    `synthetic_bars` per symbol (measured pairwise correlation 0.001) while
    the noise band was built from correlated universes (0.80).

    That makes the headline result NOT a draw from its own null distribution,
    so comparing the two is void — and comparison is the entire purpose of
    having a band.
    """
    from src.data import get_universe

    cfg = Config(
        symbols=["A", "B", "C", "D", "E"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(), risk=RiskConfig(),
    )
    uni = get_universe(cfg, synthetic=True)
    rets = pd.DataFrame({k: np.log(v["close"]).diff() for k, v in uni.items()}).dropna()
    corr = rets.corr().values
    off = corr[np.triu_indices_from(corr, 1)]

    assert 0.6 < off.mean() < 0.95, (
        f"headline universe correlation {off.mean():.3f} does not match the "
        "null process — the noise band cannot be compared against it"
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_max_drawdown_known_value():
    eq = pd.Series([100, 120, 60, 80, 150])
    assert max_drawdown(eq) == pytest.approx(-0.5)


def test_metrics_on_flat_equity():
    eq = pd.Series([100.0] * 300, index=pd.bdate_range("2020-01-01", periods=300))
    m = compute_metrics(eq)
    assert m.total_return == pytest.approx(0.0)
    assert m.max_drawdown == pytest.approx(0.0)
    assert m.sharpe == 0.0


def test_sharpe_sign_matches_direction():
    """Drift must be large relative to the standard error of the mean, or
    this test is a 50/50 guess. SE here is 0.004/sqrt(1000) ~= 1.3e-4,
    so a 1.5e-3 drift is ~12 sigma — no seed will move it."""
    n = 1000
    idx = pd.bdate_range("2018-01-01", periods=n)
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.004, n)

    up = pd.Series(100 * np.cumprod(1 + 0.0015 + noise), index=idx)
    down = pd.Series(100 * np.cumprod(1 - 0.0015 + noise), index=idx)

    assert compute_metrics(up).sharpe > 1.0
    assert compute_metrics(down).sharpe < -1.0
    assert compute_metrics(up).total_return > 0
    assert compute_metrics(down).total_return < 0


def test_insolvency_halts_trading_and_keeps_metrics_finite():
    """Reach the insolvency guard and pin its behaviour.

    An audit reported this guard as unreachable dead code: reverting it left
    the suite green, and no ramp — even a 20x or 1e6x rise against a short
    book — drove equity below zero. That is because the engine rebalances
    every bar, so a *gradual* move grinds the position down faster than the
    loss accumulates.

    A single catastrophic OVERNIGHT gap gives no such chance to react. That is
    also the realistic version of this event: a short squeeze, or a halted
    stock reopening on news. Price jumps 1e5x between one close and the next
    open, the short is marked at the new open, and equity is deeply negative
    before any order can be placed.

    Without the guard, `target_val = target_w * equity_at_open` flips sign and
    the engine places an inverted order. The damage is bounded — an audit
    measured one trade and ~100 of equity, because the cash guard blocks every
    buy after that — so this test pins the BEHAVIOUR (a wiped-out account does
    not trade) rather than a magnitude.
    """
    n = 60
    close = np.full(n, 1.0)
    close[n // 2:] = 1e5                       # overnight gap, no chance to react
    idx = pd.bdate_range("2020-01-01", periods=n)
    bars = pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": np.full(n, 1e6)}, index=idx)

    class AlwaysShort(Strategy):
        @property
        def name(self):
            return "always_short"

        def generate_weights(self, b):
            return pd.Series(-1.0, index=b.index, dtype=float)

    cfg = Config(
        symbols=["T"],
        data=DataConfig(start="2020-01-01"),
        backtest=BacktestConfig(initial_cash=100_000.0),
        risk=RiskConfig(max_position_pct=10.0, max_gross_exposure=10.0,
                        min_order_notional=0.0),
    )
    res = Backtester(cfg).run({"T": bars}, AlwaysShort())

    # Precondition: this input really does bankrupt the book. If a future
    # change makes equity survive here, the test is no longer exercising the
    # guard and must be rebuilt rather than quietly passing.
    assert res.equity.min() < 0, "scenario no longer reaches insolvency"

    ruin_ts = res.equity[res.equity <= 0].index[0]

    # The contract: once insolvent, the engine stops trading. It does not
    # trade an inverted book back towards "solvency".
    #
    # `>=`, not `>`. The guard tests equity at the OPEN, and on this input the
    # gap has already happened by then — so the bar the equity series first
    # shows as negative is the same bar the guard must block. An earlier draft
    # of this test used `>` and passed even with the guard disabled, because
    # the one extra trade lands exactly on `ruin_ts`. Off-by-one-bar is how a
    # regression test ends up guarding nothing.
    after = res.trades[res.trades.index >= ruin_ts]
    assert len(after) == 0, (
        f"placed {len(after)} order(s) at or after insolvency ({ruin_ts})"
    )

    # Metrics must still be reportable. nan CAGR turns the most important
    # result a backtest can produce — "this blew up" — into a blank cell.
    assert np.isfinite(res.metrics.cagr), "CAGR is nan on a wiped-out account"
    assert res.metrics.cagr == pytest.approx(-1.0)
    assert np.isfinite(res.metrics.max_drawdown)
    assert np.isfinite(res.metrics.total_return)


def test_insolvency_latches_even_if_equity_recovers():
    """The halt is permanent, and the log message says so.

    The guard used to re-test `equity_at_open <= 0` every bar, with the
    `insolvent` flag only suppressing duplicate log lines. So a book that went
    to zero and then clawed back above it would resume trading — while the log
    read "halting all trading for the rest of the run". The message was a
    promise the code did not keep.

    Here the price gaps up 1e5x (bankrupting the short), then collapses to
    well BELOW the entry price, so the surviving short is deep in profit and
    equity is firmly positive again.

    The tail price matters and this test got it wrong once. Returning to the
    original 1.0 restores equity to +99,950 but the unlatched engine still
    places no order — so the test passed with the latch removed and proved
    nothing. At 0.1 the unlatched engine resumes and trades; the latched one
    does not. Picking the recovery level by eye is not enough; it has to be
    checked against the mutant.
    """
    n = 60
    close = np.full(n, 1.0)
    close[20:40] = 1e5                      # ruin the short...
    close[40:] = 0.1                        # ...then hand back more than it lost
    idx = pd.bdate_range("2020-01-01", periods=n)
    bars = pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": np.full(n, 1e6)}, index=idx)

    class AlwaysShort(Strategy):
        @property
        def name(self):
            return "always_short"

        def generate_weights(self, b):
            return pd.Series(-1.0, index=b.index, dtype=float)

    cfg = Config(
        symbols=["T"],
        data=DataConfig(start="2020-01-01"),
        backtest=BacktestConfig(initial_cash=100_000.0),
        risk=RiskConfig(max_position_pct=10.0, max_gross_exposure=10.0,
                        min_order_notional=0.0),
    )
    res = Backtester(cfg).run({"T": bars}, AlwaysShort())

    assert res.equity.min() < 0, "scenario no longer reaches insolvency"
    ruin_ts = res.equity[res.equity <= 0].index[0]

    after = res.trades[res.trades.index >= ruin_ts]
    assert len(after) == 0, (
        f"resumed trading after insolvency ({len(after)} order(s)); the halt "
        f"must latch for the rest of the run"
    )


def test_documented_test_count_matches_reality(request):
    """The docs quote a suite size. Pin it to the collected count.

    Both numbers have already rotted once: CLAUDE.md claimed 38 and README
    claimed 26 against an actual 46. A stale count is a small lie that makes
    every other claim in the file less trustworthy, and nothing was checking
    it. Adding a test now forces the number to be updated in the same commit
    that changes the suite.

    Skips on a partial run — the count only means anything for the full suite.
    """
    items = request.session.items
    root = Path(__file__).resolve().parent.parent

    # Derive the required set from what is on disk rather than hard-coding it.
    # A hard-coded list silently stops guarding the moment someone adds a new
    # test file — the very failure mode this test exists to prevent.
    required = {p.name for p in (root / "tests").glob("test_*.py")}
    modules = {item.path.name for item in items}
    if not required.issubset(modules):
        pytest.skip("partial run; suite size is only meaningful for the whole suite")

    actual = len(items)

    claims = {
        "CLAUDE.md": r"pytest tests/ -q\s+#\s*tests \((\d+)",
        "README.md": r"tests/\s+#\s*(\d+) tests",
    }
    found: dict[str, int] = {}
    for name, pattern in claims.items():
        match = re.search(pattern, (root / name).read_text(encoding="utf-8"))
        assert match, f"{name}: no test-count claim found — did the line move?"
        found[name] = int(match.group(1))

    # Guard against the test passing because it checked nothing.
    assert len(found) == len(claims)

    for name, claimed in found.items():
        assert claimed == actual, (
            f"{name} says {claimed} tests, the suite collects {actual}. "
            f"Update the doc in this commit."
        )
