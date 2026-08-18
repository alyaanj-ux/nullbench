"""Console + rotating-file logging, and a JSONL trade log.

The JSONL trade log matters more than it looks: comparing your live fills
against what the backtest *said* would happen is the only way to find out
whether your cost model is honest.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

_CONFIGURED = False


def setup_logging(level: int = logging.INFO, log_dir: str = "logs") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("nullbench")
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    d = PROJECT_ROOT / log_dir
    d.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(d / "nullbench.log", maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _CONFIGURED = True
    return logger


def get_logger(name: str = "nullbench") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name if name.startswith("nullbench") else f"nullbench.{name}")


class TradeLog:
    """Append-only newline-delimited JSON of every order we intended or placed."""

    def __init__(self, path: str = "logs/trades.jsonl"):
        self.path = PROJECT_ROOT / path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields: Any) -> None:
        fields.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with open(self.path, "a") as fh:
            fh.write(json.dumps(fields, default=str) + "\n")
