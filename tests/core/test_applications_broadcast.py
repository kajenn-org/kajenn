# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""``server.applications_broadcast``: one ``@broadcast_hook`` called on every application.

Every test below drives a real ``AsgiServer``: the broadcast calls the hook on
every registered application, ``_server`` included, in registration order or in
reverse, collects ``[(code, result)]`` and routes the failures through
``on_error``. A route on a broadcast hook is refused when the application is
registered. An application with ``spawner`` runs the hook in its own process
through ``RemoteApplication.forward_hook``.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import kbus
import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication, broadcast_hook
from kajenn.server_app.server_app import ServerApplication
from tests.conftest import start_server


class Probe:
    """The test hook, marked once here."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    @broadcast_hook
    def probe_hook(self, word: str, *, suffix: str = "") -> str:
        self.calls.append(word)
        if word == "fail" and self.code == "beta":
            raise LookupError("beta failed")
        return f"{self.code}:{word}{suffix}"


class Alpha(Probe, RoutedApplication):
    """Overrides the hook as async, without repeating the decorator."""

    async def probe_hook(self, word: str, *, suffix: str = "") -> str:
        await asyncio.sleep(0)
        return "async " + super().probe_hook(word, suffix=suffix)

    @route()
    def total(self) -> int:
        return 1


class Beta(Probe, RoutedApplication):
    pass


class Gamma(Probe, RoutedApplication):
    pass


class ProbeServerApplication(Probe, ServerApplication):
    pass


async def probed_server() -> AsgiServer:
    return await start_server(AsgiServer(applications=[
        (ProbeServerApplication, {"code": "_server"}),
        (Alpha, {"code": "alpha"}),
        (Beta, {"code": "beta"}),
        (Gamma, {"code": "gamma"}),
    ]))


def expected(code: str, word: str, suffix: str = "") -> str:
    prefix = "async " if code == "alpha" else ""
    return f"{prefix}{code}:{word}{suffix}"


async def test_every_application_answers_in_registration_order() -> None:
    server = await probed_server()
    answers = await server.applications_broadcast("probe_hook", "hi", suffix="!")
    codes = list(server.applications)
    assert "_server" in codes
    assert answers == [(code, expected(code, "hi", "!")) for code in codes]


async def test_reverse_calls_them_in_reverse_order() -> None:
    server = await probed_server()
    answers = await server.applications_broadcast("probe_hook", "hi", reverse=True)
    assert [code for code, _ in answers] == list(reversed(server.applications))


async def test_an_override_without_the_decorator_stays_subscribed() -> None:
    server = await probed_server()
    answers = dict(await server.applications_broadcast("probe_hook", "x"))
    assert answers["alpha"] == "async alpha:x"


async def test_a_disabled_application_is_called_too() -> None:
    server = await probed_server()
    server.disable_application("beta")
    answers = dict(await server.applications_broadcast("probe_hook", "x"))
    assert answers["beta"] == "beta:x"


async def test_a_method_that_is_not_a_broadcast_hook_is_refused() -> None:
    server = await probed_server()
    with pytest.raises(ValueError, match="total is not a broadcast hook"):
        await server.applications_broadcast("total")
    with pytest.raises(ValueError, match="nothing_at_all is not a broadcast hook"):
        await server.applications_broadcast("nothing_at_all")
    assert server.applications["alpha"].calls == []


async def test_without_on_error_the_first_exception_stops_the_broadcast() -> None:
    server = await probed_server()
    with pytest.raises(LookupError, match="beta failed"):
        await server.applications_broadcast("probe_hook", "fail")
    assert server.applications["gamma"].calls == []


async def test_on_error_that_returns_lets_the_broadcast_go_on() -> None:
    server = await probed_server()
    seen: list[tuple[str, Exception]] = []
    answers = await server.applications_broadcast(
        "probe_hook", "fail", on_error=lambda code, exc: seen.append((code, exc)))
    assert [code for code, _ in answers] == [c for c in server.applications if c != "beta"]
    assert [(code, str(exc)) for code, exc in seen] == [("beta", "beta failed")]
    assert server.applications["gamma"].calls == ["fail"]


async def test_on_error_that_raises_stops_the_broadcast() -> None:
    server = await probed_server()

    def refuse(code: str, exc: Exception) -> None:
        raise RuntimeError(f"{code} refused") from exc

    with pytest.raises(RuntimeError, match="beta refused"):
        await server.applications_broadcast("probe_hook", "fail", on_error=refuse)
    assert server.applications["gamma"].calls == []


class RoutedHook(Probe, RoutedApplication):
    @route()
    def probe_hook(self, word: str = "", *, suffix: str = "") -> str:
        return word


class RoutedShutdown(RoutedApplication):
    @route()
    def on_shutdown(self) -> None:
        return None


@pytest.mark.parametrize("app_class", [RoutedHook, RoutedShutdown])
def test_a_route_on_a_broadcast_hook_is_refused_at_registration(app_class: type) -> None:
    with pytest.raises(ValueError, match="is the broadcast hook"):
        AsgiServer(applications=[(app_class, {"code": "clash"})])


class Shutting(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        self.events: list[str] = kwargs.pop("events")
        super().__init__(**kwargs)

    async def on_shutdown(self) -> None:
        self.events.append(self.code)


async def test_the_shutdown_still_runs_on_shutdown_in_reverse_order() -> None:
    events: list[str] = []
    server = AsgiServer(applications=[
        (Shutting, {"code": "first", "events": events}),
        (Shutting, {"code": "second", "events": events}),
    ])
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    await inbox.put({"type": "lifespan.shutdown"})
    assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
    await lifespan
    assert events == ["second", "first"]


RECIPE = '''
import os

from genro_routes import route

from kajenn import RoutedApplication, broadcast_hook
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Probe:
    @broadcast_hook
    def probe_hook(self):
        return {"code": self.code, "pid": os.getpid()}

    @broadcast_hook
    def failing_hook(self):
        raise LookupError("failing in the process")


class Billing(Probe, RoutedApplication):
    @route()
    def plain(self):
        return {"plain": True}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess",
                         request_timeout=5)
'''


@asynccontextmanager
async def running(config: str):
    server = AsgiServer(config=config)
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        async with asyncio.timeout(30):
            while not server.is_enabled("billing"):
                await asyncio.sleep(0.05)
        yield server
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await lifespan


async def test_forward_hook_runs_the_hook_in_the_spawned_process(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        answer = await server.applications["billing"].forward_hook("probe_hook")
    assert answer["code"] == "billing"
    assert answer["pid"] != os.getpid()


async def test_a_hook_that_raises_in_the_process_reaches_the_proxy(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        proxy = server.applications["billing"]
        with pytest.raises(kbus.RemoteError, match="LookupError: failing in the process"):
            await proxy.forward_hook("failing_hook")
        with pytest.raises(kbus.RemoteError, match="plain is not a broadcast hook"):
            await proxy.forward_hook("plain")
