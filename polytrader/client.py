"""Read-only Polymarket data access (markets + order books).

Uses public REST endpoints only -- no auth needed for reads:
  * Gamma API  -> market metadata + outcome token ids
  * CLOB  /book -> live order books per token

Kept separate from execution so price discovery never depends on having keys.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from .models import Market, OrderBook, Token


class PolymarketData:
    def __init__(self, clob_host: str, gamma_host: str, timeout: float = 10.0, session=None):
        self.clob_host = clob_host.rstrip("/")
        self.gamma_host = gamma_host.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests  # lazy so importing the module needs no deps

            session = requests.Session()
            session.headers.update({"User-Agent": "polytrader/0.1"})
        self._session = session

    def _get(self, url: str, params: Optional[dict] = None):
        r = self._session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_active_markets(self, limit: int = 50) -> List[Market]:
        """Active, order-book-enabled, non-closed markets from Gamma."""
        data = self._get(
            f"{self.gamma_host}/markets",
            params={"active": "true", "closed": "false", "limit": limit},
        )
        rows = data if isinstance(data, list) else data.get("data", [])
        markets: List[Market] = []
        for row in rows:
            m = self._parse_market(row)
            if m and m.enable_order_book and not m.closed and len(m.tokens) >= 2:
                markets.append(m)
        return markets

    @staticmethod
    def _parse_market(row: dict) -> Optional[Market]:
        # Gamma encodes some fields as JSON strings.
        def maybe_json(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    return v
            return v

        token_ids = maybe_json(row.get("clobTokenIds")) or []
        outcomes = maybe_json(row.get("outcomes")) or []
        if not token_ids:
            return None
        tokens = [
            Token(token_id=str(tid), outcome=str(outcomes[i]) if i < len(outcomes) else f"OUT{i}")
            for i, tid in enumerate(token_ids)
        ]
        return Market(
            condition_id=str(row.get("conditionId") or row.get("condition_id") or row.get("id")),
            question=str(row.get("question", "")),
            tokens=tokens,
            closed=bool(row.get("closed", False)),
            enable_order_book=bool(row.get("enableOrderBook", True)),
        )

    def fetch_book(self, token_id: str) -> OrderBook:
        raw = self._get(f"{self.clob_host}/book", params={"token_id": token_id})
        return OrderBook.from_clob(token_id, raw)

    def hydrate_books(self, markets: List[Market]) -> Dict[str, OrderBook]:
        """Fetch and attach order books for every token; return id->book map.

        On a per-token fetch error the token's book is left as None so the
        strategy simply skips that market rather than crashing the loop.
        """
        books: Dict[str, OrderBook] = {}
        for m in markets:
            for t in m.tokens:
                try:
                    book = self.fetch_book(t.token_id)
                    t.book = book
                    books[t.token_id] = book
                except Exception:
                    t.book = None
        return books
