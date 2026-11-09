# Copyright 2026 Softwell S.r.l.
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

"""Applications mounted disabled and enabled on ``ready`` (#112, first part).

An ``AsgiServer`` registers every application but ``_server`` disabled. The
lifespan starts each one's ``on_startup`` without waiting for it; when it
returns, the server sends ``ready`` to ``_server`` over the KajennBus and the
application answers. Until then it answers 503 with ``Retry-After``. A
``FatalBootError`` of the server with ``abort_on_fatal_boot_error=False``
keeps the server up for ``_server`` only.

Requests go through the real lifespan (``LifespanRunner``) at the ASGI level.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from genro_routes import route

from kajenn import AsgiServer, Avatar, BaseApplication, BaseServer, RoutedApplication
from kajenn.db import DbHandlerBase
from tests.conftest import LifespanRunner, ask_app


class Gated(RoutedApplication):
    """An application whose activation waits on ``gate``."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gate = asyncio.Event()

    async def on_startup(self) -> None:
        await self.gate.wait()

    @route()
    def ping(self) -> dict[str, bool]:
        return {"pong": True}


class Broken(RoutedApplication):
    """An application whose activation fails."""

    def on_startup(self) -> None:
        raise ConnectionError("service unreachable")

    @route()
    def ping(self) -> dict[str, bool]:
        return {"pong": True}


class Unopenable(DbHandlerBase):
    """A database handler whose ``open`` fails: a fatal boot of the server."""

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        """Nothing to keep."""

    async def open(self) -> None:
        raise ConnectionError("database unreachable")


async def enabled(server: AsgiServer, code: str) -> None:
    """Wait until ``code`` answers: its activation runs in a task of its own."""
    async with asyncio.timeout(5):
        while not server.is_enabled(code):
            await asyncio.sleep(0.005)


def admin_bus(server: AsgiServer, path: str, data: Any) -> Any:
    """A call on the KajennBus carrying the administrator tag."""
    return server.kbus_call(path, data, auth=Avatar("ops", ["SERVER_ADMIN"]))


class TestBoot:
    async def test_an_application_answers_503_until_its_on_startup_returns(self) -> None:
        server = AsgiServer(applications=[(Gated, {"code": "gated"})])
        runner = LifespanRunner(server)
        await runner.startup()
        try:
            waiting = await ask_app(server, "/gated/ping")
            assert waiting["status"] == 503
            assert ("retry-after", "5") in waiting["headers"]
            server.applications["gated"].gate.set()
            await enabled(server, "gated")
            answered = await ask_app(server, "/gated/ping")
            assert (answered["status"], json.loads(answered["body"])) == (200, {"pong": True})
        finally:
            await runner.shutdown()

    async def test_the_server_application_answers_while_others_mount(self) -> None:
        server = AsgiServer(applications=[(Gated, {"code": "gated"})])
        runner = LifespanRunner(server)
        await runner.startup()
        try:
            assert (await ask_app(server, "/readyz"))["status"] == 200
            listed = await admin_bus(server, "/_server/applications/list", {})
            assert {row["code"]: row["state"] for row in listed} == {
                "gated": "disabled", "_server": "enabled"}
        finally:
            await runner.shutdown()

    async def test_a_failing_on_startup_leaves_the_application_disabled(self, caplog) -> None:
        server = AsgiServer(applications=[(Broken, {"code": "broken"})])
        runner = LifespanRunner(server)
        await runner.startup()
        try:
            await asyncio.sleep(0.05)
            assert (await ask_app(server, "/broken/ping"))["status"] == 503
            assert not server.is_enabled("broken")
            assert "the application stays disabled" in caplog.text
        finally:
            await runner.shutdown()

    async def test_a_base_server_serves_at_once(self) -> None:
        class Plain(RoutedApplication):
            @route()
            def ping(self) -> dict[str, bool]:
                return {"pong": True}

        server = BaseServer(applications=[Plain(code="plain")])
        assert server.is_enabled("plain")


class TestTheApplicationsSection:
    async def test_ready_asks_for_the_administrator_tag(self) -> None:
        server = AsgiServer(applications=[(Gated, {"code": "gated"})])
        runner = LifespanRunner(server)
        await runner.startup()
        try:
            refused = await ask_app(server, "/_server/applications/list")
            assert refused["status"] in (401, 403)
            assert not server.is_enabled("gated")
            assert await admin_bus(server, "/_server/applications/ready", {"code": "gated"}) == {
                "code": "gated", "state": "enabled"}
            assert (await ask_app(server, "/gated/ping"))["status"] == 200
        finally:
            await runner.shutdown()

    async def test_ready_on_an_unknown_code_is_404(self) -> None:
        server = AsgiServer(applications=[(BaseApplication, {"mount": ""})])
        runner = LifespanRunner(server)
        await runner.startup()
        try:
            try:
                await admin_bus(server, "/_server/applications/ready", {"code": "nobody"})
            except Exception as failure:  # noqa: BLE001 — the status is the contract
                assert getattr(failure, "status", None) == 404
            else:
                raise AssertionError("ready on an unknown code answered")
        finally:
            await runner.shutdown()


class TestFatalBoot:
    async def test_the_default_ends_the_startup(self) -> None:
        server = AsgiServer(applications=[(Gated, {"code": "gated"})])
        server.add_database("main", {}, Unopenable)
        runner = LifespanRunner(server)
        await runner.startup()
        assert runner.sent[-1]["type"] == "lifespan.startup.failed"
        await runner.shutdown()

    async def test_without_abort_only_the_server_application_answers(self) -> None:
        server = AsgiServer(applications=[(Gated, {"code": "gated"})],
                            abort_on_fatal_boot_error=False)
        server.add_database("main", {}, Unopenable)
        runner = LifespanRunner(server)
        await runner.startup()
        try:
            assert runner.sent[-1]["type"] == "lifespan.startup.complete"
            server.applications["gated"].gate.set()
            await enabled(server, "gated")
            refused = await ask_app(server, "/gated/ping")
            assert refused["status"] == 503
            assert "retry-after" not in dict(refused["headers"])
            assert (await ask_app(server, "/readyz"))["status"] == 503
            assert (await ask_app(server, "/livez"))["status"] == 200
        finally:
            await runner.shutdown()
