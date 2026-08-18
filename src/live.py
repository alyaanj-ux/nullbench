"""The live (paper) trading loop.

Flow, once per poll interval:
  1. Is the market open? If not, sleep.
  2. Have we breached the daily loss kill switch? If so, flatten and stop.
  3. Pull recent bars for each symbol (enough to cover the strategy warmup).
  4. Ask the strategy for the latest target weight.
  5. Diff against actual positions; submit orders for meaningful gaps only.
  6. Log everything.

Step 6 is not optional. The gap between this log and your backtest is the
real information this project produces.
"""

from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .broker import AlpacaBroker
from .config import Config, load_config
from .data import get_bars
from .logging_setup import get_logger
from .strategies import build_strategy

log = get_logger("live")


class LiveTrader:
    def __init__(self, cfg: Config, dry_run: bool | None = None):
        self.cfg = cfg
        self.strategy = build_strategy(cfg.strategy_name, **cfg.strategy_params)
        self.broker = AlpacaBroker(cfg, dry_run=dry_run)
        self.rebalance_threshold = 0.05
        self._stop = False
        self._start_equity: float | None = None
        self.state_path = cfg.path(cfg.live.state_file)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, *_):
        log.info("shutdown signal received — finishing this cycle then exiting")
        self._stop = True

    # -----------------------------------------------------------------------

    def target_weights(self) -> dict[str, float]:
        """Latest desired weight per symbol, already scaled by the risk caps."""
        slot = min(1.0 / len(self.cfg.symbols), self.cfg.risk.max_position_pct)
        out: dict[str, float] = {}

        for sym in self.cfg.symbols:
            try:
                # Bypass the cache in live mode — we need fresh bars.
                bars = get_bars(self.cfg, sym, use_cache=False)
                if len(bars) < max(self.strategy.warmup, 2):
                    log.warning("%s: only %d bars, need %d — skipping",
                                sym, len(bars), self.strategy.warmup)
                    continue
                w = float(self.strategy.generate_weights(bars).iloc[-1])
                out[sym] = w * slot
            except Exception as exc:
                log.error("signal generation failed for %s: %s", sym, exc)

        gross = sum(abs(v) for v in out.values())
        if gross > self.cfg.risk.max_gross_exposure and gross > 0:
            scale = self.cfg.risk.max_gross_exposure / gross
            out = {k: v * scale for k, v in out.items()}
        return out

    def rebalance(self) -> None:
        account = self.broker.get_account()
        positions = self.broker.get_positions()

        if self._start_equity is None:
            self._start_equity = account.last_equity or account.equity

        # --- kill switch ---------------------------------------------------
        daily_pl = account.daily_pl_pct
        if daily_pl < -self.cfg.risk.max_daily_loss_pct:
            log.error("KILL SWITCH: down %.2f%% today (limit %.2f%%). Flattening.",
                      daily_pl * 100, self.cfg.risk.max_daily_loss_pct * 100)
            for sym in positions:
                self.broker.close_position(sym)
            self._stop = True
            return

        equity = account.equity
        targets = self.target_weights()
        log.info("equity=%.2f  day P/L=%+.2f%%  targets=%s",
                 equity, daily_pl * 100,
                 {k: round(v, 3) for k, v in targets.items()})

        for sym, target_w in targets.items():
            current_val = positions[sym].market_value if sym in positions else 0.0
            current_w = current_val / equity if equity > 0 else 0.0
            drift = target_w - current_w

            if abs(drift) < self.rebalance_threshold:
                continue

            delta_notional = drift * equity
            if abs(delta_notional) < self.cfg.risk.min_order_notional:
                continue

            # Closing out entirely is cleaner than selling a computed notional,
            # which can leave dust behind from price movement between the
            # quote and the fill.
            if target_w <= 0 and sym in positions:
                self.broker.close_position(sym)
                continue

            self.broker.submit_order(
                symbol=sym,
                notional=abs(delta_notional),
                side="buy" if delta_notional > 0 else "sell",
            )

        self._save_state(account, targets)

    def _save_state(self, account, targets: dict[str, float]) -> None:
        state = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "equity": account.equity,
            "cash": account.cash,
            "daily_pl_pct": account.daily_pl_pct,
            "targets": targets,
            "strategy": repr(self.strategy),
            "dry_run": self.broker.dry_run,
        }
        self.state_path.write_text(json.dumps(state, indent=2, default=str))

    # -----------------------------------------------------------------------

    def run(self) -> None:
        mode = "DRY RUN" if self.broker.dry_run else "PAPER"
        log.info("starting live loop | mode=%s | strategy=%s | universe=%s",
                 mode, self.strategy, self.cfg.symbols)

        while not self._stop:
            try:
                if self.broker.is_market_open():
                    self.rebalance()
                else:
                    log.info("market closed — sleeping")
            except Exception as exc:
                # Never let one bad cycle kill a long-running process.
                log.exception("cycle failed, continuing: %s", exc)

            for _ in range(self.cfg.live.poll_seconds):
                if self._stop:
                    break
                time.sleep(1)

        log.info("live loop stopped")


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Run the paper-trading loop")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--live-orders", action="store_true",
                   help="Actually submit orders to the PAPER account "
                        "(without this flag, orders are only logged)")
    p.add_argument("--once", action="store_true",
                   help="Run a single rebalance cycle and exit")
    args = p.parse_args()

    cfg = load_config(args.config)
    trader = LiveTrader(cfg, dry_run=not args.live_orders)

    if args.once:
        trader.rebalance()
    else:
        trader.run()


if __name__ == "__main__":
    main()
