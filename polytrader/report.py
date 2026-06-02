"""Roll up a JSONL trade journal into P&L and activity stats.

Reads the append-only event log written by :class:`polytrader.journal.TradeJournal`
and aggregates it -- overall, per UTC day, and per market -- with no network and
no dependence on the live engine. Use it to answer "how did the bot actually
do?" after (or during) a run:

    python -m polytrader --report trades.jsonl
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .journal import DETECTED, EXECUTED, HALT, MERGED, SKIPPED, UNWOUND


@dataclass
class DayStats:
    date: str
    detected: int = 0
    executed: int = 0
    merged: int = 0
    skipped: int = 0
    realized_pnl: float = 0.0
    volume: float = 0.0  # USDC notional traded (sum of set_cost * size)


@dataclass
class Report:
    detected: int = 0
    executed: int = 0
    merged: int = 0
    skipped: int = 0
    unwound: int = 0
    halts: int = 0
    realized_pnl: float = 0.0
    volume: float = 0.0
    wins: int = 0
    losses: int = 0
    best_trade: Optional[dict] = None
    worst_trade: Optional[dict] = None
    by_day: Dict[str, DayStats] = field(default_factory=dict)
    by_market: Dict[str, float] = field(default_factory=dict)
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    @property
    def win_rate(self) -> float:
        n = self.wins + self.losses
        return (self.wins / n * 100.0) if n else 0.0

    @property
    def avg_pnl_per_trade(self) -> float:
        return (self.realized_pnl / self.merged) if self.merged else 0.0


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def build_report(path: str) -> Report:
    rep = Report()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a torn last line from a crash

            event = rec.get("event")
            ts = rec.get("ts")
            if ts is not None:
                rep.first_ts = ts if rep.first_ts is None else min(rep.first_ts, ts)
                rep.last_ts = ts if rep.last_ts is None else max(rep.last_ts, ts)
            day = _day(ts) if ts is not None else "unknown"
            d = rep.by_day.setdefault(day, DayStats(date=day))

            if event == DETECTED:
                rep.detected += 1
                d.detected += 1
            elif event == SKIPPED:
                rep.skipped += 1
                d.skipped += 1
            elif event == UNWOUND:
                rep.unwound += 1
            elif event == HALT:
                rep.halts += 1
            elif event == EXECUTED:
                rep.executed += 1
                d.executed += 1
                vol = float(rec.get("set_cost", 0)) * float(rec.get("size", 0))
                rep.volume += vol
                d.volume += vol
            elif event == MERGED:
                rep.merged += 1
                d.merged += 1
                pnl = float(rec.get("realized_pnl", 0) or 0)
                rep.realized_pnl += pnl
                d.realized_pnl += pnl
                rep.by_market[rec.get("condition_id", "?")] = (
                    rep.by_market.get(rec.get("condition_id", "?"), 0.0) + pnl
                )
                if pnl >= 0:
                    rep.wins += 1
                else:
                    rep.losses += 1
                if rep.best_trade is None or pnl > rep.best_trade.get("realized_pnl", float("-inf")):
                    rep.best_trade = rec
                if rep.worst_trade is None or pnl < rep.worst_trade.get("realized_pnl", float("inf")):
                    rep.worst_trade = rec
    return rep


def format_report(rep: Report) -> str:
    lines: List[str] = []
    span = ""
    if rep.first_ts and rep.last_ts:
        span = f" ({_day(rep.first_ts)} -> {_day(rep.last_ts)})"
    lines.append(f"=== polytrader report{span} ===")
    lines.append(
        f"detected={rep.detected} executed={rep.executed} merged={rep.merged} "
        f"skipped={rep.skipped} unwound={rep.unwound} halts={rep.halts}"
    )
    lines.append(
        f"realized PnL: ${rep.realized_pnl:+.2f}  |  volume: ${rep.volume:.2f}  |  "
        f"win rate: {rep.win_rate:.0f}% ({rep.wins}W/{rep.losses}L)  |  "
        f"avg/trade: ${rep.avg_pnl_per_trade:+.4f}"
    )
    if rep.best_trade:
        lines.append(
            f"best:  ${float(rep.best_trade.get('realized_pnl', 0)):+.2f} "
            f"{str(rep.best_trade.get('question', ''))[:50]!r}"
        )
    if rep.worst_trade:
        lines.append(
            f"worst: ${float(rep.worst_trade.get('realized_pnl', 0)):+.2f} "
            f"{str(rep.worst_trade.get('question', ''))[:50]!r}"
        )

    if rep.by_day:
        lines.append("-- by day --")
        for day in sorted(rep.by_day):
            d = rep.by_day[day]
            lines.append(
                f"  {d.date}  PnL ${d.realized_pnl:+8.2f}  trades {d.merged:3d}  "
                f"vol ${d.volume:9.2f}  detected {d.detected}"
            )

    if rep.by_market:
        lines.append("-- top markets by realized PnL --")
        top = sorted(rep.by_market.items(), key=lambda kv: kv[1], reverse=True)[:10]
        for cid, pnl in top:
            lines.append(f"  {cid[:40]:40s}  ${pnl:+.2f}")

    return "\n".join(lines)
