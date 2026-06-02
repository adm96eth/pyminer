"""Append-only trade journal: structured JSONL events + a flat CSV of trades.

Every meaningful thing the engine does emits an event here so you have an
auditable record separate from the log stream. JSONL keeps full structure (one
JSON object per line); the CSV is a convenience flattening of executed trades
for spreadsheets.

Writes are append + flush per event so a crash mid-run still leaves a complete
record up to the last action.
"""

from __future__ import annotations

import csv
import json
import os
import time
from typing import Optional

from .models import ArbOpportunity

# event types
DETECTED = "opportunity_detected"
EXECUTED = "trade_executed"
SKIPPED = "opportunity_skipped"
MERGED = "set_merged"
UNWOUND = "leg_unwound"
HALT = "halt"

_CSV_FIELDS = [
    "ts", "event", "condition_id", "question", "size", "set_cost",
    "edge_per_set", "total_edge", "realized_pnl", "detail",
]


class TradeJournal:
    def __init__(self, jsonl_path: Optional[str] = None, csv_path: Optional[str] = None):
        self.jsonl_path = jsonl_path
        self.csv_path = csv_path
        self._csv_header_written = False
        if csv_path and os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            self._csv_header_written = True

    # -- low level -----------------------------------------------------------
    def _write_jsonl(self, record: dict) -> None:
        if not self.jsonl_path:
            return
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
            f.flush()

    def _write_csv(self, record: dict) -> None:
        if not self.csv_path:
            return
        row = {k: record.get(k, "") for k in _CSV_FIELDS}
        with open(self.csv_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            if not self._csv_header_written:
                w.writeheader()
                self._csv_header_written = True
            w.writerow(row)
            f.flush()

    def emit(self, event: str, *, csv_row: bool = False, **fields) -> dict:
        record = {"ts": round(time.time(), 3), "event": event, **fields}
        self._write_jsonl(record)
        if csv_row:
            self._write_csv(record)
        return record

    # -- typed helpers -------------------------------------------------------
    @staticmethod
    def _opp_fields(opp: ArbOpportunity) -> dict:
        return {
            "condition_id": opp.market.condition_id,
            "question": opp.market.question,
            "size": round(opp.size, 4),
            "set_cost": round(opp.set_cost, 6),
            "edge_per_set": round(opp.edge_per_set, 6),
            "total_edge": round(opp.total_edge, 4),
        }

    def detected(self, opp: ArbOpportunity) -> None:
        self.emit(DETECTED, **self._opp_fields(opp))

    def skipped(self, opp: ArbOpportunity, reason: str) -> None:
        self.emit(SKIPPED, detail=reason, **self._opp_fields(opp))

    def executed(self, opp: ArbOpportunity, approved_size: float, spent: float) -> None:
        f = self._opp_fields(opp)
        f["size"] = round(approved_size, 4)
        self.emit(EXECUTED, csv_row=True, detail=f"spent=${spent:.4f}", **f)

    def merged(self, opp: ArbOpportunity, credited: float, realized_pnl: float) -> None:
        f = self._opp_fields(opp)
        self.emit(MERGED, csv_row=True, realized_pnl=round(realized_pnl, 4),
                  detail=f"credited=${credited:.4f}", **f)

    def unwound(self, token_id: str, detail: str) -> None:
        self.emit(UNWOUND, condition_id="", detail=f"{token_id}: {detail}")

    def halted(self, reason: str) -> None:
        self.emit(HALT, detail=reason)
