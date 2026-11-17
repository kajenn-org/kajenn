# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The WSX message and websocket connection families, counted in the server.

Every test drives a real ``AsgiServer`` through its ASGI entry point and reads
``/_server/metrics`` with an API key whose identity holds only
``SERVER_METRICS``.

What the suite pins:

- every WSX message handed to an application counts with ``application``,
  ``route`` (the route name the application resolved) and ``status``, for an
  in-process and a ``spawner="subprocess"`` application; the duration
  histogram counts the same messages; the ``/_wsx/ping`` answered inline is
  not counted; a message to a switched-off application counts its 503 with
  ``application=""`` (the public ``demux`` returns the refusal, not the
  application);
- a websocket handed to ``WsxConnection`` counts one opened and one live
  connection of kind ``wsx`` while it is open, live back to 0 after it
  closes; a websocket handed to ``serve_websocket`` counts as kind ``raw``;
  both kinds count with the code of a spawned application too.
"""

from __future__ import annotations

import asyncio
from typing import Any

from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.wsx import WsxEnvelope
from tests.conftest import read_samples, running, start_server, value
from tests.core.test_websocket_raw_seam import EchoSocketApp
from tests.storage_support import auth_application

MESSAGES = "kajenn_wsx_messages_total"
DURATION_COUNT = "kajenn_wsx_message_duration_seconds_count"
OPENED = "kajenn_websocket_connections_opened_total"
LIVE = "kajenn_websocket_connections_live"


class Desk(RoutedApplication):
    """Routes on the ``wsx`` channel: one answers, one raises."""

    @route(channel_channels="api,wsx")
    def ping(self) -> dict[str, bool]:
        return {"pong": True}

    @route(channel_channels="api,wsx")
    def lost(self) -> None:
        raise LookupError("lost")


class Socket:
    """One websocket through the server's ASGI entry point, open until ``close``."""

    def __init__(self, server: AsgiServer, path: str) -> None:
        self.server = server
        self.path = path
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.answers: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] == "websocket.send":
            await self.answers.put(WsxEnvelope(message["text"]))

    async def open(self) -> None:
        await self.inbox.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(self.server({
            "type": "websocket", "asgi": {"version": "3.0"}, "path": self.path,
            "raw_path": self.path.encode(), "root_path": "", "query_string": b"",
            "headers": [(b"host", b"127.0.0.1")], "subprotocols": [],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
        }, self.inbox.get, self.send))

    async def ask(self, ident: str, path: str) -> WsxEnvelope:
        """Send one message with an ``id`` and wait for its answer."""
        envelope = WsxEnvelope(id=ident, method="WSK", path=path, data={})
        await self.inbox.put({"type": "websocket.receive", "text": envelope.encode()})
        return await asyncio.wait_for(self.answers.get(), 10)

    async def close(self) -> None:
        await self.inbox.put({"type": "websocket.disconnect", "code": 1000})
        assert self.task is not None
        await self.task


async def desk_server() -> AsgiServer:
    return await start_server(AsgiServer(
        applications=[auth_application(), (Desk, {"code": "desk"})]))


def counted_messages(samples: list[Any]) -> list[tuple[str, str, str, float]]:
    return sorted((sample.labels["application"], sample.labels["route"],
                   sample.labels["status"], sample.value)
                  for sample in samples if sample.name == MESSAGES)


async def test_messages_to_an_in_process_application_are_counted() -> None:
    server = await desk_server()
    socket = Socket(server, "/desk/_wsx")
    await socket.open()
    assert (await socket.ask("1", "/desk/ping")).status == 200
    assert (await socket.ask("2", "/desk/ping")).status == 200
    assert (await socket.ask("3", "/desk/lost")).status == 500
    assert (await socket.ask("4", "/desk/nothing")).status == 404
    assert (await socket.ask("5", "/_wsx/ping")).status == 200
    await socket.close()
    samples = await read_samples(server)
    assert counted_messages(samples) == [
        ("desk", "", "404", 1.0), ("desk", "lost", "500", 1.0), ("desk", "ping", "200", 2.0)]
    assert value(samples, DURATION_COUNT, application="desk", route="ping") == 2


async def test_a_message_to_a_switched_off_application_counts_without_application() -> None:
    server = await start_server(AsgiServer(
        applications=[auth_application(), (Desk, {"code": "desk"}), (Desk, {"code": "other"})]))
    server.disable_application("other")
    socket = Socket(server, "/desk/_wsx")
    await socket.open()
    assert (await socket.ask("1", "/other/ping")).status == 503
    await socket.close()
    samples = await read_samples(server)
    assert counted_messages(samples) == [("", "", "503", 1.0)]


