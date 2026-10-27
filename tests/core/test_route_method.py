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

"""The HTTP method a route declares is the only one it answers.

A route declaring ``openapi_method`` answers an HTTP request on another method
with 405 and an ``Allow`` header naming the declared one; the handler never
runs. A route declaring none answers every method, as before. The check is the
HTTP face's: a call through the KajennBus is not an HTTP request and reaches
the handler whatever the route declares.
"""

from __future__ import annotations

import json
from typing import Any

from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.types import Message, Scope


class Orders(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    @route(openapi_method="post")
    def submit(self, item: str = "") -> dict[str, str]:
        self.calls.append("submit")
        return {"submitted": item}

    @route(openapi_method="delete")
    def remove(self, item: str = "") -> dict[str, str]:
        self.calls.append("remove")
        return {"removed": item}

    @route(openapi_method="get")
    def lookup(self, item: str = "") -> dict[str, str]:
        self.calls.append("lookup")
        return {"found": item}

    @route()
    def free(self, item: str = "") -> dict[str, str]:
        self.calls.append("free")
        return {"free": item}


def make_server() -> AsgiServer:
    return AsgiServer(applications=[(Orders, {"code": "orders"})])


def orders(server: AsgiServer) -> Orders:
    return server.applications["orders"]


async def drive(server: AsgiServer, method: str, path: str) -> list[Message]:
    path, _, query = path.partition("?")
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def status_of(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def allow_of(sent: list[Message]) -> bytes | None:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"]).get(b"allow")


def body_of(sent: list[Message]) -> Any:
    return json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))


class TestDeclaredMethod:
    async def test_get_on_a_post_route_is_405_with_allow_and_never_runs(self) -> None:
        server = make_server()
        sent = await drive(server, "GET", "/orders/submit?item=book")
        assert status_of(sent) == 405
        assert allow_of(sent) == b"POST"
        assert orders(server).calls == []

    async def test_post_on_a_post_route_runs(self) -> None:
        server = make_server()
        sent = await drive(server, "POST", "/orders/submit?item=book")
        assert status_of(sent) == 200
        assert body_of(sent) == {"submitted": "book"}

    async def test_the_declared_method_is_matched_case_insensitively(self) -> None:
        server = make_server()
        sent = await drive(server, "post", "/orders/submit?item=book")
        assert status_of(sent) == 200

    async def test_post_on_a_delete_route_is_405_naming_delete(self) -> None:
        server = make_server()
        sent = await drive(server, "POST", "/orders/remove?item=book")
        assert status_of(sent) == 405
        assert allow_of(sent) == b"DELETE"
        assert orders(server).calls == []

    async def test_post_on_a_get_route_is_405_naming_get(self) -> None:
        server = make_server()
        sent = await drive(server, "POST", "/orders/lookup?item=book")
        assert status_of(sent) == 405
        assert allow_of(sent) == b"GET"


class TestUndeclaredMethod:
    async def test_a_route_without_a_declaration_answers_every_method(self) -> None:
        server = make_server()
        for method in ("GET", "POST", "PUT", "DELETE"):
            sent = await drive(server, method, "/orders/free?item=book")
            assert status_of(sent) == 200, method
            assert allow_of(sent) is None


class TestNotHttp:
    async def test_a_kbus_call_reaches_a_get_route(self) -> None:
        server = make_server()
        assert await server.kbus_call("/orders/lookup", {"item": "book"}) == {"found": "book"}

    async def test_discovery_still_lists_routes_of_every_method(self) -> None:
        server = make_server()
        scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        listing = await orders(server).discover(scope)
        assert {"submit", "remove", "lookup", "free"} <= set(listing["entries"])
