"""Tray-Socket (SO_PEERCRED), Chat-Cache, Ausgangs-Warteschlange, Benachrichtigungen."""

import asyncio
import getpass
from datetime import datetime, time, timezone

from kidctl_common import protocol as p
from kidctl_common import tray as t

from kidctl_agent import chat as chat_mod
from kidctl_agent import loginlock, store
from kidctl_agent.agent import Agent
from kidctl_agent.logind import SessionInfo

ME = getpass.getuser()


class FakeLogind:
    async def sessions(self):
        return [SessionInfo(id="1", uid=1001, user=ME, session_class="user", active=True,
                            locked=False, state="active")]

    async def terminate(self, sid):
        pass


class FakeDesktop:
    def __init__(self):
        self.sent = []

    async def notify(self, user, uid, title, body, critical=False, on_action=None):
        self.sent.append((user, title, body))

    async def logout(self, user, uid):
        return True

    async def restart_display_manager(self):
        pass


class FakeConn:
    def __init__(self, connected=False):
        self.connected = connected
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
        return True


def policy(user=ME):
    return p.Policy(version="v", timezone="Europe/Berlin", users={user: 1}, children=[p.ChildPolicy(
        id=1, name="Max", rules=[p.DayRule(weekday=d, quota_minutes=60, window_start=time(0),
                                          window_end=time(23, 59, 59)) for d in range(7)])])


def chat_msg(mid, from_child=False, kind="text", status=None, **kw):
    return p.ChatMessage(id=mid, child_id=1, from_child=from_child, sender_name="Mama" if not from_child else "Max",
                         kind=kind, body=f"Nachricht {mid}", created_at=datetime.now(timezone.utc),
                         status=status, **kw)


def make_agent(user=ME, connected=False):
    store.save(store.POLICY_FILE, policy(user).model_dump(mode="json"))
    desk = FakeDesktop()
    a = Agent(FakeLogind(), desk, None, tray_path=str(loginlock.RUN_DIR / "tray.sock"))
    a.conn = FakeConn(connected)
    return a, desk


class Client:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer

    async def send(self, msg):
        self.w.write(msg.model_dump_json().encode() + b"\n")
        await self.w.drain()

    async def recv(self, skip_state=False):
        while True:
            m = t.agent_to_tray.validate_json(await asyncio.wait_for(self.r.readline(), 3))
            if not (skip_state and isinstance(m, t.TrayState)):
                return m


async def connect(a):
    await a.tray.start()
    return Client(*await asyncio.open_unix_connection(str(a.tray.path)))


def test_hello_state_and_history():
    async def scenario():
        a, _ = make_agent()
        a.chat.upsert(chat_msg(5))
        c = await connect(a)
        await c.send(t.TrayHello())
        st = await c.recv()
        assert isinstance(st, t.TrayState) and st.restricted and st.child_name == "Max"
        assert st.allowed and st.allowed_minutes == 60 and st.remaining_seconds > 0
        hist = await c.recv()
        assert isinstance(hist, t.TrayChat) and [m.id for m in hist.messages] == [5]
        await a.tray.stop()
    asyncio.run(scenario())


def test_unmapped_user_gets_nothing():
    async def scenario():
        a, _ = make_agent(user="jemand-anderes")
        c = await connect(a)
        await c.send(t.TrayHello())
        st = await c.recv()
        assert st.restricted is False
        await c.send(t.TrayChatSend(body="hallo"))
        assert isinstance(await c.recv(), t.TrayError)
        assert a.outbox.items == {}
        await a.tray.stop()
    asyncio.run(scenario())


