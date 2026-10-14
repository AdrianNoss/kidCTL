"""E2E: Tray -> Agent -> echter Server -> Eltern-WebUI -> zurück zur Tray."""

import asyncio
import getpass
import re

from kidctl_common import protocol as p
from kidctl_common import tray as t

import kidctl_agent.agent as agent_mod
from kidctl_agent import cli, loginlock, store
from kidctl_agent.agent import Agent
from test_e2e_server import FakeLogind, FakeNotifier, server, wait_for  # noqa: F401 (Fixture)
from test_tray import Client

ME = getpass.getuser()


def test_chat_and_request_roundtrip(server):  # noqa: F811
    base, web, csrf = server
    code = re.search(r"enroll \S+ (\w{8})", web.post("/devices/pair", data={"csrf_token": csrf, "name": "PC"}).text).group(1)
    cli.enroll(base, code, insecure_http=True)
    device = store.load(store.DEVICE_FILE)
    agent_mod.local_users = lambda: [p.LocalUser(username=ME, uid=1001)]

    async def scenario():
        agent = Agent(FakeLogind([]), FakeNotifier(), device, tray_path=str(loginlock.RUN_DIR / "tray.sock"))
        run = asyncio.create_task(agent.run())
        await wait_for(lambda: agent.online)
        page = web.get(f"/devices/{device['device_id']}").text
        field = re.search(r'name="(user_\d+)"', page).group(1)
        child_id = re.search(r'<option value="(\d+)"', page).group(1)
        web.post(f"/devices/{device['device_id']}/mapping", data={"csrf_token": csrf, field: child_id})
        await wait_for(lambda: agent.policy and ME in agent.policy.users)

        c = Client(*await asyncio.open_unix_connection(str(agent.tray.path)))
        await c.send(t.TrayHello())
        assert (await c.recv()).restricted
        assert isinstance(await c.recv(), t.TrayChat)  # Verlauf nach hello

        # Kind schreibt -> Eltern sehen es; eigene Nachricht kommt als chat_message zurück
        await c.send(t.TrayChatSend(body="Hallo Mama"))
        assert (await c.recv(skip_state=True)).online is True
        echo = await c.recv(skip_state=True)
        assert isinstance(echo, t.TrayChatMessage) and echo.message.body == "Hallo Mama"
        assert "Hallo Mama" in web.get(f"/children/{child_id}/chat").text
        await wait_for(lambda: not agent.outbox.items)  # per Ack bestätigt

        # Eltern antworten -> Tray
        web.post(f"/children/{child_id}/chat", data={"csrf_token": csrf, "body": "Essen!"})
        m = await c.recv(skip_state=True)
        assert m.message.body == "Essen!" and not m.message.from_child

        # Verlängerung anfragen -> Eltern genehmigen angepasst -> Tray sieht Status, Policy hat Gutschrift
        await c.send(t.TrayExtensionRequest(minutes=30, reason="Referat fertig machen"))
        await c.recv(skip_state=True)
        req = (await c.recv(skip_state=True)).message
        assert req.kind == "extension_request" and req.status == "pending"
        web.post(f"/children/{child_id}/requests/{req.id}",
                 data={"csrf_token": csrf, "action": "approve", "minutes": "20", "window_end": ""})
        upd = await c.recv(skip_state=True)
        assert upd.message.id == req.id and upd.message.status == "approved" and upd.message.approved_minutes == 20
        await wait_for(lambda: any(e.extra_minutes == 20 for e in agent.policy.children[0].exceptions))

        run.cancel()
        try:
            await run
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
