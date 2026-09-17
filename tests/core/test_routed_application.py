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

"""RoutedApplication tests (Macro 4 Phase 4).

Every request drives a REAL ``AsgiServer`` composition at the ASGI level
(no uvicorn): the app is the primary, ``ErrorMiddleware`` is armed by
default, sync handlers cross the server pool. The GET-side helpers come from
``tests/conftest.py``; ``json_request`` (local) adds a JSON body.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

import pytest
from genro_routes import RoutingClass, route

from kajenn import AsgiServer, Avatar, BaseMiddleware, RoutedApplication
from kajenn.types import Message, Scope


class DemoApp(RoutedApplication):
    """Test app: sync/async handlers, an auth-ruled one, typed metadata."""

    @route()
    def hello(self) -> dict[str, str]:
        return {"hello": "world"}

    @route()
    async def ahello(self) -> dict[str, str]:
        return {"hello": "async"}

    @route()
    def echo(self, a: Any = None, b: Any = None) -> dict[str, Any]:
        return {"a": a, "b": b}

    @route(auth_rule="admin")
    def restricted(self) -> dict[str, bool]:
        return {"secret": True}

    @route()
    def sync_ident(self) -> int:
        return threading.get_ident()

    @route()
    async def async_ident(self) -> int:
        return threading.get_ident()

    @route(media_type="text/html")
    def page(self) -> str:
        return "<h1>hi</h1>"

    @route()
    def wrapped(self) -> Any:
        return self.result_wrapper("<p>meta</p>", media_type="text/html")


class TypedApp(RoutedApplication):
    """Pydantic-plugged app: the body-spread reconciliation has fields to read."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.plug("pydantic")

    @route()
    def add(self, x: int = 0, y: int = 0) -> dict[str, int]:
        return {"sum": x + y}

    @route()
    def raw(self, body_data: dict | None = None) -> dict[str, Any]:
        return {"body": body_data}


class SubApi(RoutingClass):
    """External RoutingClass mounted into an app via ``add_branches`` (instance form)."""

    @route()
    def ping(self) -> dict[str, bool]:
        return {"sub": True}


class StampAuthMiddleware(BaseMiddleware):
    """Test middleware: stamps a fixed identity on ``scope["auth"]``.

    Order 500: the AsgiServer composition arms the real ``AuthMiddleware``
    (450), which resolves the scope identity itself — the stamp must run
    after it so the fixed identity wins.
    """

    middleware_order = 500

    def __init__(self, app: Any, server: Any, *, avatar: Avatar | None = None, **options: Any):
        self._avatar = avatar
        super().__init__(app, server, **options)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope["auth"] = self._avatar
        await self.app(scope, receive, send)


def auth_server(entry: tuple[type, dict], avatar: Avatar | None) -> AsgiServer:
    """An AsgiServer whose chain stamps ``avatar`` as the request identity."""
    return AsgiServer(
        applications=[entry],
        middleware={"stamp": {"avatar": avatar}},
        middleware_registry={"stamp": StampAuthMiddleware},
    )


@pytest.fixture
def json_request() -> Callable[..., object]:
    """Fixture: drive one JSON-body request through a server at the ASGI level."""

    async def _json_request(
        server: object, path: str, body: bytes, method: str = "POST"
    ) -> list[Message]:
        scope: Scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)  # type: ignore[operator]
        return sent

    return _json_request


@pytest.fixture
def query_request() -> Callable[..., object]:
    """Fixture: drive one GET request carrying a query string through a server."""

    async def _query_request(server: object, path: str, query: bytes) -> list[Message]:
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query,
            "headers": [],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)  # type: ignore[operator]
        return sent

    return _query_request


