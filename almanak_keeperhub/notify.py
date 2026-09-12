"""Optional operator alerts over Telegram.

KeeperHub's own Telegram node is a Pro-plan feature, and this backend runs on the
free tier, so the alert is sent from here: one message per broadcast, settlement
or refusal. Set ``ALMANAK_KEEPERHUB_TELEGRAM_BOT_TOKEN`` (from @BotFather) and
``ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID`` (your chat with the bot). Unset means silent.
A failed send is logged and never affects execution.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

TOKEN_ENV = "ALMANAK_KEEPERHUB_TELEGRAM_BOT_TOKEN"
CHAT_ENV = "ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send(self, event: str, detail: str, link: str | None) -> None:
        if not self.enabled:
            return
        text = f"almanak-keeperhub · {event}\n{detail}" + (f"\n{link}" if link else "")
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                await http.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
                )
        except Exception as exc:  # noqa: BLE001 - alerts are best effort
            logger.warning("telegram alert failed (%s): %s", event, exc)


def notifier_from_env() -> TelegramNotifier:
    return TelegramNotifier(os.environ.get(TOKEN_ENV, ""), os.environ.get(CHAT_ENV, ""))
