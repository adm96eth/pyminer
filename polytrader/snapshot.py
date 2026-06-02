"""Serialize market+book snapshots to JSONL "frames" and back.

A frame is one line of JSON capturing every scanned market with its full order
books at a moment in time:

    {"ts": 1700000000.0, "markets": [
        {"condition_id": "...", "question": "...", "tokens": [
            {"token_id": "...", "outcome": "Yes",
             "asks": [[0.45, 100], ...], "bids": [[0.44, 80], ...]}]}]}

Recording these live lets you replay an identical sequence through the strategy
offline (see ``backtest.py``) -- so you can tune thresholds against real books
without re-fetching or risking funds.
"""

from __future__ import annotations

import json
import time
from typing import Dict, Iterator, List, Optional

from .models import Market, OrderBook, OrderLevel, Token


def _book_to_obj(book: Optional[OrderBook]) -> dict:
    if book is None:
        return {"asks": [], "bids": []}
    return {
        "asks": [[lvl.price, lvl.size] for lvl in book.asks],
        "bids": [[lvl.price, lvl.size] for lvl in book.bids],
    }


def market_to_obj(m: Market) -> dict:
    return {
        "condition_id": m.condition_id,
        "question": m.question,
        "closed": m.closed,
        "enable_order_book": m.enable_order_book,
        "tokens": [
            {"token_id": t.token_id, "outcome": t.outcome, **_book_to_obj(t.book)}
            for t in m.tokens
        ],
    }


def market_from_obj(obj: dict) -> Market:
    tokens = []
    for t in obj.get("tokens", []):
        book = OrderBook(
            token_id=str(t["token_id"]),
            asks=[OrderLevel(float(p), float(s)) for p, s in t.get("asks", [])],
            bids=[OrderLevel(float(p), float(s)) for p, s in t.get("bids", [])],
        )
        tokens.append(Token(token_id=str(t["token_id"]), outcome=t.get("outcome", ""), book=book))
    return Market(
        condition_id=str(obj["condition_id"]),
        question=obj.get("question", ""),
        tokens=tokens,
        closed=bool(obj.get("closed", False)),
        enable_order_book=bool(obj.get("enable_order_book", True)),
    )


class SnapshotRecorder:
    """Appends one frame per call to a JSONL file."""

    def __init__(self, path: str):
        self.path = path

    def record(self, markets: List[Market], ts: Optional[float] = None) -> None:
        frame = {
            "ts": round(ts if ts is not None else time.time(), 3),
            "markets": [market_to_obj(m) for m in markets],
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(frame, separators=(",", ":")) + "\n")
            f.flush()


def read_frames(path: str) -> Iterator[Dict]:
    """Yield ``(ts, [Market, ...])`` for each frame in a snapshot file."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            markets = [market_from_obj(m) for m in obj.get("markets", [])]
            yield {"ts": obj.get("ts"), "markets": markets}
