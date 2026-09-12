"""Optional Telegram alerts: silent when unset, one message per event when configured."""

from __future__ import annotations

import httpx
import pytest
import respx

from almanak_keeperhub.notify import TelegramNotifier, notifier_from_env


def test_unset_env_gives_a_disabled_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALMANAK_KEEPERHUB_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID", raising=False)
    assert notifier_from_env().enabled is False


@respx.mock
async def test_sends_one_message_per_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALMANAK_KEEPERHUB_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID", "42")
    route = respx.post("https://api.telegram.org/bot123:abc/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notifier = notifier_from_env()
    assert notifier.enabled is True

    await notifier.send("broadcast", "approve -> 0xToken", "https://sepolia.basescan.org/tx/0xabc")

    assert route.call_count == 1
    body = route.calls[0].request.content.decode()
    assert '"chat_id": "42"' in body or '"chat_id":"42"' in body
    assert "broadcast" in body and "0xToken" in body


@respx.mock
async def test_telegram_failures_never_break_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALMANAK_KEEPERHUB_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID", "42")
    respx.post("https://api.telegram.org/bot123:abc/sendMessage").mock(side_effect=httpx.ConnectError("down"))

    await notifier_from_env().send("refused", "cap", None)  # must not raise


async def test_disabled_notifier_makes_no_requests() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    await notifier.send("x", "y", None)  # nothing to assert but no network and no error
