# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""An application in another process, carried by the kbus library.

The configuration declares the real application class with a ``spawner``; the
server starts the process and hands it one URL carrying the name and a fresh
token; the process connects to the server's ``kbus.Dispatcher`` with them. The
server answers that mount through the member. Every test below observes the
server from outside: HTTP through the ASGI interface, ``kbus_call``, the pid
of the process, the process table, a ``kbus.Member`` connecting from outside.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Any

import kbus
import pytest

from kajenn import AsgiServer, Avatar
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.kbus import KBusCallError
from kajenn.wsx import WsxEnvelope

RECIPE = '''
import asyncio
import os

from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from kajenn.well_known import WELL_KNOWN_ROOT
from genro_routes import RoutingClass


class Discovery(RoutingClass):
    @route()
    def probe(self):
        return {"served": "probe"}


class Billing(RoutedApplication):
    prepared = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.route.add_branches({"name": WELL_KNOWN_ROOT, "instance": Discovery()})

    async def on_startup(self):
        await asyncio.sleep(STARTUP_DELAY)
        self.prepared = True

    @route()
    def total(self, order):
        return {"total": int(order) * 2}

    @route()
    def prepared_state(self):
        return {"prepared": self.prepared, "pid": os.getpid()}

    @route()
    async def ask_local(self):
        return await self.server.kbus_call("/local/ping", {"x": 7})

    @route()
    def configuration(self):
        codes = [kwargs["code"] for _, kwargs in self.server.config.applications()[0]]
        return {"codes": codes, "site": self.server.config.site_kwargs().get("site_name")}

    @route()
    async def slow(self):
        await asyncio.sleep(3)
        return {"slow": True}

    @route()
    def fail(self):
        raise RuntimeError("billing failed")

    @route()
    def seen_by(self, _request=None):
        avatar = _request.avatar()
        return {
            "identity": avatar.identity if avatar is not None else None,
            "channel": _request.scope.get("kajenn.channel"),
            "pid": os.getpid(),
        }


class Local(RoutedApplication):
    @route()
    def ping(self, x):
        return {"pong": x}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    site_name = "billingsite"
SERVER_SECTION
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, DECLARATION)
        apps.application(code="local", app_class=Local)
'''

SPAWNED = 'spawner="subprocess", request_timeout=1'
INPROCESS = 'mount="billing"'


def write_recipe(
    tmp_path, *, declaration: str = SPAWNED, kbus_options: str | None = None,
    startup_delay: float = 0.2,
) -> str:
    """The recipe with ``billing`` declared by ``declaration``; ``kbus_options`` is the
    argument list of ``cfg.server().kbus(...)``, absent meaning no ``server.kbus``."""
    section = ""
    if kbus_options is not None:
        section = f"\n    def server_section(self, cfg):\n        cfg.server().kbus({kbus_options})\n\n"
    text = (RECIPE.replace("SERVER_SECTION", section)
            .replace("DECLARATION", declaration)
            .replace("STARTUP_DELAY", repr(startup_delay)))
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


def short_dir() -> str:
    """A directory whose socket paths fit the 104-byte limit of macOS."""
    return tempfile.mkdtemp(prefix="kb", dir="/tmp")


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def request(server: AsgiServer, path: str, *, query: bytes = b"",
                  method: str = "GET", body: bytes = b"") -> tuple[int, Any]:
    result = await BufferedAsgiEndpoint(server).serve({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": query,
        "headers": [(b"content-type", b"application/json")] if body else [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }, body)
    try:
        answer = json.loads(result["body"])
    except ValueError:
        answer = result["body"]
    return result["status"], answer


async def answering(server: AsgiServer, *, other_than: int | None = None,
                    seconds: float = 30) -> dict[str, Any]:
    """Wait until the mount answers 200, from a process other than ``other_than``."""
    deadline = time.monotonic() + seconds
    while True:
        status, state = await request(server, "/billing/prepared_state")
        if status == 200 and state["pid"] != other_than:
            return state
        assert time.monotonic() < deadline, f"the mount never answered: {status} {state}"
        await asyncio.sleep(0.05)


@asynccontextmanager
async def running(config: str, *, wait: bool = True):
    server = AsgiServer(config=config)
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        if wait:
            await answering(server)
        yield server
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await lifespan


def command_line(pid: int) -> str:
    return subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)],
                          capture_output=True, text=True, check=True).stdout.strip()


