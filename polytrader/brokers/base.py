"""Broker interface shared by paper and live execution."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Fill:
    """The result of attempting to execute an order."""

    token_id: str
    side: Side
    requested_size: float
    filled_size: float
    avg_price: float          # USDC per token, over the filled portion
    cost: float               # signed USDC: negative = paid out, positive = received
    ok: bool
    detail: str = ""

    @property
    def fully_filled(self) -> bool:
        return self.requested_size > 0 and abs(self.filled_size - self.requested_size) < 1e-9


class Broker(abc.ABC):
    """Minimal execution surface the engine relies on."""

    name: str = "broker"

    @abc.abstractmethod
    def usdc_balance(self) -> float:
        ...

    @abc.abstractmethod
    def buy(self, token_id: str, size: float, max_price: float) -> Fill:
        """Marketable buy up to ``max_price``; never crosses above it."""
        ...

    @abc.abstractmethod
    def sell(self, token_id: str, size: float, min_price: float) -> Fill:
        """Marketable sell down to ``min_price``; never crosses below it."""
        ...
