"""Execution backends. Same interface, swappable via the MODE switch."""

from .base import Broker, Fill, Side
from .paper import PaperBroker

__all__ = ["Broker", "Fill", "Side", "PaperBroker"]