async def test_an_external_application_answers_like_an_inprocess_one(tmp_path):
    # wf:contract: GET /billing/total?order=2 answers (200, {"total": 4}) both when billing
    # wf:contract: is declared with spawner="subprocess" and when it is declared in-process;
    # wf:contract: the spawned answer comes from a process other than the server's.
    async with running(write_recipe(tmp_path)) as server:
        external = await request(server, "/billing/total", query=b"order=2")
        _, state = await request(server, "/billing/prepared_state")
    (tmp_path / "inproc").mkdir()
    async with running(write_recipe(tmp_path / "inproc", declaration=INPROCESS)) as server:
        inprocess = await request(server, "/billing/total", query=b"order=2")
    assert external == inprocess == (200, {"total": 4})
    assert state["pid"] != os.getpid()


async def test_kbus_call_reaches_the_external_route(tmp_path):
    # wf:contract: server.kbus_call("/billing/total", {"order": 3}) returns {"total": 6}.
    async with running(write_recipe(tmp_path)) as server:
        assert await server.kbus_call("/billing/total", {"order": 3}) == {"total": 6}


async def test_the_external_process_reaches_a_route_of_the_server(tmp_path):
    # wf:contract: a handler of the external application calling
    # wf:contract: self.server.kbus_call("/local/ping", data) gets the answer of the
    # wf:contract: in-process application local of the server.
    async with running(write_recipe(tmp_path)) as server:
        assert await server.kbus_call("/billing/ask_local") == {"pong": 7}


async def test_the_external_process_sees_the_whole_configuration(tmp_path):
    # wf:contract: inside the spawned process self.server.config is the configuration the
    # wf:contract: server loaded: every application code and the site name are readable.
    async with running(write_recipe(tmp_path)) as server:
        assert await request(server, "/billing/configuration") == (
            200, {"codes": ["billing", "local"], "site": "billingsite"})


async def test_requests_are_answered_only_after_on_startup(tmp_path):
    # wf:contract: the process joins the dispatcher only after its application's
    # wf:contract: on_startup returned: the first 200 already sees what on_startup prepared.
    async with running(write_recipe(tmp_path, startup_delay=1.0)) as server:
        state = await answering(server)
    assert state["prepared"] is True


async def test_a_mount_whose_process_has_not_joined_answers_503(tmp_path):
    # wf:contract: while the process of billing has not joined (its on_startup still
    # wf:contract: running), an HTTP request to the mount answers 503 and kbus_call raises
    # wf:contract: KBusCallError with status 503; the server finished its startup anyway.
    async with running(write_recipe(tmp_path, startup_delay=20), wait=False) as server:
        status, _ = await request(server, "/billing/total", query=b"order=2")
        with pytest.raises(KBusCallError) as refused:
            await server.kbus_call("/billing/total", {"order": 2})
    assert status == 503
    assert refused.value.status == 503


async def test_a_member_with_the_wrong_token_is_rejected(tmp_path):
    # wf:contract: a kbus.Member named "billing" with a secret that is not the token the
    # wf:contract: server generated is rejected by the server's dispatcher (kbus.Rejected
    # wf:contract: from connect), and the mount keeps answering 503.
    directory = short_dir()
    try:
        address = f"unix://{directory}/kbus.sock"
        config = write_recipe(tmp_path, kbus_options=f'address="{address}"', startup_delay=20)
        async with running(config, wait=False) as server:
            intruder = kbus.Member("billing", secret="wrong", handler=None)
            with pytest.raises(kbus.Rejected):
                await intruder.connect(address)
            status, _ = await request(server, "/billing/total", query=b"order=2")
        assert status == 503
    finally:
        shutil.rmtree(directory, ignore_errors=True)


