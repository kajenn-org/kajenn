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

"""Standard middlewares tests (Macro 2 Phase 3 + Macro 4 Phase 3 deferrals).

Same ASGI-level driving style as ``tests/test_middleware.py`` (no uvicorn):
a canned http scope, a recording ``send``, chain assembled through
``MiddlewareMixin`` composed over ``BaseServer``. The request-driving and
message-reading helpers now live in ``tests/conftest.py`` as fixtures
(``http_request``, ``response_status``, ``response_headers``, ``response_body``).

The Macro 4 Phase 3 additions cover the ``ErrorMiddleware`` on the real
``Response`` class (wire equivalence, forwarded exception headers, the
response-started guard) and the two previously-untested CORS branches
(restricted origins rejecting a foreign origin).
"""

from __future__ import annotations

import json
import logging

import pytest

from kajenn import (
    AsgiServer,
    BaseApplication,
    BaseServer,
    MemorySessionStore,
    MiddlewareMixin,
)
from kajenn.exceptions import HTTPNotFound, HTTPUnauthorized, Redirect
from kajenn.types import Message, Receive, Scope, Send


class MwServer(MiddlewareMixin, BaseServer):
    """Phase 2 composition: middleware capability over the base server."""


class RoutedApp(BaseApplication):
    """Test app: a plain 200 for every path it is given."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": f"ok:{scope['path']}".encode()})


class TestCORSMiddleware:
    async def test_preflight_returns_cors_headers(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(applications=[RoutedApp(mount="")], middleware={"cors": True})
        sent = await http_request(
            server, "/", method="OPTIONS", headers=[(b"origin", b"https://example.test")]
        )
        assert response_status(sent) in (200, 204)
        headers = response_headers(sent)
        assert headers[b"access-control-allow-origin"] == b"*"
        assert b"access-control-allow-methods" in headers

    async def test_simple_get_carries_allow_origin_header(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(applications=[RoutedApp(mount="")], middleware={"cors": True})
        sent = await http_request(server, "/", headers=[(b"origin", b"https://example.test")])
        assert response_status(sent) == 200
        assert response_headers(sent)[b"access-control-allow-origin"] == b"*"

    async def test_credentialed_wildcard_echoes_origin_with_vary(
        self, http_request, response_headers
    ) -> None:
        server = MwServer(applications=[RoutedApp(mount="")], middleware={"cors": {"allow_credentials": True}})
        sent = await http_request(server, "/", headers=[(b"origin", b"https://example.test")])
        headers = response_headers(sent)
        assert headers[b"access-control-allow-origin"] == b"https://example.test"
        assert headers[b"vary"] == b"Origin"
        assert headers[b"access-control-allow-credentials"] == b"true"

    async def test_restricted_origins_reject_a_foreign_origin(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(
            applications=[RoutedApp(mount="")], middleware={"cors": {"allow_origins": ["https://allowed.test"]}}
        )
        sent = await http_request(server, "/", headers=[(b"origin", b"https://foreign.test")])
        assert response_status(sent) == 200
        assert b"access-control-allow-origin" not in response_headers(sent)

    async def test_preflight_disallowed_origin_returns_400(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(
            applications=[RoutedApp(mount="")], middleware={"cors": {"allow_origins": ["https://allowed.test"]}}
        )
        sent = await http_request(
            server, "/", method="OPTIONS", headers=[(b"origin", b"https://foreign.test")]
        )
        assert response_status(sent) == 400
        assert b"access-control-allow-origin" not in response_headers(sent)


class RaisingApp(BaseApplication):
    """Test app raising the control-flow exceptions the errors middleware maps."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        if path == "/missing":
            raise HTTPNotFound("nothing here")
        if path == "/old":
            raise Redirect("/new")
        if path == "/challenge":
            raise HTTPUnauthorized("no", headers=[(b"www-authenticate", b"Bearer")])
        raise RuntimeError("boom")


