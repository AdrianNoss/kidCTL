"""Chat-Benachrichtigungen: Verlauf aus dem Abgleich spammt nicht (neue Zuordnung, Offline-Phase)."""

import asyncio
from datetime import datetime, timedelta, timezone

from kidctl_agent import core
from test_tray import ME, chat_msg, make_agent


def old(mid, **kw):
    m = chat_msg(mid, **kw)
    return m.model_copy(update={"created_at": datetime.now(timezone.utc) - timedelta(days=1)})


async def _agent():
    a, desk = make_agent(connected=True)
    a.policy = a.policy or core.store.load(core.store.POLICY_FILE, core.p.Policy.model_validate)
    a._sessions = await a.logind.sessions()
    return a, desk


def test_first_sync_is_silent_then_live_notifies(monkeypatch):
    monkeypatch.setattr(core, "BACKLOG_SETTLE", 0.01)

    async def scenario():
        a, desk = await _agent()
        await a.sync_chat()                                  # neue Zuordnung: kein lokaler Verlauf
        for mid in range(1, 6):
            await a.on_chat_message(old(mid))
        await asyncio.sleep(0.05)
        assert desk.sent == []
        assert len(a.chat.history(1)) == 5                   # Verlauf still übernommen
        await a.on_chat_message(chat_msg(6))                 # danach live
        assert desk.sent == [(ME, "Nachricht von Mama", "Nachricht 6")]
    asyncio.run(scenario())


def test_first_live_message_without_history_notifies():
    async def scenario():
        a, desk = await _agent()
        await a.sync_chat()                                  # Server hat noch keinen Verlauf
        await a.on_chat_message(chat_msg(1))
        assert desk.sent == [(ME, "Nachricht von Mama", "Nachricht 1")]
    asyncio.run(scenario())


def test_offline_backlog_gives_one_summary(monkeypatch):
    monkeypatch.setattr(core, "BACKLOG_SETTLE", 0.01)

    async def scenario():
        a, desk = await _agent()
        a.chat.upsert(old(1))
        await a.sync_chat()                                  # Wiederverbindung mit Verlauf
        for mid in (2, 3, 4):
            await a.on_chat_message(old(mid))
        await a.on_chat_message(old(5, from_child=True))     # eigene Nachricht zählt nicht
        assert desk.sent == []
        await asyncio.sleep(0.05)
        assert desk.sent == [(ME, "3 neue Nachrichten", "Von Mama. Öffne den Chat, um sie zu lesen.")]
    asyncio.run(scenario())


def test_offline_single_message_notifies_normally(monkeypatch):
    monkeypatch.setattr(core, "BACKLOG_SETTLE", 0.01)

    async def scenario():
        a, desk = await _agent()
        a.chat.upsert(old(1))
        await a.sync_chat()
        await a.on_chat_message(old(2))
        await asyncio.sleep(0.05)
        assert desk.sent == [(ME, "Nachricht von Mama", "Nachricht 2")]
    asyncio.run(scenario())