class TestDispatch:
    async def test_sync_route_answers_json(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/hello")
        assert response_status(sent) == 200
        assert response_headers(sent)[b"content-type"] == b"application/json"
        assert response_body(sent) == b'{"hello":"world"}'

    async def test_async_route_answers_json(
        self, http_request, response_status, response_body
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/ahello")
        assert response_status(sent) == 200
        assert response_body(sent) == b'{"hello":"async"}'

    async def test_query_params_reach_handler_kwargs(self, response_body) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/echo",
            "query_string": b"a=1&b=two",
            "headers": [],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)
        assert response_body(sent) == b'{"a":1,"b":"two"}'

    async def test_unknown_path_is_404_via_error_middleware(
        self, http_request, response_status
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/nowhere")
        assert response_status(sent) == 404

    async def test_metadata_media_type_reaches_the_response(
        self, http_request, response_headers, response_body
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/page")
        assert response_headers(sent)[b"content-type"] == b"text/html; charset=utf-8"
        assert response_body(sent) == b"<h1>hi</h1>"

    async def test_result_wrapper_metadata_wins(
        self, http_request, response_headers, response_body
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/wrapped")
        assert response_headers(sent)[b"content-type"] == b"text/html; charset=utf-8"
        assert response_body(sent) == b"<p>meta</p>"

    async def test_unmounted_app_refuses_dispatch(self) -> None:
        app = DemoApp()

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            raise AssertionError("nothing must be sent")

        with pytest.raises(RuntimeError):
            await app({"type": "http", "method": "GET", "path": "/hello"}, receive, send)


class TestBodyBinding:
    async def test_json_body_spread_over_params(
        self, json_request, response_status, response_body
    ) -> None:
        server = AsgiServer(applications=[(TypedApp, {"mount": ""})])
        sent = await json_request(server, "/add", b'{"x": 1, "y": 2, "extra": 9}')
        assert response_status(sent) == 200
        assert response_body(sent) == b'{"sum":3}'

    async def test_body_data_kept_whole_when_declared(self, json_request, response_body) -> None:
        server = AsgiServer(applications=[(TypedApp, {"mount": ""})])
        sent = await json_request(server, "/raw", b'{"x": 1}')
        assert response_body(sent) == b'{"body":{"x":1}}'


class TestArgumentErrors:
    """Bad handler arguments surface as an HTTP answer, never 500.

    genro-routes keeps the two failures apart: an unbindable extra argument is
    a ``signature_error``, an uncoercible typed argument is a
    ``validation_error``. The dispatcher catches one marker per code and both
    answer 400 under the strict reading, the application default (issue #87);
    an application declaring the FastAPI convention answers 422 to the second
    (``tests/core/test_body_decoding.py``).
    """

    async def test_uncoercible_typed_arg_is_400(self, query_request, response_status) -> None:
        server = AsgiServer(applications=[(TypedApp, {"mount": ""})])
        sent = await query_request(server, "/add", b"x=abc")
        assert response_status(sent) == 400

    async def test_unbindable_extra_arg_is_400(self, query_request, response_status) -> None:
        server = AsgiServer(applications=[(TypedApp, {"mount": ""})])
        sent = await query_request(server, "/add", b"x=1&y=2&z=99")
        assert response_status(sent) == 400


class TestAuth:
    async def test_anonymous_is_401_on_ruled_entry(self, http_request, response_status) -> None:
        """No identity presented: the server asks who is calling, it does not refuse."""
        server = auth_server((DemoApp, {"mount": ""}), avatar=None)
        sent = await http_request(server, "/restricted")
        assert response_status(sent) == 401

    async def test_wrong_tags_are_403(self, http_request, response_status) -> None:
        server = auth_server((DemoApp, {"mount": ""}), avatar=Avatar("bob", ["viewer"]))
        sent = await http_request(server, "/restricted")
        assert response_status(sent) == 403

    async def test_matching_tag_is_200(self, http_request, response_status, response_body) -> None:
        server = auth_server((DemoApp, {"mount": ""}), avatar=Avatar("alice", ["admin"]))
        sent = await http_request(server, "/restricted")
        assert response_status(sent) == 200
        assert response_body(sent) == b'{"secret":true}'

    async def test_untagged_entry_stays_public_without_middleware(
        self, http_request, response_status
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/hello")
        assert response_status(sent) == 200

    async def test_ruled_entry_denied_without_middleware(
        self, http_request, response_status
    ) -> None:
        """Default-deny with no auth chain at all: nobody was presented, so 401."""
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/restricted")
        assert response_status(sent) == 401


class TestSubTrees:
    async def test_attached_instance_reachable_under_its_name(
        self, http_request, response_status, response_body
    ) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        app = server.applications["demoapp"]
        app.route.add_branches({"name": "sub", "instance": SubApi()})
        sent = await http_request(server, "/sub/ping")
        assert response_status(sent) == 200
        assert response_body(sent) == b'{"sub":true}'


class TestExecutionVehicle:
    async def test_sync_handler_runs_through_the_pool(self, http_request, response_body) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/sync_ident")
        assert int(response_body(sent)) != threading.get_ident()

    async def test_async_handler_stays_on_the_loop(self, http_request, response_body) -> None:
        server = AsgiServer(applications=[(DemoApp, {"mount": ""})])
        sent = await http_request(server, "/async_ident")
        assert int(response_body(sent)) == threading.get_ident()


class CleanupApp(RoutedApplication):
    """Test app: ``route_cleanup`` records the thread it ran on, per dispatch."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cleanups: list[int] = []

    @route()
    def sync_ident(self) -> int:
        return threading.get_ident()

    @route()
    async def async_hello(self) -> dict[str, str]:
        return {"hello": "async"}

    @route()
    def failing(self) -> None:
        raise RuntimeError("boom")

    def route_cleanup(self) -> None:
        self.cleanups.append(threading.get_ident())


class TestRouteCleanup:
    async def test_the_cleanup_runs_on_the_handler_thread(
        self, http_request, response_body
    ) -> None:
        server = AsgiServer(applications=[(CleanupApp, {"mount": ""})])
        app = server.applications["cleanupapp"]
        sent = await http_request(server, "/sync_ident")
        # Same dispatch, same pool thread: what the handler opened, the
        # cleanup can close.
        assert app.cleanups == [int(response_body(sent))]

    async def test_the_cleanup_runs_when_the_handler_raises(
        self, http_request, response_status
    ) -> None:
        server = AsgiServer(applications=[(CleanupApp, {"mount": ""})])
        app = server.applications["cleanupapp"]
        sent = await http_request(server, "/failing")
        assert response_status(sent) == 500
        assert len(app.cleanups) == 1

    async def test_the_async_path_never_cleans(self, http_request, response_status) -> None:
        server = AsgiServer(applications=[(CleanupApp, {"mount": ""})])
        app = server.applications["cleanupapp"]
        sent = await http_request(server, "/async_hello")
        assert response_status(sent) == 200
        assert app.cleanups == []


class TestBodyTypeError:
    """The handler body is mapped to no error code: its TypeError is a 500.

    Binding happens before the handler runs, so a body failure is never
    mistaken for a bad call — sync path and async path alike.
    """

    async def test_async_handler_body_typeerror_is_500(
        self, http_request, response_status
    ) -> None:
        class Exploding(RoutedApplication):
            @route()
            async def boom(self) -> dict:
                raise TypeError("async body failure")

        server = AsgiServer(applications=[(Exploding, {"mount": ""})])
        sent = await http_request(server, "/boom")
        assert response_status(sent) == 500

    async def test_sync_handler_body_typeerror_is_500(
        self, http_request, response_status
    ) -> None:
        class Exploding(RoutedApplication):
            @route()
            def boom(self) -> dict:
                raise TypeError("sync body failure")

        server = AsgiServer(applications=[(Exploding, {"mount": ""})])
        sent = await http_request(server, "/boom")
        assert response_status(sent) == 500