def test_send_queues_offline_and_flushes_on_connect():
    async def scenario():
        a, _ = make_agent(connected=False)
        c = await connect(a)
        await c.send(t.TrayHello())
        await c.recv(), await c.recv()
        await c.send(t.TrayChatSend(body="  Darf ich noch?  "))
        q = await c.recv(skip_state=True)
        assert isinstance(q, t.TrayQueued) and q.online is False
        await c.send(t.TrayExtensionRequest(minutes=0, reason="x"))
        assert isinstance(await c.recv(skip_state=True), t.TrayError)  # weder Minuten noch Ende
        await c.send(t.TrayExtensionRequest(minutes=15, reason="Level fertig"))
        await c.recv(skip_state=True)
        assert len(a.outbox.items) == 2 and a.conn.sent == []
        # überlebt Neustart
        assert len(chat_mod.Outbox.load().items) == 2
        # Verbindung da -> alles senden; Ack entfernt
        a.conn.connected = True
        await a.flush_outbox()
        sent = a.conn.sent
        assert {type(m) for m in sent} == {p.ChatSend, p.ExtensionRequest}
        assert next(m for m in sent if isinstance(m, p.ChatSend)).body == "Darf ich noch?"
        req = next(m for m in sent if isinstance(m, p.ExtensionRequest))
        assert req.minutes == 15 and req.username == ME and req.day == a.now_local().date()
        for m in sent:
            await a.on_message(p.Ack(client_msg_id=m.client_msg_id, ok=True, message_id=1))
        assert a.outbox.items == {} and chat_mod.Outbox.load().items == {}
        await a.tray.stop()
    asyncio.run(scenario())


def test_chat_messages_pushed_and_notified():
    async def scenario():
        a, desk = make_agent(connected=True)
        await a.tick_once()  # Sitzungen bekannt
        c = await connect(a)
        await c.send(t.TrayHello())
        await c.recv(), await c.recv()
        # Erster Abgleich (leerer Cache): an Tray, aber keine Benachrichtigungsflut
        await a.on_message(chat_msg(1))
        assert (await c.recv(skip_state=True)).message.id == 1 and desk.sent == []
        # Neue Eltern-Nachricht -> Benachrichtigung
        await a.on_message(chat_msg(2))
        assert (await c.recv(skip_state=True)).message.id == 2
        assert desk.sent == [(ME, "Nachricht von Mama", "Nachricht 2")]
        # Eigene Nachricht vom Server zurück -> keine Benachrichtigung; Duplikat -> nichts
        await a.on_message(chat_msg(3, from_child=True))
        await a.on_message(chat_msg(3, from_child=True))
        assert len(desk.sent) == 1
        # Anfrage pending -> genehmigt -> Benachrichtigung mit Inhalt
        await a.on_message(chat_msg(4, from_child=True, kind="extension_request", status="pending"))
        await a.on_message(chat_msg(4, from_child=True, kind="extension_request", status="approved",
                                    approved_minutes=20, approved_window_end=time(21)))
        assert desk.sent[-1] == (ME, "Verlängerung genehmigt", "+20 Minuten und bis 21:00 Uhr")
        assert [m.id for m in chat_mod.ChatStore.load().history(1)] == [1, 2, 3, 4]
        await a.tray.stop()
    asyncio.run(scenario())


def test_chat_sync_after_policy_uses_max_id():
    async def scenario():
        a, _ = make_agent(connected=True)
        a.chat.upsert(chat_msg(7))
        await a.on_message(policy())
        sync = [m for m in a.conn.sent if isinstance(m, p.ChatSync)]
        assert sync == [p.ChatSync(username=ME, after_id=7)]
    asyncio.run(scenario())


def test_notification_action_opens_request_dialog():
    async def scenario():
        a, _ = make_agent()
        c = await connect(a)
        await c.send(t.TrayHello())
        await c.recv(), await c.recv()
        await a.open_request_dialog(ME)
        assert isinstance(await c.recv(skip_state=True), t.TrayOpenRequestDialog)
        await a.tray.stop()
    asyncio.run(scenario())


def test_invalid_input_keeps_connection():
    async def scenario():
        a, _ = make_agent()
        c = await connect(a)
        c.w.write(b'{"type": "kaputt"}\n')
        await c.w.drain()
        assert isinstance(await c.recv(), t.TrayError)
        await c.send(t.TrayHello())
        assert isinstance(await c.recv(), t.TrayState)
        await a.tray.stop()
    asyncio.run(scenario())