class StartThenRaiseApp(BaseApplication):
    """Test app that starts the response, then raises — nothing more can be sent."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("boom after start")


class TestErrorMiddlewareOnResponse:
    async def test_http_exception_wire_shape(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/missing")
        assert response_status(sent) == 404
        assert response_headers(sent)[b"content-type"] == b"text/plain; charset=utf-8"
        assert response_body(sent) == b"nothing here"

    async def test_plain_exception_maps_to_500(
        self, http_request, response_status, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/boom")
        assert response_status(sent) == 500
        assert response_body(sent) == b"Internal Server Error"

    async def test_redirect_sets_location_header(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/old")
        assert response_status(sent) == 302
        assert response_headers(sent)[b"location"] == b"/new"

    async def test_exception_headers_are_forwarded(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/challenge")
        assert response_status(sent) == 401
        assert response_headers(sent)[b"www-authenticate"] == b"Bearer"

    async def test_error_after_start_is_reraised_not_double_sent(self) -> None:
        server = MwServer(applications=[StartThenRaiseApp(mount="")])
        scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        with pytest.raises(RuntimeError, match="after start"):
            await server(scope, receive, send)

        starts = [m for m in sent if m["type"] == "http.response.start"]
        assert len(starts) == 1
        assert starts[0]["status"] == 200


class TestErrorContentNegotiation:
    """Macro 5a Phase 5: the error body follows the caller's ``Accept``."""

    async def test_json_accept_gets_error_document(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/missing", headers=[(b"accept", b"application/json")])
        assert response_status(sent) == 404
        assert response_headers(sent)[b"content-type"] == b"application/json"
        assert json.loads(response_body(sent)) == {"error": "nothing here"}

    async def test_wildcard_accept_gets_error_document(
        self, http_request, response_headers, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/missing", headers=[(b"accept", b"*/*")])
        assert response_headers(sent)[b"content-type"] == b"application/json"
        assert json.loads(response_body(sent)) == {"error": "nothing here"}

    async def test_html_accept_keeps_text_plain(
        self, http_request, response_headers, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/missing", headers=[(b"accept", b"text/html")])
        assert response_headers(sent)[b"content-type"] == b"text/plain; charset=utf-8"
        assert response_body(sent) == b"nothing here"

    async def test_no_accept_defaults_text_plain(
        self, http_request, response_headers, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/missing")
        assert response_headers(sent)[b"content-type"] == b"text/plain; charset=utf-8"
        assert response_body(sent) == b"nothing here"

    async def test_generic_500_json_hides_internal_message(
        self, http_request, response_status, response_body
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/boom", headers=[(b"accept", b"application/json")])
        assert response_status(sent) == 500
        assert json.loads(response_body(sent)) == {"error": "Internal Server Error"}


class TestUnauthorizedIsAnOrdinaryError:
    """D-SA-4: the core answers a bare 401, the same one to every caller.

    The negotiation that turned a browser's 401 into a 302 to
    ``/_server/login_page`` — and an API caller's into a ``{"login_url": ...}``
    body — was the only place where the core knew a login surface it does not
    own. It is gone: the challenge header the exception carries is forwarded,
    nothing else is added, and a server composed with the full ``AsgiServer``
    answers exactly like the bare middleware composition.
    """

    async def test_browser_navigation_keeps_the_bare_401(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        server = AsgiServer(applications=[(RaisingApp, {"mount": ""})])
        sent = await http_request(server, "/challenge", headers=[(b"accept", b"text/html")])
        assert response_status(sent) == 401
        assert response_headers(sent)[b"www-authenticate"] == b"Bearer"
        assert b"location" not in response_headers(sent)
        assert response_body(sent) == b"no"

    async def test_api_caller_gets_the_json_error_and_the_challenge_header(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        server = AsgiServer(applications=[(RaisingApp, {"mount": ""})])
        sent = await http_request(server, "/challenge", headers=[(b"accept", b"application/json")])
        assert response_status(sent) == 401
        assert response_headers(sent)[b"www-authenticate"] == b"Bearer"
        assert json.loads(response_body(sent)) == {"error": "no"}

    async def test_the_bare_composition_answers_the_same(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(applications=[RaisingApp(mount="")])
        sent = await http_request(server, "/challenge", headers=[(b"accept", b"text/html")])
        assert response_status(sent) == 401
        assert response_headers(sent)[b"www-authenticate"] == b"Bearer"


class TestLoggingMiddleware:
    async def test_records_one_entry_per_request(self, http_request, response_status) -> None:
        records: list[str] = []

        class RecordingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        server = MwServer(applications=[RoutedApp(mount="")], middleware={"logging": True})
        access_logger = logging.getLogger("kajenn.middleware.logging.LoggingMiddleware")
        handler = RecordingHandler()
        access_logger.addHandler(handler)
        access_logger.setLevel(logging.INFO)
        try:
            sent = await http_request(server, "/")
        finally:
            access_logger.removeHandler(handler)

        assert response_status(sent) == 200
        assert len(records) == 2
        assert records[0].startswith("<- GET /")
        assert records[1].startswith("-> GET / 200")


class CountingSessionStore(MemorySessionStore):
    """A memory store that counts ``save`` calls (write-back assertions)."""

    def __init__(self) -> None:
        super().__init__()
        self.saves = 0

    def save(self, session) -> None:
        self.saves += 1
        super().save(session)


class SessionMutatingApp(BaseApplication):
    """Mutates the scope session on ``/write``, reads it on any other path."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        session = scope.get("session")
        if session is not None and scope["path"] == "/write":
            session.data["hit"] = "yes"
            session.mark_dirty()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class TestSessionWriteBack:
    def _server(self) -> tuple[AsgiServer, CountingSessionStore]:
        server = AsgiServer(
            applications=[(SessionMutatingApp, {"mount": ""})],
            session_store=CountingSessionStore,
        )
        return server, server.session_store

    async def test_read_only_request_does_not_save(self, http_request, response_status) -> None:
        server, store = self._server()
        sent = await http_request(server, "/read")
        assert response_status(sent) == 200
        assert store.saves == 0  # read-only stays zero-I/O

    async def test_mutating_request_saves_once(self, http_request, response_status) -> None:
        server, store = self._server()
        sent = await http_request(server, "/write")
        assert response_status(sent) == 200
        assert store.saves == 1  # dirty → one write-back

    async def test_write_back_clears_the_dirty_flag(self, http_request) -> None:
        server, store = self._server()
        await http_request(server, "/write")
        # the single live session in the store is clean again after the save
        session = next(iter(store.dump()))
        assert store.get(session).dirty is False


class TestDisabledByDefault:
    async def test_standard_middlewares_absent_without_switches(
        self, http_request, response_headers
    ) -> None:
        # Hidden paths are not a middleware since #88: the rule is the
        # server's own, covered in ``tests/core/test_hidden_paths.py``.
        server = MwServer(applications=[RoutedApp(mount="")])

        cors_sent = await http_request(server, "/", headers=[(b"origin", b"https://example.test")])
        assert b"access-control-allow-origin" not in response_headers(cors_sent)

        records: list[str] = []

        class RecordingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        access_logger = logging.getLogger("kajenn.middleware.logging.LoggingMiddleware")
        handler = RecordingHandler()
        access_logger.addHandler(handler)
        try:
            await http_request(server, "/")
        finally:
            access_logger.removeHandler(handler)
        assert records == []