async def test_a_wsx_connection_is_opened_and_live_until_it_closes() -> None:
    server = await desk_server()
    socket = Socket(server, "/desk/_wsx")
    await socket.open()
    assert (await socket.ask("1", "/desk/ping")).status == 200
    samples = await read_samples(server)
    assert value(samples, OPENED, application="desk", kind="wsx") == 1
    assert value(samples, LIVE, application="desk", kind="wsx") == 1
    await socket.close()
    samples = await read_samples(server)
    assert value(samples, OPENED, application="desk", kind="wsx") == 1
    assert value(samples, LIVE, application="desk", kind="wsx") == 0


async def test_a_raw_connection_counts_as_raw() -> None:
    server = await start_server(AsgiServer(
        applications=[auth_application(), (EchoSocketApp, {"code": "raw"})]))
    inbox: asyncio.Queue = asyncio.Queue()
    echoed = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "websocket.send":
            echoed.set()

    await inbox.put({"type": "websocket.connect"})
    await inbox.put({"type": "websocket.receive", "text": "hello"})
    task = asyncio.create_task(server(
        {"type": "websocket", "path": "/raw/socket", "headers": []}, inbox.get, send))
    await asyncio.wait_for(echoed.wait(), 10)
    samples = await read_samples(server)
    assert value(samples, OPENED, application="raw", kind="raw") == 1
    assert value(samples, LIVE, application="raw", kind="raw") == 1
    await inbox.put({"type": "websocket.disconnect", "code": 1000})
    await task
    samples = await read_samples(server)
    assert value(samples, LIVE, application="raw", kind="raw") == 0


RECIPE = '''
from genro_routes import route

from kajenn import AuthApplicationBase, RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryAuthStore


class Desk(RoutedApplication):
    @route(channel_channels="api,wsx")
    def ping(self):
        return {"pong": True}


class Billing(RoutedApplication):
    @route(channel_channels="api,wsx")
    def invoice(self):
        return {"number": 7}


class Pipe(RoutedApplication):
    async def serve_websocket(self, scope, receive, send):
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            await send({"type": "websocket.send", "text": message["text"]})


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        auth = apps.application(code="_auth", app_class=AuthApplicationBase)
        auth.store(store_class=MemoryAuthStore)
        apps.application(code="desk", app_class=Desk)
        apps.application(code="billing", app_class=Billing,
                         spawner="subprocess", request_timeout=5)
        apps.application(code="pipe", app_class=Pipe,
                         spawner="subprocess", request_timeout=5)
'''


async def test_messages_to_a_spawned_application_are_counted(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        socket = Socket(server, "/desk/_wsx")
        await socket.open()
        assert (await socket.ask("1", "/billing/invoice")).status == 200
        assert (await socket.ask("2", "/desk/ping")).status == 200
        await socket.close()
        samples = await read_samples(server)
    assert counted_messages(samples) == [
        ("billing", "invoice", "200", 1.0), ("desk", "ping", "200", 1.0)]
    assert value(samples, DURATION_COUNT, application="billing", route="invoice") == 1


async def test_connections_to_spawned_applications_are_counted(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        socket = Socket(server, "/billing/_wsx")
        await socket.open()
        assert (await socket.ask("1", "/billing/invoice")).status == 200
        inbox: asyncio.Queue = asyncio.Queue()
        echoed = asyncio.Event()

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "websocket.send":
                echoed.set()

        await inbox.put({"type": "websocket.connect"})
        await inbox.put({"type": "websocket.receive", "text": "hello"})
        raw = asyncio.create_task(server(
            {"type": "websocket", "path": "/pipe/socket", "headers": [],
             "query_string": b""}, inbox.get, send))
        await asyncio.wait_for(echoed.wait(), 10)
        live = await read_samples(server)
        await socket.close()
        await inbox.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(raw, 10)
        closed = await read_samples(server)
    assert value(live, LIVE, application="billing", kind="wsx") == 1
    assert value(live, LIVE, application="pipe", kind="raw") == 1
    assert value(closed, OPENED, application="billing", kind="wsx") == 1
    assert value(closed, OPENED, application="pipe", kind="raw") == 1
    assert value(closed, LIVE, application="billing", kind="wsx") == 0
    assert value(closed, LIVE, application="pipe", kind="raw") == 0
