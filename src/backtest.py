"""Bar-by-bar portfolio backtester with an honest cost model.

Design decisions, and why:

1. **Next-open fills.** A signal computed from bar `i`'s close is executed at
   bar `i+1`'s open. You cannot trade at a price you only knew after the bar
   closed. Same-bar fills are the single most common way backtests lie.

2. **Costs on every share traded.** Slippage in basis points on the fill
   price, plus optional per-share commission. Default 5bps per side is
   deliberately pessimistic for liquid large caps — better to be surprised
   upward.

3. **No leverage by default, position caps enforced.** The engine, not the
   strategy, owns risk.

4. **Explicit rebalance threshold.** Without it, a continuously-varying
   target weight generates a trade every single bar and costs eat everything.

Deliberately NOT modelled (know your gaps): market impact, partial fills,
borrow costs on shorts, dividends, corporate actions, and taxes. Real returns
will be worse than what this prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .logging_setup import get_logger
from .metrics import Metrics, compute_metrics
from .strategies import Strategy

log = get_logger("backtest")


@dataclass
class BacktestResult:
    equity: pd.Series
    positions: pd.DataFrame          # shares held per symbol, per bar
    target_weights: pd.DataFrame     # what the strategy asked for
    trades: pd.DataFrame             # every executed fill
    metrics: Metrics
    cash: pd.Series = field(default_factory=pd.Series)
    exposure: pd.Series = field(default_factory=pd.Series)

    def summary(self) -> str:
        return str(self.metrics)


class Backtester:
    def __init__(self, cfg: Config, rebalance_threshold: float = 0.05):
        """
        rebalance_threshold: only trade when the target weight differs from
        the current weight by more than this (in absolute portfolio weight).
        Set to 0.0 to trade on every change — then watch costs destroy you.
        """
        self.cfg = cfg
        self.bt = cfg.backtest
        self.risk = cfg.risk
        self.rebalance_threshold = rebalance_threshold

    # -- helpers ------------------------------------------------------------

    def _fill_price(self, raw_price: float, side: int) -> float:
        """Apply slippage against us: buys fill higher, sells fill lower."""
        slip = self.bt.slippage_bps / 10_000.0
        return raw_price * (1.0 + slip) if side > 0 else raw_price * (1.0 - slip)

    def _commission(self, shares: float) -> float:
        c = abs(shares) * self.bt.commission_per_share
        return max(c, self.bt.commission_min) if abs(shares) > 0 else 0.0

    # -- main ---------------------------------------------------------------

    def run(self, data: dict[str, pd.DataFrame], strategy: Strategy) -> BacktestResult:
        symbols = sorted(data)
        if not symbols:
            raise ValueError("No data supplied to backtester")

        # Align every symbol onto the timestamps they all share. Using the
        # intersection avoids inventing prices with a forward-fill.
        common = None
        for df in data.values():
            common = df.index if common is None else common.intersection(df.index)
        common = pd.DatetimeIndex(sorted(common))
        if len(common) < 10:
            raise ValueError(
                f"Only {len(common)} overlapping bars across symbols — "
                "check your date range and timeframe."
            )

        opens = pd.DataFrame({s: data[s].loc[common, "open"] for s in symbols})
        closes = pd.DataFrame({s: data[s].loc[common, "close"] for s in symbols})

        # Strategy sees each symbol's full history, then we align the output.
        weights = pd.DataFrame(
            {s: strategy.generate_weights(data[s]).reindex(common).fillna(0.0)
             for s in symbols},
            index=common,
        )

        # Split capital across the universe, then apply the per-name cap.
        slot = min(1.0 / len(symbols), self.risk.max_position_pct)
        weights = weights * slot

        # Never exceed max gross exposure — scale the whole book down if so.
        gross = weights.abs().sum(axis=1)
        over = gross > self.risk.max_gross_exposure
        if over.any():
            weights.loc[over] = weights.loc[over].div(
                gross[over] / self.risk.max_gross_exposure, axis=0
            )

        cash = self.bt.initial_cash
        shares = {s: 0.0 for s in symbols}

        equity_curve: list[float] = []
        cash_curve: list[float] = []
        gross_curve: list[float] = []
        position_rows: list[dict[str, float]] = []
        trade_records: list[dict] = []
        total_costs = 0.0
        total_notional = 0.0
        n_trades = 0
        insolvent = False

        n = len(common)
        for i in range(n):
            ts = common[i]

            # --- Execute yesterday's decision at today's open ---------------
            # weights.iloc[i-1] was computable from bar i-1's close.
            if i > 0:
                # Mark to the open so sizing uses a price we can actually trade.
                mark_open = float(
                    sum(shares[s] * opens.iat[i, j] for j, s in enumerate(symbols))
                )
                equity_at_open = cash + mark_open
                targets = weights.iloc[i - 1]

                # Insolvency guard. Latches — once wiped out, stay wiped out.
                #
                # Every position is sized as target_val = target_w * equity.
                # If equity goes non-positive that product FLIPS SIGN, so a
                # long-only strategy starts opening shorts.
                #
                # On the damage, stated accurately: without this guard the
                # engine places ONE inverted order on the ruin bar, consuming
                # the remaining cash, and the cash guard blocks every buy after
                # that. So the loss is bounded — an audit measured one trade
                # and ~100 of equity, not an unbounded runaway. The guard earns
                # its place on the behavioural rule (a wiped-out account does
                # not trade), not on the size of the number it saves. An
                # earlier version of this comment claimed "-158 shares, 1.70x
                # gross, CAGR nan" for the *unguarded* case; that was measured
                # before the cash guard existed and no longer holds.
                #
                # The latch matters. This test used to re-run every bar with
                # `insolvent` only suppressing duplicate log lines — so if
                # equity clawed back above zero the engine would resume, while
                # the log said "halting for the rest of the run". Now the
                # message is true.
                if insolvent or equity_at_open <= 0:
                    if not insolvent:
                        log.error(
                            "INSOLVENT at %s (equity %.2f) — halting all "
                            "trading for the rest of the run", ts, equity_at_open
                        )
                        insolvent = True
                    targets = None

                for j, s in enumerate(symbols if targets is not None else []):
                    px = float(opens.iat[i, j])
                    if not np.isfinite(px) or px <= 0:
                        continue

                    current_val = shares[s] * px
                    current_w = current_val / equity_at_open if equity_at_open > 0 else 0.0
                    target_w = float(targets[s])

                    if abs(target_w - current_w) < self.rebalance_threshold:
                        continue

                    target_val = target_w * equity_at_open
                    delta_val = target_val - current_val
                    if abs(delta_val) < self.risk.min_order_notional:
                        continue

                    side = 1 if delta_val > 0 else -1
                    fill_px = self._fill_price(px, side)

                    # Size in SHARES against the reference price px, NOT the
                    # slipped fill price.
                    #
                    # Bug this fixes: dividing delta_val by fill_px made sells
                    # overshoot (fill_px < px for a sell, so delta_val/fill_px
                    # is a larger share count than intended). That left tiny
                    # negative "stub" positions — a short book inside a
                    # long-only config, on 561 of 1500 bars. Equity still
                    # netted out correctly, which is precisely why every
                    # existing test passed. Found by adversarial audit.
                    delta_shares = delta_val / px

                    # Don't let a buy overdraw the account. Commission must be
                    # inside the affordability check: otherwise cash can land
                    # a hair below zero, and from then on max(cash, 0.0)
                    # silently blocks every future buy.
                    if side > 0:
                        per_share = fill_px + self.bt.commission_per_share
                        max_affordable = (max(cash, 0.0) / per_share
                                          if per_share > 0 else 0.0)
                        delta_shares = min(delta_shares, max_affordable)
                        if delta_shares <= 0:
                            continue

                    commission = self._commission(delta_shares)
                    trade_cost = delta_shares * fill_px + commission

                    # Guard BOTH sides. An earlier version checked `side > 0`
                    # only, which left sells unprotected: for a sell,
                    # trade_cost = -proceeds + commission, so a commission
                    # larger than the proceeds is a net cash OUTFLOW. With
                    # commission_min=500 on a $200 position, a single "sell"
                    # took cash from 10,000 to 9,006.
                    #
                    # For a normal sell trade_cost is negative, so this is a
                    # no-op on the healthy path.
                    if trade_cost > cash:
                        continue

                    cash -= trade_cost
                    shares[s] += delta_shares

                    slippage_cost = abs(delta_shares) * abs(fill_px - px)
                    total_costs += commission + slippage_cost
                    total_notional += abs(delta_shares * fill_px)
                    n_trades += 1

                    trade_records.append({
                        "timestamp": ts, "symbol": s,
                        "side": "buy" if side > 0 else "sell",
                        "shares": delta_shares, "price": fill_px,
                        "ref_price": px, "commission": commission,
                        "slippage": slippage_cost,
                        "target_w": target_w, "prev_w": current_w,
                    })

            # --- Mark the book to today's close -----------------------------
            # Net value drives equity; GROSS value (sum of absolute position
            # values) drives the exposure metric. Using abs(net) understates a
            # long/short book to ~0 exposure when it is actually 2x gross.
            position_value = float(
                sum(shares[s] * closes.iat[i, j] for j, s in enumerate(symbols))
            )
            gross_value = float(
                sum(abs(shares[s] * closes.iat[i, j]) for j, s in enumerate(symbols))
            )
            equity = cash + position_value

            equity_curve.append(equity)
            cash_curve.append(cash)
            gross_curve.append(gross_value / equity if equity > 0 else 0.0)
            position_rows.append(dict(shares))

        equity_s = pd.Series(equity_curve, index=common, name="equity")
        exposure_s = pd.Series(gross_curve, index=common, name="exposure")

        periods = _periods_per_year(self.cfg.data.timeframe)
        metrics = compute_metrics(
            equity_s, exposure=exposure_s, n_trades=n_trades,
            total_costs=total_costs, periods_per_year=periods,
            total_notional=total_notional,
        )

        trades_df = pd.DataFrame(trade_records)
        if not trades_df.empty:
            trades_df = trades_df.set_index("timestamp")

        log.info("backtest complete: %d bars, %d trades, final equity %.2f",
                 n, n_trades, equity_s.iloc[-1])

        return BacktestResult(
            equity=equity_s,
            positions=pd.DataFrame(position_rows, index=common),
            target_weights=weights,
            trades=trades_df,
            metrics=metrics,
            cash=pd.Series(cash_curve, index=common, name="cash"),
            exposure=exposure_s,
        )


def _periods_per_year(timeframe: str) -> int:
    return {
        "1Day": 252,
        "1Hour": 252 * 7,
        "30Min": 252 * 13,
        "15Min": 252 * 26,
        "5Min": 252 * 78,
        "1Min": 252 * 390,
    }.get(timeframe, 252)


# ---------------------------------------------------------------------------
# Validation tools — these are what separate a real project from a toy
# ---------------------------------------------------------------------------

def walk_forward(
    cfg: Config,
    data: dict[str, pd.DataFrame],
    strategy_name: str,
    param_grid: list[dict],
    n_splits: int = 4,
) -> pd.DataFrame:
    """Anchored walk-forward: optimise on everything up to time T, then score
    the winning parameters on the *next*, never-seen window. Slide T forward
    and repeat.

    Why this matters: picking the best parameters over your whole sample and
    reporting that result is not a backtest, it's a lookup. Walk-forward is
    the cheapest honest alternative. If out-of-sample Sharpe collapses versus
    in-sample, your "edge" was curve fitting.

    Caveat worth knowing: the training windows overlap between folds, so the
    folds are not independent and the mean OOS Sharpe still overstates things
    somewhat. It is a sanity check, not a proof.
    """
    from .strategies import build_strategy

    n_bars = len(next(iter(data.values())).index)
    fold_size = n_bars // (n_splits + 1)
    if fold_size < 50:
        raise ValueError(
            f"Need at least {50 * (n_splits + 1)} bars for {n_splits} splits, "
            f"have {n_bars}. Use fewer splits or a longer date range."
        )

    rows = []
    for k in range(n_splits):
        # Train on [0, train_end), test on [train_end, test_end). No overlap
        # between a fold's own train and test windows — that is the point.
        train_end = fold_size * (k + 1)
        test_end = min(fold_size * (k + 2), n_bars)

        train_data = {s: df.iloc[0:train_end] for s, df in data.items()}
        test_data = {s: df.iloc[train_end:test_end] for s, df in data.items()}

        # Selection must be deterministic ACROSS MACHINES, not just across
        # runs. Two parameter sets whose Sharpes differ in the 15th decimal are
        # a tie in every sense that matters, but `>` resolves that tie by
        # whichever side floating point happened to land on — and libm/BLAS
        # differ between platforms. A published walk-forward table generated on
        # Linux therefore failed to reproduce on Windows: fold 4 selected
        # fast=5 on one and fast=10 on the other, changing every number in the
        # row. Round the comparison, then break exact ties on a stable key.
        #
        # 1e-3, NOT 1e-9. The first fix used 1e-9, which is six orders of
        # magnitude below both measured cross-platform noise (~0.001-0.01 on
        # these Sharpes) and real selection margins (0.0037-0.14) — so
        # platform noise still flipped fold selection, and the snapshot kept
        # failing to reproduce across OSes. 1e-3 matches the precision the
        # table itself is rounded to: differences the reader cannot see are
        # ties by definition.
        TIE = 1e-3
        best_params, best_sharpe = None, -np.inf
        for params in param_grid:
            # Note: the test window starts cold, so the strategy sits flat for
            # its warmup period at the start of each fold. That drags OOS
            # results down slightly. The alternative — feeding in training
            # bars as warmup — would leak in-sample returns into the OOS
            # equity curve, which is the worse sin. We take the conservative
            # bias on purpose.
            try:
                r = Backtester(cfg).run(train_data, build_strategy(strategy_name, **params))
            except Exception:
                continue
            sharpe = r.metrics.sharpe
            if sharpe > best_sharpe + TIE:
                best_sharpe, best_params = sharpe, params
            elif abs(sharpe - best_sharpe) <= TIE and best_params is not None:
                # Exact-ish tie: prefer the lexicographically smaller parameter
                # set. Arbitrary, but identical on every machine.
                if sorted(params.items()) < sorted(best_params.items()):
                    best_sharpe, best_params = sharpe, params

        if best_params is None:
            continue

        oos = Backtester(cfg).run(test_data, build_strategy(strategy_name, **best_params))

        # Run buy-and-hold on the SAME test window.
        #
        # Without this the table reports an out-of-sample Sharpe with nothing
        # to compare it to, so a number like 0.52 reads as a pass — when the
        # benchmark on that same window scored 0.56 and the strategy actually
        # lost. An unbenchmarked Sharpe is not a result. Found by audit.
        oos_bench = Backtester(cfg).run(test_data, build_strategy("buy_and_hold"))

        rows.append({
            "fold": k + 1,
            "params": best_params,
            "in_sample_sharpe": round(best_sharpe, 3),
            "oos_sharpe": round(oos.metrics.sharpe, 3),
            "oos_benchmark_sharpe": round(oos_bench.metrics.sharpe, 3),
            "oos_edge": round(oos.metrics.sharpe - oos_bench.metrics.sharpe, 3),
            "oos_return": round(oos.metrics.total_return, 4),
            "oos_maxdd": round(oos.metrics.max_drawdown, 4),
            "oos_trades": oos.metrics.n_trades,
        })

    return pd.DataFrame(rows)


def noise_test(
    cfg: Config,
    strategy_name: str,
    params: dict | None = None,
    n_trials: int = 40,
    n_bars: int = 1500,
) -> dict:
    """Run the strategy against many independent random-walk universes.

    This answers the question single backtests cannot: **is this result
    distinguishable from luck?**

    Random walks contain no exploitable structure. So the distribution of
    "strategy Sharpe minus buy-and-hold Sharpe" across many synthetic
    universes is your null distribution — the spread of outcomes you get from
    noise alone. A single real-data backtest that lands inside this spread has
    told you nothing.

    Two people running one synthetic backtest each can easily see -0.7 and
    +0.4 and draw opposite conclusions. Both are sampling the same noise.
    """
    from .data import synthetic_universe
    from .strategies import build_strategy

    params = params or {}
    deltas, strat_sharpes, bench_sharpes = [], [], []

    for trial in range(n_trials):
        # Correlated basket, not independent paths — see synthetic_universe.
        # Independent paths diversify away almost all variance and make this
        # null band far too narrow.
        data = synthetic_universe(cfg.symbols, n=n_bars, seed=trial * 1000)
        try:
            s = Backtester(cfg).run(data, build_strategy(strategy_name, **params))
            b = Backtester(cfg).run(data, build_strategy("buy_and_hold"))
        except Exception as exc:
            log.warning("noise trial %d failed: %s", trial, exc)
            continue

        strat_sharpes.append(s.metrics.sharpe)
        bench_sharpes.append(b.metrics.sharpe)
        deltas.append(s.metrics.sharpe - b.metrics.sharpe)

    d = np.array(deltas)
    if d.size == 0:
        raise RuntimeError("All noise trials failed")

    return {
        "n_trials": int(d.size),
        "mean_delta": float(d.mean()),
        "std_delta": float(d.std(ddof=1)) if d.size > 1 else 0.0,
        "min_delta": float(d.min()),
        "max_delta": float(d.max()),
        "p5": float(np.percentile(d, 5)),
        "p95": float(np.percentile(d, 95)),
        "win_rate": float((d > 0).mean()),
        "mean_strategy_sharpe": float(np.mean(strat_sharpes)),
        "mean_benchmark_sharpe": float(np.mean(bench_sharpes)),
    }


def parameter_sensitivity(
    cfg: Config,
    data: dict[str, pd.DataFrame],
    strategy_name: str,
    param_grid: list[dict],
) -> pd.DataFrame:
    """Score every parameter set on the full sample.

    Read the *shape*, not the peak. A robust strategy shows a broad plateau of
    decent results. A single spiking cell surrounded by losses is noise you
    happened to fit.
    """
    from .strategies import build_strategy

    rows = []
    for params in param_grid:
        try:
            r = Backtester(cfg).run(data, build_strategy(strategy_name, **params))
        except Exception as exc:
            log.debug("skipping %s: %s", params, exc)
            continue
        rows.append({
            **params,
            "sharpe": round(r.metrics.sharpe, 3),
            "cagr": round(r.metrics.cagr, 4),
            "max_dd": round(r.metrics.max_drawdown, 4),
            "trades": r.metrics.n_trades,
        })
    return pd.DataFrame(rows).sort_values("sharpe", ascending=False)
