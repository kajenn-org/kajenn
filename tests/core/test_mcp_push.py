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

"""Tests for the MCP push channel (core 1e Phase 6): GET -> SSE over the hub.

Real objects, no mocks: a real ``AsgiServer`` (storage on tmp_path) with an
``McpApplication`` mounted, driven at the ASGI level. The ``sse_request``
fixture (conftest) keeps the GET open like a real SSE client; frames are
awaited, then the connection is cancelled (the client-gone path, exercising
the hub unsubscribe). The A<->C bridge is tested end-to-end: a spool task
carrying the launching ``session_id`` surfaces as ``data:`` frames while it
runs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from genro_routes import route

from tests.storage_support import site_mounts

from kajenn import AsgiServer, McpApplication, RoutedApplication
from kajenn.lifespan import STOPPING
from kajenn.tasks import new_descriptor
from kajenn.types import Message, Scope


class Primary(RoutedApplication):
    """The primary app: one handler a spool task can run."""

    @route()
    def sum_sync(self, a: int = 0, b: int = 0) -> int:
        return a + b


@pytest.fixture
def server(tmp_path: Path) -> AsgiServer:
    """A real server: Primary + McpApplication at ``/mcp``, storage on tmp_path."""
    srv = AsgiServer(
        applications=[(Primary, {"mount": ""}), (McpApplication, {"code": "mcp"})],
        storage=site_mounts(tmp_path),
    )
    return srv


async def drive(server: Any, path: str, *, method: str = "GET",
                headers: list[tuple[bytes, bytes]] | None = None,
                body: bytes = b"") -> list[Message]:
    """One plain (non-streaming) request through the server."""
    scope: Scope = {"type": "http", "method": method, "path": path,
                    "query_string": b"", "headers": list(headers or [])}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def start_headers(sent: list[Message]) -> dict[bytes, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"])


def payload(frame: bytes) -> dict[str, Any]:
    """Decode the JSON payload of one ``data:`` SSE frame."""
    text = frame.decode()
    data_lines = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    return json.loads("\n".join(data_lines))


def stage(server: AsgiServer, task_id: str, session_id: str | None) -> None:
    """Create a pending task on the primary, launched by ``session_id``."""
    descriptor = new_descriptor(task_id, owner="alice", mount="", node_path="sum_sync",
                                session_id=session_id)
    server.tasks.spool.create(descriptor, {"a": 2, "b": 3})


class TestSessionId:
    """GET mints or echoes the Mcp-Session-Id and streams text/event-stream."""

    async def test_get_mints_session_id(self, server: AsgiServer, sse_request) -> None:
        conn = await sse_request(server, "/mcp")
        try:
            headers = start_headers(conn.sent)
            assert headers[b"content-type"].startswith(b"text/event-stream")
            assert len(headers[b"mcp-session-id"]) > 20      # minted token
        finally:
            await conn.close()

    async def test_get_echoes_supplied_session_id(self, server: AsgiServer, sse_request) -> None:
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-1")])
        try:
            assert start_headers(conn.sent)[b"mcp-session-id"] == b"sess-1"
        finally:
            await conn.close()

    async def test_get_without_tasks_is_405(self, tmp_path: Path) -> None:
        srv = AsgiServer(
            applications=[(Primary, {"mount": ""}), (McpApplication, {"code": "mcp"})],
            tasks=False,
            storage=site_mounts(tmp_path),
        )
        sent = await drive(srv, "/mcp", method="GET")
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 405


class TestLiveFeed:
    """hub.publish surfaces as a ``data:`` frame on the subscribed stream."""

    async def test_publish_becomes_frame(self, server: AsgiServer, sse_request) -> None:
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-2")])
        try:
            server.tasks.hub.publish("sess-2", {"type": "progress", "task_id": "t1",
                                                "data": {"pct": 10}})
            frames = await conn.wait_frames(1)
            assert payload(frames[0]) == {"type": "progress", "task_id": "t1",
                                          "data": {"pct": 10}}
        finally:
            await conn.close()

    async def test_other_session_not_delivered(self, server: AsgiServer, sse_request) -> None:
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-3")])
        try:
            server.tasks.hub.publish("other", {"type": "progress", "task_id": "x"})
            server.tasks.hub.publish("sess-3", {"type": "marker"})
            frames = await conn.wait_frames(1)
            assert payload(frames[0]) == {"type": "marker"}   # only own session
        finally:
            await conn.close()

    async def test_close_unsubscribes(self, server: AsgiServer, sse_request) -> None:
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-4")])
        await conn.close()
        assert "sess-4" not in server.tasks.hub._subscribers   # finally ran


class TestExecutorBridge:
    """A task carrying session_id publishes started/settled while it runs."""

    async def test_lifecycle_events_stream(self, server: AsgiServer, sse_request) -> None:
        stage(server, "t-bridge", "sess-5")
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-5")])
        try:
            server.tasks.spool.assign("t-bridge", "local")
            outcome = await server.tasks.executor.execute("t-bridge", "local")
            assert outcome == "ok"
            frames = await conn.wait_frames(2)
            events = [payload(f) for f in frames]
            assert events[0] == {"task_id": "t-bridge", "type": "started"}
            assert events[1]["type"] == "settled" and events[1]["outcome"] == "ok"
        finally:
            await conn.close()

    async def test_no_session_no_publish(self, server: AsgiServer) -> None:
        stage(server, "t-silent", None)                       # no push channel
        server.tasks.spool.assign("t-silent", "local")
        outcome = await server.tasks.executor.execute("t-silent", "local")
        assert outcome == "ok"                                # publish was a no-op
        assert server.tasks.hub._subscribers == {}


class TestPublishProgressSeam:
    """manager.publish_progress pairs the spool write with the hub publish."""

    async def test_paired_write_and_publish(self, server: AsgiServer, sse_request) -> None:
        stage(server, "t-prog", "sess-6")
        server.tasks.spool.assign("t-prog", "local")
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-6")])
        try:
            server.tasks.publish_progress("t-prog", {"pct": 40})
            assert server.tasks.spool.read_progress("t-prog") == {"pct": 40}   # spool wrote
            frames = await conn.wait_frames(1)                                 # hub carried
            assert payload(frames[0]) == {"type": "progress", "task_id": "t-prog",
                                          "data": {"pct": 40}}
        finally:
            await conn.close()

    def test_not_active_raises(self, server: AsgiServer) -> None:
        stage(server, "t-pending", "sess-7")                  # pending, not active
        with pytest.raises(LookupError, match="not active"):
            server.tasks.publish_progress("t-pending", {"pct": 1})
        with pytest.raises(LookupError, match="not active"):
            server.tasks.publish_progress("t-ghost", {"pct": 1})


class TestBaseline:
    """Last-Event-ID replays the session's progress snapshots, then live."""

    async def test_reconnect_replays_snapshot(self, server: AsgiServer, sse_request) -> None:
        stage(server, "t-base", "sess-8")
        server.tasks.spool.assign("t-base", "local")
        server.tasks.publish_progress("t-base", {"pct": 70})   # no subscriber yet
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-8"),
                                                          (b"last-event-id", b"0")])
        try:
            frames = await conn.wait_frames(1)
            assert payload(frames[0]) == {"type": "progress", "task_id": "t-base",
                                          "data": {"pct": 70}}
        finally:
            await conn.close()

    async def test_fresh_connect_has_no_baseline(self, server: AsgiServer, sse_request) -> None:
        stage(server, "t-fresh", "sess-9")
        server.tasks.spool.assign("t-fresh", "local")
        server.tasks.publish_progress("t-fresh", {"pct": 70})
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-9")])
        try:
            server.tasks.hub.publish("sess-9", {"type": "marker"})
            frames = await conn.wait_frames(1)
            assert payload(frames[0]) == {"type": "marker"}    # live only, no replay
        finally:
            await conn.close()


