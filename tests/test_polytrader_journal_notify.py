"""Tests for the trade journal and notifiers."""

import json

from polytrader.journal import EXECUTED, MERGED, TradeJournal
from polytrader.models import ArbOpportunity, Market, Token
from polytrader.notify import (
    ConsoleNotifier,
    MultiNotifier,
    Notifier,
    TelegramNotifier,
    WebhookNotifier,
)


def _opp(cid="c", size=10, edge=0.1):
    m = Market(cid, "Will X happen?", tokens=[Token("YES", "Yes"), Token("NO", "No")])
    return ArbOpportunity(m, size, 0.9, 0.9 * size, 0.0, edge, edge * size)


# -- journal -----------------------------------------------------------------

def test_journal_writes_jsonl_events(tmp_path):
    jl = tmp_path / "events.jsonl"
    j = TradeJournal(jsonl_path=str(jl))
    opp = _opp()
    j.detected(opp)
    j.executed(opp, approved_size=8, spent=7.2)
    j.merged(opp, credited=8.0, realized_pnl=0.8)

    lines = jl.read_text().strip().splitlines()
    assert len(lines) == 3
    recs = [json.loads(x) for x in lines]
    assert recs[0]["event"] == "opportunity_detected"
    assert recs[1]["event"] == EXECUTED and recs[1]["size"] == 8
    assert recs[2]["event"] == MERGED and recs[2]["realized_pnl"] == 0.8
    assert all("ts" in r for r in recs)


def test_journal_csv_has_header_and_rows(tmp_path):
    csvp = tmp_path / "trades.csv"
    j = TradeJournal(csv_path=str(csvp))
    opp = _opp()
    j.detected(opp)                       # not a csv_row event -> no CSV line
    j.executed(opp, approved_size=8, spent=7.2)
    j.merged(opp, credited=8.0, realized_pnl=0.8)

    lines = csvp.read_text().strip().splitlines()
    assert lines[0].startswith("ts,event,condition_id")  # header
    assert len(lines) == 3                                 # header + 2 csv rows
    assert "trade_executed" in lines[1]
    assert "set_merged" in lines[2]


def test_journal_noop_without_paths():
    j = TradeJournal()  # no files configured
    j.detected(_opp())  # must not raise
    j.halted("test")


# -- notifiers ---------------------------------------------------------------

class _StubResp:
    def raise_for_status(self):
        pass


class _StubSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return _StubResp()


def test_console_notifier_returns_true():
    assert ConsoleNotifier().send("t", "b") is True


def test_webhook_notifier_posts_text():
    s = _StubSession()
    n = WebhookNotifier("http://hook", session=s)
    assert n.send("edge", "details") is True
    url, payload = s.calls[0]
    assert url == "http://hook"
    assert payload["text"] == "edge\ndetails"


def test_telegram_notifier_posts_to_bot_api():
    s = _StubSession()
    n = TelegramNotifier("TOKEN", "CHAT", session=s)
    n.send("edge", "details")
    url, payload = s.calls[0]
    assert "botTOKEN/sendMessage" in url
    assert payload["chat_id"] == "CHAT"


def test_safe_send_swallows_errors():
    class Boom(Notifier):
        def send(self, title, body):
            raise RuntimeError("down")

    assert Boom().safe_send("t", "b") is False  # logged, not raised


def test_multi_notifier_fans_out():
    s1, s2 = _StubSession(), _StubSession()
    multi = MultiNotifier([WebhookNotifier("http://a", session=s1),
                           WebhookNotifier("http://b", session=s2)])
    assert multi.send("t", "b") is True
    assert s1.calls and s2.calls


def test_multi_notifier_empty_is_ok():
    assert MultiNotifier([]).send("t", "b") is True
