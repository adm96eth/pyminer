"""Tests for the journal -> stats rollup."""

import json

from polytrader.report import build_report, format_report


def _write_journal(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# 2026-06-01 and 2026-06-02 in UTC
DAY1 = 1748736000.0  # ~2025-06-01 ... use a fixed known epoch below instead
# Use explicit, easy-to-reason epochs:
T_JUN1 = 1717200000.0  # 2024-06-01 00:00:00 UTC
T_JUN2 = 1717286400.0  # 2024-06-02 00:00:00 UTC


def test_report_aggregates_pnl_counts_and_winrate(tmp_path):
    p = tmp_path / "j.jsonl"
    _write_journal(p, [
        {"ts": T_JUN1, "event": "opportunity_detected", "condition_id": "a"},
        {"ts": T_JUN1, "event": "trade_executed", "condition_id": "a", "set_cost": 0.9, "size": 10},
        {"ts": T_JUN1, "event": "set_merged", "condition_id": "a", "realized_pnl": 1.0,
         "question": "Win A?"},
        {"ts": T_JUN1, "event": "opportunity_skipped", "condition_id": "b"},
        {"ts": T_JUN2, "event": "trade_executed", "condition_id": "c", "set_cost": 0.5, "size": 20},
        {"ts": T_JUN2, "event": "set_merged", "condition_id": "c", "realized_pnl": -0.5,
         "question": "Lose C?"},
        {"ts": T_JUN2, "event": "halt", "detail": "kill switch"},
    ])
    rep = build_report(str(p))

    assert rep.detected == 1
    assert rep.executed == 2
    assert rep.merged == 2
    assert rep.skipped == 1
    assert rep.halts == 1
    assert abs(rep.realized_pnl - 0.5) < 1e-9        # 1.0 + (-0.5)
    assert abs(rep.volume - (0.9 * 10 + 0.5 * 20)) < 1e-9  # 9 + 10
    assert rep.wins == 1 and rep.losses == 1
    assert abs(rep.win_rate - 50.0) < 1e-9
    assert rep.best_trade["realized_pnl"] == 1.0
    assert rep.worst_trade["realized_pnl"] == -0.5


def test_report_splits_by_day_and_market(tmp_path):
    p = tmp_path / "j.jsonl"
    _write_journal(p, [
        {"ts": T_JUN1, "event": "set_merged", "condition_id": "a", "realized_pnl": 2.0},
        {"ts": T_JUN1, "event": "set_merged", "condition_id": "a", "realized_pnl": 1.0},
        {"ts": T_JUN2, "event": "set_merged", "condition_id": "b", "realized_pnl": 5.0},
    ])
    rep = build_report(str(p))

    assert set(rep.by_day) == {"2024-06-01", "2024-06-02"}
    assert abs(rep.by_day["2024-06-01"].realized_pnl - 3.0) < 1e-9
    assert abs(rep.by_day["2024-06-02"].realized_pnl - 5.0) < 1e-9
    assert abs(rep.by_market["a"] - 3.0) < 1e-9
    assert abs(rep.by_market["b"] - 5.0) < 1e-9


def test_report_tolerates_torn_last_line(tmp_path):
    p = tmp_path / "j.jsonl"
    with open(p, "w") as f:
        f.write(json.dumps({"ts": T_JUN1, "event": "set_merged",
                            "condition_id": "a", "realized_pnl": 1.0}) + "\n")
        f.write('{"ts": 1717200000.0, "event": "set_merged", "realiz')  # crash mid-write
    rep = build_report(str(p))
    assert rep.merged == 1
    assert abs(rep.realized_pnl - 1.0) < 1e-9


def test_format_report_is_readable(tmp_path):
    p = tmp_path / "j.jsonl"
    _write_journal(p, [
        {"ts": T_JUN1, "event": "set_merged", "condition_id": "a", "realized_pnl": 1.0,
         "question": "Win A?"},
    ])
    text = format_report(build_report(str(p)))
    assert "polytrader report" in text
    assert "realized PnL: $+1.00" in text
    assert "by day" in text
    assert "2024-06-01" in text


def test_empty_report():
    rep = build_report  # ensure import ok
    from polytrader.report import Report
    r = Report()
    assert r.win_rate == 0.0
    assert r.avg_pnl_per_trade == 0.0
