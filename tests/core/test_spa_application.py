# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from kajenn import Page, SpaApplicationBase
from kajenn.wsx import WsxEnvelope
from tests.conftest import start_server
from tests.storage_support import add_user, issue_token
from .test_external_application import running
from .test_external_application_websocket import Socket


@pytest.mark.parametrize(
    "default,override,same",
    [
        (True, None, True),
        (False, None, False),
        (False, True, True),
        (True, False, False),
    ],
)
def test_instance_lifetime_and_recreation(default, override, same):
    class Example(Page):
        persistent = override

    app = SpaApplicationBase(persistent=default)
    r = app.registry
    r.new_connection("c")
    r.new_page("p", connection_id="c", page_class=Example)

    def resolve(_):
        return r.resolve_page(app, "p", connection_id="c")

    with ThreadPoolExecutor(8) as pool:
        pages = list(pool.map(resolve, range(16)))
    assert len({id(p) for p in pages}) == (1 if same else len(pages))
    page = pages[0]
    assert page.application is app
    page.store["draft"] = "saved"
    r.release_page_instance("p", connection_id="c")
    rebuilt = resolve(None)
    assert rebuilt is not page and rebuilt.store is page.store
    assert rebuilt.store["draft"] == "saved"
    r.change_connection_user("c", "mario")
    assert page.state.user == rebuilt.state.user == "mario"


RECIPE = """
import os
from genro_routes import route
from kajenn import AuthApplicationBase, Page, SpaApplicationBase
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryAuthStore

class Transient(Page):
    persistent = False

class Example(SpaApplicationBase):
    @route()
    def transient(self, pid=None, _request=None):
        page = self.get_page(_request, pid) if pid else self.open_page(_request, Transient)
        page.calls = getattr(page, "calls", 0) + 1
        same = self.get_page(_request, page.page_id) is page
        return {"id": page.page_id, "same": same, "calls": page.calls}

    @route()
    def prepared_state(self):
        return {"pid": os.getpid()}

    @route()
    def tab(self, _request=None):
        page = self.open_page(_request)
        page.store["draft"] = "saved"
        return {"id": page.page_id, "user": page.state.user}

    @route(channel_channels="api,wsx")
    def read(self, pid, _request=None):
        page = self.get_page(_request, pid)
        return {"draft": page.store["draft"], "user": page.state.user}

    @route()
    async def notify(self, pid, _request=None):
        page = self.get_page(_request, pid)
        return {"sent": await page.send_message("changed", {"draft": page.store["draft"]})}

class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Example, PLACEMENT)
        apps.application(code="_auth", app_class=AuthApplicationBase).store(store_class=MemoryAuthStore)
"""


@pytest.mark.parametrize("spawned", [False, True], ids=["local", "subprocess"])
async def test_two_tabs_login_and_socket(tmp_path, spawned):
    config = tmp_path / "config.py"
    config.write_text(
        RECIPE.replace("PLACEMENT", 'spawner="subprocess"' if spawned else 'mount="billing"')
    )
    async with running(str(config)) as server:
        transport = httpx.ASGITransport(app=server)
        add_user(server, "mario", password="pw")
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://example.org:8000") as client,
            httpx.AsyncClient(transport=transport, base_url="http://example.org:8000") as other,
        ):
            process = (await client.get("/billing/prepared_state")).json()
            assert (process["pid"] != os.getpid()) == spawned
            transient = (await client.get("/billing/transient")).json()
            again = await client.get("/billing/transient", params={"pid": transient["id"]})
            assert transient == again.json() == {"id": transient["id"], "same": True, "calls": 1}
            response = await client.get("/billing/tab")
            assert response.status_code == 200, response.text
            first = response.json()
            second = (await client.get("/billing/tab")).json()
            assert first["id"] != second["id"] and first["user"] == second["user"]
            params = {"pid": first["id"]}
            assert (await other.get("/billing/read", params=params)).status_code == 403
            token = issue_token(server, "mario")
            temporary = await client.get(
                "/billing/read", params=params, headers={"authorization": f"Bearer {token}"}
            )
            assert temporary.json()["user"] == first["user"]

            socket = Socket(server, "/billing/_wsx")
            cookie = f"session_id={client.cookies['session_id']}"
            socket.scope["headers"].append((b"cookie", cookie.encode()))
            assert (await socket.open())["type"] == "websocket.accept"

            async def bind(page_id):
                envelope = WsxEnvelope(
                    id="1", method="WSK", path="/billing/_wsx/openchannel", page_id=page_id
                )
                await socket.say(envelope.encode())
                return WsxEnvelope((await socket.next())["text"]).status

            try:
                foreign = (await other.get("/billing/tab")).json()
                assert await bind(foreign["id"]) == 403
                assert await bind(first["id"]) == 200
                refused = await client.post(
                    "/_auth/login", json={"identity": "mario", "password": "wrong"}
                )
                assert "error" in refused.json()
                assert (await client.get("/billing/read", params=params)).json()["user"] == first[
                    "user"
                ]
                login = await client.post(
                    "/_auth/login", json={"identity": "mario", "password": "pw"}
                )
                assert login.status_code == 200, login.text
                for tab in (first, second):
                    assert (
                        await client.get("/billing/read", params={"pid": tab["id"]})
                    ).json() == {"draft": "saved", "user": "mario"}
                assert (
                    await client.get("/billing/notify", params={"pid": second["id"]})
                ).json() == {"sent": False}
                assert (await client.get("/billing/notify", params=params)).json() == {"sent": True}
                event = WsxEnvelope((await socket.next())["text"])
                assert (event.path, event.page_id, event.data) == (
                    "changed",
                    first["id"],
                    {"draft": "saved"},
                )
                logout = await client.post(
                    "/_auth/logout", params={"session_id": client.cookies["session_id"]}
                )
                assert logout.json()["status"] == "ok"
                app = server.applications["billing"]
                push = app.remote_call if spawned else None
                cid = first["user"].removeprefix("guest_")
                if push:
                    delivered = await push(
                        "send_page_message", first["id"], "closed", connection_id=cid
                    )
                else:
                    delivered = await app.send_page_message(
                        first["id"], "closed", connection_id=cid
                    )
                assert delivered is False
                assert await bind(first["id"]) == 403
                assert (await client.get("/billing/read", params=params)).status_code == 403
            finally:
                await socket.incoming.put({"type": "websocket.disconnect", "code": 1000})
                await asyncio.wait_for(socket.task, 10)