async def test_the_token_is_not_on_the_command_line(tmp_path):
    # wf:contract: the spawned process runs `<python> -m kajenn serve <config> --role
    # wf:contract: application:billing` and nothing more: the URL with name and token
    # wf:contract: reaches it through its environment, never through its arguments.
    config = write_recipe(tmp_path)
    async with running(config) as server:
        state = await answering(server)
        line = command_line(state["pid"])
    assert line.endswith(f"-m kajenn serve {config} --role application:billing")


async def test_shutdown_stops_the_spawned_process(tmp_path):
    # wf:contract: at server shutdown the process the spawner started is stopped and no
    # wf:contract: child outlives the server.
    async with running(write_recipe(tmp_path)) as server:
        state = await answering(server)
    with pytest.raises(ProcessLookupError):
        os.kill(state["pid"], 0)


async def test_a_killed_process_is_relaunched(tmp_path):
    # wf:contract: when the spawned process dies while the server runs, the spawner starts
    # wf:contract: it again with a new token, and the mount answers again from the new pid.
    async with running(write_recipe(tmp_path)) as server:
        first = await answering(server)
        os.kill(first["pid"], signal.SIGKILL)
        second = await answering(server, other_than=first["pid"])
        assert await request(server, "/billing/total", query=b"order=5") == (200, {"total": 10})
    assert second["pid"] != first["pid"]


async def test_a_call_past_the_request_timeout_answers_503(tmp_path):
    # wf:contract: a forwarded request that takes longer than the mount's request_timeout
    # wf:contract: (1 second here) answers 503.
    async with running(write_recipe(tmp_path)) as server:
        status, _ = await request(server, "/billing/slow")
    assert status == 503


async def test_a_failing_route_answers_the_same_status_in_both_placements(tmp_path):
    # wf:contract: a route that raises answers the same status spawned and in-process.
    async with running(write_recipe(tmp_path)) as server:
        spawned, _ = await request(server, "/billing/fail")
    (tmp_path / "inproc").mkdir()
    async with running(write_recipe(tmp_path / "inproc", declaration=INPROCESS)) as server:
        inprocess, _ = await request(server, "/billing/fail")
    assert spawned == inprocess == 500


async def test_avatar_and_channel_reach_the_spawned_process(tmp_path):
    # wf:contract: kbus_call with auth=Avatar(...) and channel="telegram" reaches the
    # wf:contract: spawned handler with that identity and that channel; without a channel
    # wf:contract: the handler sees "rest".
    async with running(write_recipe(tmp_path)) as server:
        seen = await server.kbus_call("/billing/seen_by", {},
                                      auth=Avatar("ann", ["admin"]), channel="telegram")
        plain = await server.kbus_call("/billing/seen_by", {})
    assert (seen["identity"], seen["channel"]) == ("ann", "telegram")
    assert plain["channel"] == "rest"
    assert seen["pid"] != os.getpid()


async def test_discovery_documents_answer_the_same_spawned_and_inprocess(tmp_path):
    # wf:contract: GET /.well-known/probe answers {"served": "probe"} when billing is
    # wf:contract: spawned exactly as when it is in-process, and again after a relaunch.
    async with running(write_recipe(tmp_path)) as server:
        spawned = await request(server, "/.well-known/probe")
        first = await answering(server)
        os.kill(first["pid"], signal.SIGKILL)
        await answering(server, other_than=first["pid"])
        relaunched = await request(server, "/.well-known/probe")
    (tmp_path / "inproc").mkdir()
    async with running(write_recipe(tmp_path / "inproc", declaration=INPROCESS)) as server:
        inprocess = await request(server, "/.well-known/probe")
    assert spawned == relaunched == inprocess == (200, {"served": "probe"})