class TestInitializeAdvertisesPush:
    """POST initialize advertises the push capability; POST path unchanged."""

    async def test_capability_advertised(self, server: AsgiServer) -> None:
        envelope = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        sent = await drive(server, "/mcp", method="POST",
                           headers=[(b"content-type", b"application/json")],
                           body=json.dumps(envelope).encode())
        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        result = json.loads(body)["result"]
        assert result["capabilities"]["experimental"] == {"push": {}}
        assert result["capabilities"]["tools"] == {}           # unchanged

    async def test_notification_still_202(self, server: AsgiServer) -> None:
        envelope = {"jsonrpc": "2.0", "method": "initialize"}
        sent = await drive(server, "/mcp", method="POST",
                           headers=[(b"content-type", b"application/json")],
                           body=json.dumps(envelope).encode())
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 202


class TestServerLeaving:
    """A stream over a server that starts leaving ends by itself, unsubscribed."""

    async def test_the_stream_ends_when_the_server_leaves(
        self, server: AsgiServer, sse_request
    ) -> None:
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-leaving")])

        server.state = STOPPING

        await asyncio.wait_for(conn.task, timeout=2.0)

    async def test_the_stream_that_ends_by_itself_still_unsubscribes(
        self, server: AsgiServer, sse_request
    ) -> None:
        conn = await sse_request(server, "/mcp", headers=[(b"mcp-session-id", b"sess-leaving-2")])

        server.state = STOPPING
        await asyncio.wait_for(conn.task, timeout=2.0)

        assert "sess-leaving-2" not in server.tasks.hub._subscribers