async def test_guest_site_without_authentication_application():
    from kajenn import AsgiServer
    from genro_routes import route

    class Guestbook(SpaApplicationBase):
        @route()
        def tab(self, _request=None):
            return {"user": self.open_page(_request).state.user}

    server = AsgiServer(applications=[(Guestbook, {"code": "book"})])
    await start_server(server)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://example.org:8000"
    ) as client:
        first = (await client.get("/book/tab")).json()["user"]
        assert first.startswith("guest_")
        assert (await client.get("/book/tab")).json()["user"] == first


async def test_oidc_callback_preserves_an_open_guest_page(tmp_path, monkeypatch):
    from genro_routes import route
    from tests.auth_app import test_oidc as oidc

    monkeypatch.setattr(oidc.httpx, "AsyncClient", oidc.FakeAsyncClient)
    monkeypatch.setattr(oidc.jwt, "PyJWKClient", oidc.FakePyJWKClient)
    server = oidc.make_server(tmp_path)

    class Example(SpaApplicationBase):
        @route()
        def tab(self, _request=None):
            page = self.open_page(_request)
            page.store["draft"] = "saved"
            return {"pid": page.page_id, "user": page.state.user}

        @route()
        def read(self, pid, _request=None):
            page = self.get_page(_request, pid)
            return {"user": page.state.user, "draft": page.store["draft"]}

    server.register_application(Example(code="spa"))
    await start_server(server)
    oidc.seed(server)
    cookie = f"session_id={server.session_store.create().id}"
    tab = oidc.payload(await oidc.drive(server, "/spa/tab", cookie))
    assert tab["user"].startswith("guest_")
    start = await oidc.drive(server, f"{oidc.START}?next=/spa/tab", cookie)
    challenge = oidc.location_query(start)
    state = challenge["state"][0]
    nonce = challenge.get("nonce", [None])[0]
    monkeypatch.setattr(oidc.FakeAsyncClient, "id_token", oidc.make_id_token(nonce=nonce))
    callback = await oidc.drive(server, f"{oidc.CALLBACK}?code=auth-code&state={state}", cookie)
    assert oidc.status(callback) in (302, 303, 307)
    read = await oidc.drive(server, f"/spa/read?pid={tab['pid']}", cookie)
    assert oidc.payload(read) == {"user": "alice", "draft": "saved"}


async def test_native_socket_revocation_blocks_push_and_releases_handler(monkeypatch):
    import asyncio
    from kajenn import spa_transport

    active = True
    events = []
    released = asyncio.Event()

    async def visit(server, code, scope):
        if active:
            return {"cid": "visit", "identity": None, "login": True, "ttl": 60}

    async def handler(scope, receive, send):
        nonlocal active
        await send({"type": "websocket.accept"})
        active = False
        await send({"type": "websocket.send", "text": "private"})
        assert await receive() == {"type": "websocket.disconnect", "code": 1008}
        released.set()

    async def record(event):
        events.append(event)

    monkeypatch.setattr(spa_transport, "prepare_visit", visit)
    await asyncio.wait_for(
        spa_transport.serve_session_websocket(None, "demo", handler, {}, asyncio.Queue().get, record),
        timeout=2,
    )
    assert released.is_set()
    assert events == [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 1008}]
