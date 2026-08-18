"""Thin wrapper around Alpaca's trading API, plus a paper-safe dry-run mode.

Everything that touches money goes through this file. Two safety rails:

  * `dry_run=True` logs the order it *would* have sent and returns a fake
    acknowledgement. This is the default.
  * The client refuses to construct itself against the live endpoint unless
    ALPACA_PAPER is explicitly set to "false".
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .logging_setup import TradeLog, get_logger

log = get_logger("broker")


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    last_equity: float

    @property
    def daily_pl_pct(self) -> float:
        if self.last_equity <= 0:
            return 0.0
        return self.equity / self.last_equity - 1.0


class AlpacaBroker:
    def __init__(self, cfg: Config, dry_run: bool | None = None):
        self.cfg = cfg
        self.dry_run = cfg.live.dry_run if dry_run is None else dry_run
        self.trade_log = TradeLog()
        self._client = None

        if not cfg.creds.paper:
            log.warning(
                "=" * 68 + "\n"
                "  ALPACA_PAPER is false — this would trade REAL MONEY.\n"
                "  Refusing to proceed. Set ALPACA_PAPER=true in your .env.\n"
                + "=" * 68
            )
            raise RuntimeError("Live trading is disabled in this project by design.")

    @property
    def client(self):
        """Lazily construct the SDK client so import never requires keys."""
        if self._client is None:
            from alpaca.trading.client import TradingClient

            if not self.cfg.creds.is_configured:
                raise RuntimeError(
                    "Missing Alpaca keys. Copy .env.example to .env and fill it in."
                )
            self._client = TradingClient(
                self.cfg.creds.api_key,
                self.cfg.creds.secret_key,
                paper=True,  # hard-coded. Do not parameterise this.
            )
        return self._client

    # -- reads --------------------------------------------------------------

    def get_account(self) -> Account:
        a = self.client.get_account()
        return Account(
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            last_equity=float(a.last_equity),
        )

    def get_positions(self) -> dict[str, Position]:
        out = {}
        for p in self.client.get_all_positions():
            out[p.symbol] = Position(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
            )
        return out

    def is_market_open(self) -> bool:
        try:
            return bool(self.client.get_clock().is_open)
        except Exception as exc:
            log.error("could not fetch market clock: %s", exc)
            return False

    # -- writes -------------------------------------------------------------

    def submit_order(self, symbol: str, notional: float | None = None,
                     qty: float | None = None, side: str = "buy") -> dict:
        """Submit a market day order. Prefer `notional` — it lets Alpaca handle
        fractional shares and avoids rounding a $10 account into no position.
        """
        if (notional is None) == (qty is None):
            raise ValueError("Pass exactly one of notional or qty")

        record = {
            "symbol": symbol, "side": side,
            "notional": notional, "qty": qty,
            "dry_run": self.dry_run,
        }

        if self.dry_run:
            log.info("[DRY RUN] would %s %s %s", side, symbol,
                     f"${notional:.2f}" if notional else f"{qty} sh")
            record["status"] = "dry_run"
            self.trade_log.record(**record)
            return record

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2) if notional is not None else None,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = self.client.submit_order(req)
            record.update({"status": str(order.status), "order_id": str(order.id)})
            log.info("submitted %s %s -> %s", side, symbol, order.status)
        except Exception as exc:
            record.update({"status": "error", "error": str(exc)})
            log.error("order failed for %s: %s", symbol, exc)

        self.trade_log.record(**record)
        return record

    def close_position(self, symbol: str) -> dict:
        if self.dry_run:
            log.info("[DRY RUN] would close %s", symbol)
            return {"symbol": symbol, "status": "dry_run"}
        try:
            self.client.close_position(symbol)
            log.info("closed %s", symbol)
            return {"symbol": symbol, "status": "closed"}
        except Exception as exc:
            log.error("could not close %s: %s", symbol, exc)
            return {"symbol": symbol, "status": "error", "error": str(exc)}
