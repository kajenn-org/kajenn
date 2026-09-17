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

"""Demux, uvicorn boot and empty-websocket tests (SPECIFICATION.md §4, D3/D7).

The http tests boot the server on port 0 in a background thread, discover the
bound port from the uvicorn server state, and hit it with real httpx requests.
The websocket test drives ``BaseServer.__call__`` directly at the ASGI level:
the empty socket exists and closes cleanly without needing a websocket client.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from kajenn import BaseServer

from ..throwaway_app import ThrowawayApp


@contextmanager
def running_server(server: BaseServer) -> Iterator[int]:
    """Boot ``server`` on port 0 in a background thread; yield the bound port."""
    thread = threading.Thread(target=lambda: server.serve(host="127.0.0.1", port=0), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while server.uvicorn_server is None or not server.uvicorn_server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start in time")
        time.sleep(0.01)
    port = server.uvicorn_server.servers[0].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.uvicorn_server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def booted() -> Iterator[int]:
    """A server with a root app and a secondary mounted as ``api`` (both throwaway)."""
    server = BaseServer(
        applications=[
            ThrowawayApp(mount="", name="root"),
            ThrowawayApp(name="api", code="api"),
        ]
    )
    with running_server(server) as port:
        yield port


class TestDemux:
    def test_root_goes_to_the_root_app(self) -> None:
        with booted() as port:
            r = httpx.get(f"http://127.0.0.1:{port}/")
            assert r.status_code == 200
            assert r.text == "root:/"

    def test_unclaimed_first_segment_goes_to_the_root_app(self) -> None:
        with booted() as port:
            r = httpx.get(f"http://127.0.0.1:{port}/nothing/claimed")
            assert r.status_code == 200
            assert r.text == "root:/nothing/claimed"

    def test_mounted_first_segment_reaches_its_app_with_path_stripped(self) -> None:
        with booted() as port:
            r = httpx.get(f"http://127.0.0.1:{port}/api/echo")
            assert r.status_code == 200
            assert r.text == "api:/echo"

    def test_raising_route_returns_500_and_server_survives(self) -> None:
        with booted() as port:
            boom = httpx.get(f"http://127.0.0.1:{port}/boom")
            assert boom.status_code == 500
            healthy = httpx.get(f"http://127.0.0.1:{port}/")
            assert healthy.status_code == 200
            assert healthy.text == "root:/"

    async def test_double_slash_before_a_mount_forwards_the_remainder(self) -> None:
        # the forwarded path is rebuilt from the same remainder used to find
        # the segment: //api/x reaches the mount as /x (driven at ASGI level)
        server = BaseServer(
            applications=[
                ThrowawayApp(mount="", name="root"),
                ThrowawayApp(name="api", code="api"),
            ]
        )
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await server({"type": "http", "path": "//api/x"}, receive, send)

        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        assert body == b"api:/x"


class TestServerOfMountsOnly:
    """The three branches a server without a root application answers with."""

    async def drive(self, server: BaseServer, path: str, query: bytes = b"") -> dict[str, object]:
        """Drive one GET at the ASGI level; return the ``http.response.start``."""
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {"type": "http", "path": path, "query_string": query}
        await server(scope, receive, send)
        return next(m for m in sent if m["type"] == "http.response.start")

    def mounts_only(self, **kwargs: object) -> BaseServer:
        """A server serving ``/api`` and nothing on the root."""
        return BaseServer(applications=[ThrowawayApp(name="api", code="api")], **kwargs)

    async def test_a_claimed_segment_still_reaches_its_app(self) -> None:
        start = await self.drive(self.mounts_only(), "/api/echo")
        assert start["status"] == 200

    async def test_an_unclaimed_path_is_404(self) -> None:
        start = await self.drive(self.mounts_only(), "/nothing/claimed")
        assert start["status"] == 404

    async def test_the_root_is_404_without_a_default(self) -> None:
        start = await self.drive(self.mounts_only(), "/")
        assert start["status"] == 404

    async def test_the_root_redirects_to_the_default_with_a_307(self) -> None:
        start = await self.drive(self.mounts_only(default="api"), "/")
        assert start["status"] == 307
        assert dict(start["headers"])[b"location"] == b"/api/"

    async def test_the_redirect_carries_the_query_string_over(self) -> None:
        start = await self.drive(self.mounts_only(default="api"), "/", query=b"q=moka&n=2")
        assert dict(start["headers"])[b"location"] == b"/api/?q=moka&n=2"

    async def test_an_unclaimed_path_is_404_even_with_a_default(self) -> None:
        # the default answers the ROOT only: it is not a catch-all
        start = await self.drive(self.mounts_only(default="api"), "/nothing")
        assert start["status"] == 404


class TestTheWebsocketBranch:
    """The websocket scope reaches the motor (#68 phase 2).

    Until phase 2 this branch was the empty socket of D7: it consumed the
    connect and closed 1000. It now lives one whole connection; what that
    connection does is `tests/test_wsx_connection.py`'s subject, and what is
    asserted here is only that `__call__` hands it over.
    """

    async def test_a_handshake_on_a_served_path_is_accepted(self) -> None:
        server = BaseServer(applications=[ThrowawayApp(mount="")])
        sent: list[dict[str, object]] = []
        incoming: list[dict[str, object]] = [
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        async def receive() -> dict[str, object]:
            return incoming.pop(0) if incoming else {"type": "websocket.disconnect", "code": 1006}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await server({"type": "websocket", "path": "/", "headers": []}, receive, send)
        assert sent == [{"type": "websocket.accept"}]
