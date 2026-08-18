#!/usr/bin/env python3
"""Run a backtest and print an honest report.

Examples
--------
    # No API keys needed — synthetic data, proves the pipeline works
    python scripts/run_backtest.py --synthetic

    # Real data (needs free Alpaca keys in .env)
    python scripts/run_backtest.py

    # Compare against buy-and-hold, which is the only comparison that matters
    python scripts/run_backtest.py --synthetic --benchmark

    # Is the parameter choice robust, or did you just get lucky?
    python scripts/run_backtest.py --synthetic --sensitivity

    # Does it survive out-of-sample?
    python scripts/run_backtest.py --synthetic --walk-forward
"""

from __future__ import annotations

import argparse
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.backtest import (Backtester, noise_test, parameter_sensitivity,
                          walk_forward)
from src.config import load_config
from src.data import get_universe
from src.data_quality import audit_universe, format_report
from src.logging_setup import setup_logging
from src.reporting import (format_noise_test, format_walk_forward,
                           noise_test_verdict)
from src.strategies import build_strategy

PARAM_GRIDS = {
    "sma_cross": [
        {"fast": f, "slow": s}
        for f, s in product([5, 10, 20, 50], [50, 100, 150, 200])
        if f < s
    ],
    "mean_reversion": [
        {"lookback": lb, "entry_z": z}
        for lb, z in product([10, 20, 30, 60], [0.5, 1.0, 1.5, 2.0])
    ],
    "buy_and_hold": [{}],
}


def _plot(result, benchmark, out_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    eq = result.equity
    ax1.plot(eq.index, eq.values, label="Strategy", linewidth=1.6)
    if benchmark is not None:
        b = benchmark.equity
        ax1.plot(b.index, b.values, label="Buy & hold",
                 linewidth=1.2, alpha=0.7, linestyle="--")
    ax1.set_ylabel("Equity ($)")
    ax1.set_title("Backtest — equity curve and drawdown")
    ax1.legend()
    ax1.grid(alpha=0.25)

    dd = eq / eq.cummax() - 1.0
    ax2.fill_between(dd.index, dd.values, 0, alpha=0.4, color="crimson")
    ax2.set_ylabel("Drawdown")
    ax2.set_xlabel("Date")
    ax2.grid(alpha=0.25)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--strategy", help="Override config.yaml strategy name")
    p.add_argument("--synthetic", action="store_true",
                   help="Use generated random-walk data (no API keys required)")
    p.add_argument("--benchmark", action="store_true",
                   help="Also run buy-and-hold for comparison")
    p.add_argument("--sensitivity", action="store_true",
                   help="Score a grid of parameters on the full sample")
    p.add_argument("--walk-forward", action="store_true",
                   help="Anchored walk-forward out-of-sample test")
    p.add_argument("--noise-test", action="store_true",
                   help="Run against many random-walk universes to see the "
                        "range of results luck alone produces")
    p.add_argument("--trials", type=int, default=40,
                   help="Number of universes for --noise-test (default 40)")
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--source", choices=["stooq", "alpaca"],
                   help="Data provider (default from config.yaml). "
                        "stooq needs no API key.")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore the on-disk cache and refetch")
    p.add_argument("--skip-audit", action="store_true",
                   help="Skip the data-quality report on real data")
    p.add_argument("--allow-partial", action="store_true",
                   help="Proceed even if some symbols fail to load "
                        "(changes the universe — say so if you quote the result)")
    args = p.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    if args.source:
        cfg.data.source = args.source
    name = args.strategy or cfg.strategy_name

    if args.synthetic:
        print("\n*** SYNTHETIC DATA — random walks have no edge to find. ***")
        print("*** Use this to verify plumbing, never to judge a strategy. ***")
    else:
        print(f"\nReal market data via {cfg.data.source}.")

    data = get_universe(cfg, synthetic=args.synthetic,
                        use_cache=not args.no_cache,
                        allow_partial=args.allow_partial)
    print(f"\nLoaded {len(data)} symbols, "
          f"{min(len(d) for d in data.values())}–{max(len(d) for d in data.values())} bars each")

    # Real bars can contain things a generator cannot produce. Run the audit
    # BEFORE the backtest — an unadjusted split reads as a -75% crash and
    # manufactures profit that no test would flag.
    if not args.synthetic and not args.skip_audit:
        reports, cross = audit_universe(data)
        print(format_report(reports, cross))
        if any(r.errors for r in reports):
            print("\n  Continuing anyway — but treat every number below as "
                  "suspect until the errors above are resolved.")

    strategy = build_strategy(name, **(cfg.strategy_params if not args.strategy else {}))
    result = Backtester(cfg).run(data, strategy)

    print(f"\n{'=' * 60}\n  STRATEGY: {strategy}\n{'=' * 60}")
    print(result.summary())

    benchmark = None
    if args.benchmark:
        benchmark = Backtester(cfg).run(data, build_strategy("buy_and_hold"))
        print(f"\n{'=' * 60}\n  BENCHMARK: buy & hold\n{'=' * 60}")
        print(benchmark.summary())

        delta = result.metrics.sharpe - benchmark.metrics.sharpe
        verdict = "beats" if delta > 0 else "LOSES TO"
        print(f"\n  >> Strategy {verdict} buy & hold on Sharpe by {delta:+.2f}")
        if delta <= 0:
            print("  >> A strategy that can't beat holding isn't worth the costs "
                  "or the complexity.")

    if args.sensitivity:
        grid = PARAM_GRIDS.get(name, [{}])
        print(f"\n{'=' * 60}\n  PARAMETER SENSITIVITY ({len(grid)} combos)\n{'=' * 60}")
        table = parameter_sensitivity(cfg, data, name, grid)
        print(table.head(15).to_string(index=False))
        if len(table) > 1:
            spread = table["sharpe"].max() - table["sharpe"].min()
            print(f"\n  Sharpe range across parameters: {spread:.2f}")
            print("  Wide range + one standout cell = overfitting. "
                  "Look for a broad plateau instead.")

    if args.walk_forward:
        grid = PARAM_GRIDS.get(name, [{}])
        print(f"\n{'=' * 60}\n  WALK-FORWARD (out of sample)\n{'=' * 60}")
        try:
            wf = walk_forward(cfg, data, name, grid)
            print(format_walk_forward(wf, len(grid)))
        except ValueError as exc:
            print(f"  Skipped: {exc}")

    if args.noise_test:
        print(f"\n{'=' * 60}\n  NOISE TEST — {args.trials} random-walk universes\n{'=' * 60}")
        print("  Random walks have no edge in them. This is the range of\n"
              "  results LUCK ALONE produces for this strategy.\n")
        nt = noise_test(cfg, name,
                        params=cfg.strategy_params if not args.strategy else {},
                        n_trials=args.trials)
        print(format_noise_test(nt))
        print(noise_test_verdict(nt))

    if not args.no_plot:
        out = cfg.path("reports/equity_curve.png")
        if _plot(result, benchmark, out):
            print(f"\n  Chart saved to {out.relative_to(cfg.path('.'))}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
