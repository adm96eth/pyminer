"""Pluggable notifications for detected edges and halts.

Backends share one tiny interface (``send(title, body)``):
  * ConsoleNotifier  -- prints (default, no deps, no network)
  * WebhookNotifier  -- POST JSON to any URL (Slack/Discord/generic)
  * TelegramNotifier -- Telegram Bot API sendMessage
  * MultiNotifier    -- fan out to several

Secrets/URLs come from the environment, never config files. A failing notifier
must never take down the trading loop, so ``send`` swallows and logs errors.
"""

from __future__ import annotations

import abc
import logging
import os
from typing import List, Optional

log = logging.getLogger("polytrader.notify")


class Notifier(abc.ABC):
    @abc.abstractmethod
    def send(self, title: str, body: str) -> bool:
        ...

    def safe_send(self, title: str, body: str) -> bool:
        try:
            return self.send(title, body)
        except Exception as e:  # never break the loop over a notification
            log.warning("notifier %s failed: %s", type(self).__name__, e)
            return False


class ConsoleNotifier(Notifier):
    def send(self, title: str, body: str) -> bool:
        log.info("NOTIFY %s | %s", title, body)
        return True


class WebhookNotifier(Notifier):
    """POSTs ``{"text": "<title>\\n<body>"}`` -- works for Slack/Discord/etc."""

    def __init__(self, url: str, timeout: float = 5.0, session=None):
        self.url = url
        self.timeout = timeout
        self._session = session

    def _sess(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def send(self, title: str, body: str) -> bool:
        r = self._sess().post(self.url, json={"text": f"{title}\n{body}"}, timeout=self.timeout)
        r.raise_for_status()
        return True


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0, session=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._session = session

    def _sess(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def send(self, title: str, body: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        r = self._sess().post(
            url, json={"chat_id": self.chat_id, "text": f"*{title}*\n{body}", "parse_mode": "Markdown"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return True


class MultiNotifier(Notifier):
    def __init__(self, notifiers: List[Notifier]):
        self.notifiers = notifiers

    def send(self, title: str, body: str) -> bool:
        return all(n.safe_send(title, body) for n in self.notifiers) if self.notifiers else True


def from_env(enable_console: bool = True) -> Notifier:
    """Build a notifier from env vars; falls back to console only.

    POLYTRADER_WEBHOOK_URL   -> WebhookNotifier
    TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID -> TelegramNotifier
    """
    backends: List[Notifier] = []
    if enable_console:
        backends.append(ConsoleNotifier())

    webhook = os.environ.get("POLYTRADER_WEBHOOK_URL")
    if webhook:
        backends.append(WebhookNotifier(webhook))

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        backends.append(TelegramNotifier(tg_token, tg_chat))

    return MultiNotifier(backends)
