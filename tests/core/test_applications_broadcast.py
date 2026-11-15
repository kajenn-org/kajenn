# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""``server.applications_broadcast``: one ``@broadcast_hook`` called on every application.

Every test below drives a real ``AsgiServer``: the broadcast calls the hook on
every registered application, ``_server`` included, in registration order or in
reverse, collects ``[(code, result)]`` and routes the failures through
``on_error``. A route on a broadcast hook is refused when the application is
registered. An application without the hook is skipped. An application with
``spawner`` answers the same way: the mount forwards every ``@broadcast_hook`` of the real class
with ``RemoteApplication.remote_call``, and the server refuses ``remote_call``.
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


class Plain(RoutedApplication):
    """No hook: the broadcast skips it."""

    def probe_hook(self, word: str, *, suffix: str = "") -> str:
        return "never"


async def test_an_application_without_the_hook_is_skipped() -> None:
    server = await start_server(AsgiServer(applications=[
        (Beta, {"code": "beta"}), (Plain, {"code": "plain"})]))
    answers = await server.applications_broadcast("probe_hook", "x")
    assert answers == [("beta", "beta:x")]


async def test_a_hook_nobody_has_answers_an_empty_list() -> None:
    server = await probed_server()
    assert await server.applications_broadcast("total") == []
    assert await server.applications_broadcast("nothing_at_all") == []


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


class Unmarked:
    """A mixin nearer the root of the MRO than ``BaseApplication``, no decorator."""

    def on_shutdown(self) -> str:
        return "mixin"


class Mixed(RoutedApplication, Unmarked):
    def on_shutdown(self) -> str:
        return "application"


async def test_an_unmarked_mixin_definition_does_not_hide_the_mark() -> None:
    server = await start_server(AsgiServer(applications=[(Mixed, {"code": "mixed"})]))
    answers = dict(await server.applications_broadcast("on_shutdown"))
    assert answers["mixed"] == "application"


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

import kbus
from genro_routes import route

from kajenn import RoutedApplication, broadcast_hook
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Probe:
    @broadcast_hook
    def probe_hook(self, word, *, suffix=""):
        return {"code": self.code, "word": word + suffix, "pid": os.getpid()}

    @broadcast_hook
    async def failing_hook(self):
        raise LookupError("failing in the process")


class Billing(Probe, RoutedApplication):
    def on_shutdown(self):
        with open(SHUTDOWN_LOG, "a") as log:
            log.write(f"{os.getpid()}\\n")

    @route()
    async def ask_server(self):
        try:
            await self.server.kbus_member.call(
                "server.remote_call", kbus.Message({"method": "on_shutdown"}, b'"{}"'))
        except kbus.Error as failure:
            return {"refused": type(failure).__name__}
        return {"refused": None}


class Local(RoutedApplication):
    @broadcast_hook
    def local_hook(self):
        return {"code": self.code}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, DECLARATION)
        apps.application(code="local", app_class=Local)
'''

SPAWNED = 'spawner="subprocess", request_timeout=5'
INPROCESS = 'mount="billing"'


def write_recipe(tmp_path, declaration: str = SPAWNED) -> tuple[str, Any]:
    shutdown_log = tmp_path / "shutdown.log"
    path = tmp_path / "config.py"
    path.write_text(RECIPE.replace("DECLARATION", declaration)
                    .replace("SHUTDOWN_LOG", repr(str(shutdown_log))))
    return str(path), shutdown_log


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
            while not all(server.is_enabled(code) for code in server.applications):
                await asyncio.sleep(0.05)
        yield server
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await lifespan


async def test_a_hook_of_a_spawned_application_answers_through_the_broadcast(tmp_path) -> None:
    config, _ = write_recipe(tmp_path)
    async with running(config) as server:
        spawned = await server.applications_broadcast("probe_hook", "hi", suffix="!")
    (tmp_path / "inproc").mkdir()
    config, _ = write_recipe(tmp_path / "inproc", INPROCESS)
    async with running(config) as server:
        inprocess = await server.applications_broadcast("probe_hook", "hi", suffix="!")
    [(spawned_code, spawned_answer)] = spawned
    [(inprocess_code, inprocess_answer)] = inprocess
    assert spawned_code == inprocess_code == "billing"
    assert spawned_answer.pop("pid") != os.getpid()
    assert inprocess_answer.pop("pid") == os.getpid()
    assert spawned_answer == inprocess_answer == {"code": "billing", "word": "hi!"}


async def test_a_hook_that_raises_in_the_process_reaches_on_error(tmp_path) -> None:
    config, _ = write_recipe(tmp_path)
    seen: list[tuple[str, Exception]] = []
    async with running(config) as server:
        answers = await server.applications_broadcast(
            "failing_hook", on_error=lambda code, exc: seen.append((code, exc)))
    assert answers == []
    [(code, failure)] = seen
    assert code == "billing"
    assert isinstance(failure, kbus.RemoteError)
    assert "LookupError: failing in the process" in str(failure)


async def test_a_spawned_application_without_the_hook_is_skipped(tmp_path) -> None:
    config, _ = write_recipe(tmp_path)
    async with running(config) as server:
        only_local = await server.applications_broadcast("local_hook")
        nobody = await server.applications_broadcast("ask_server")
    assert only_local == [("local", {"code": "local"})]
    assert nobody == []


async def test_remote_call_returns_the_result_of_the_method(tmp_path) -> None:
    config, _ = write_recipe(tmp_path)
    async with running(config) as server:
        answer = await server.applications["billing"].remote_call("probe_hook", "a", suffix="b")
    assert answer["word"] == "ab"
    assert answer["pid"] != os.getpid()


async def test_the_server_refuses_remote_call(tmp_path) -> None:
    config, _ = write_recipe(tmp_path)
    async with running(config) as server:
        assert await server.kbus_call("/billing/ask_server") == {"refused": "Refused"}


async def test_on_shutdown_of_a_spawned_application_runs_once(tmp_path) -> None:
    config, shutdown_log = write_recipe(tmp_path)
    async with running(config):
        pass
    assert len(shutdown_log.read_text().splitlines()) == 1


TWO_SPAWNED = '''
import os

import kbus
from genro_routes import route
from genro_tytx import to_tytx

from kajenn import RoutedApplication, broadcast_hook
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Asker(RoutedApplication):
    @route()
    async def ask_other(self):
        payload = to_tytx({"args": ["w"], "kwargs": {}}, "json").encode()
        try:
            await self.server.kbus_member.call(
                "other.remote_call", kbus.Message({"method": "probe_hook"}, payload))
        except kbus.Error as failure:
            return {"refused": type(failure).__name__}
        return {"refused": None}


class Other(RoutedApplication):
    @broadcast_hook
    def probe_hook(self, word):
        return {"word": word, "pid": os.getpid()}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="asker", app_class=Asker, spawner="subprocess",
                         request_timeout=5)
        apps.application(code="other", app_class=Other, spawner="subprocess",
                         request_timeout=5)
'''


async def test_a_spawned_process_cannot_remote_call_another(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(TWO_SPAWNED)
    async with running(str(config)) as server:
        refused = await server.kbus_call("/asker/ask_other")
        answer = await server.applications["other"].remote_call("probe_hook", "w")
    assert refused == {"refused": "Refused"}
    assert answer["word"] == "w"
    assert answer["pid"] != os.getpid()