async def test_forty_concurrent_calls_all_resolve(tmp_path):
    # wf:contract: forty kbus_call to the spawned application in flight at once all
    # wf:contract: return their own answer.
    async with running(write_recipe(tmp_path)) as server:
        answers = await asyncio.gather(
            *(server.kbus_call("/billing/total", {"order": n}) for n in range(40)))
    assert answers == [{"total": n * 2} for n in range(40)]


async def test_a_body_over_the_frame_limit_answers_413(tmp_path):
    # wf:contract: server.kbus(max_frame=N) sets the frame limit of the dispatcher; a
    # wf:contract: request whose body does not fit one frame answers 413 and the mount
    # wf:contract: keeps answering afterwards.
    config = write_recipe(tmp_path, kbus_options="max_frame=65536")
    async with running(config) as server:
        status, _ = await request(server, "/billing/total", method="POST",
                                  body=b'{"order": "' + b"9" * 100_000 + b'"}')
        after = await request(server, "/billing/total", query=b"order=1")
    assert status == 413
    assert after == (200, {"total": 2})


@pytest.mark.parametrize("kind", ["unix", "ws"])
async def test_the_configured_address_carries_the_application(tmp_path, kind):
    # wf:contract: server.kbus(address="unix://<path>") and server.kbus(address=
    # wf:contract: "ws://127.0.0.1:<port>/kbus") both carry the spawned application:
    # wf:contract: GET /billing/total answers through it.
    directory = short_dir()
    try:
        address = (f"unix://{directory}/kbus.sock" if kind == "unix"
                   else f"ws://127.0.0.1:{free_port()}/kbus")
        config = write_recipe(tmp_path, kbus_options=f'address="{address}"')
        async with running(config) as server:
            assert await request(server, "/billing/total", query=b"order=4") == (
                200, {"total": 8})
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.parametrize("old", ["uds:/tmp/kbus.sock", "tcp:127.0.0.1:9000"])
def test_the_old_address_forms_are_a_boot_error(tmp_path, old):
    # wf:contract: an address in the retired forms uds:<path> / tcp:<host>:<port> is
    # wf:contract: refused when the server is built, with a ValueError naming unix://.
    with pytest.raises(ValueError, match="unix://"):
        AsgiServer(config=write_recipe(tmp_path, kbus_options=f'address="{old}"'))


async def test_a_wsx_message_reaches_the_spawned_application(tmp_path):
    # wf:contract: on a server whose billing is spawned, a WSX message with an id and
    # wf:contract: path "/billing/total" (data {"order": 2}), sent on a websocket opened on
    # wf:contract: the in-process application local, is answered on that socket with the
    # wf:contract: same id, status 200 and data {"total": 4}.
    async with running(write_recipe(tmp_path)) as server:
        envelope = WsxEnvelope(id="m1", method="POST", path="/billing/total",
                               data={"order": 2}).encode()
        inbox: asyncio.Queue = asyncio.Queue()
        sent: list[dict[str, Any]] = []
        answered = asyncio.Event()

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            if message["type"] == "websocket.send":
                answered.set()

        await inbox.put({"type": "websocket.connect"})
        await inbox.put({"type": "websocket.receive", "text": envelope})
        socket = asyncio.create_task(server({
            "type": "websocket", "asgi": {"version": "3.0"}, "path": "/local/_wsx",
            "raw_path": b"/local/_wsx", "root_path": "", "query_string": b"",
            "headers": [(b"host", b"127.0.0.1")], "subprotocols": [],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
        }, inbox.get, send))
        await asyncio.wait_for(answered.wait(), 10)
        await inbox.put({"type": "websocket.disconnect", "code": 1000})
        await socket
    reply = WsxEnvelope(next(m["text"] for m in sent if m["type"] == "websocket.send"))
    assert (reply.id, reply.status, reply.data) == ("m1", 200, {"total": 4})
