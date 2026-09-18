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

"""Subprocess integration proof for the generic remote application mount."""

import asyncio
import json
import os
import socket
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from examples.remote_openapi.frontend import LocalApplication
from kajenn import Avatar, BaseServer
from kajenn.remote_application import RemoteApplication
from genro_tytx import from_tytx, to_tytx

FACTORY = "examples.remote_openapi.app:create_application"


def unix_address() -> str:
    """Use a deliberately short macOS-safe Unix socket path."""
    return f"uds:/tmp/ga-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"


def tcp_address() -> str:
    """Reserve a loopback port briefly, then hand it to the runner."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp:127.0.0.1:{probe.getsockname()[1]}"


@asynccontextmanager
async def owned_remote(address: str) -> AsyncIterator[RemoteApplication]:
    app = RemoteApplication(
        address=address,
        factory=FACTORY,
        code="demo",
        mount="demo",
        startup_timeout=4,
        shutdown_timeout=1,
        request_timeout=2,
        max_calls=8,
    )
    BaseServer(applications=[app])
    await app.on_startup()
    try:
        yield app
    finally:
        await app.on_shutdown()


@asynccontextmanager
async def connected_remote(address: str) -> AsyncIterator[tuple[RemoteApplication, Any]]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "kajenn.remote_runner",
        "--factory",
        FACTORY,
        "--address",
        address,
        "--mount",
        "demo",
    )
    app = RemoteApplication(
        address=address,
        code="demo",
        mount="demo",
        startup_timeout=4,
        shutdown_timeout=1,
        request_timeout=2,
        max_calls=8,
    )
    BaseServer(applications=[app])
    try:
        await app.on_startup()
        yield app, process
    finally:
        await app.on_shutdown()
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 2)


async def request(
    application: Any,
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    avatar: Avatar | None = None,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "root_path": "",
        "query_string": query,
        "headers": list(headers or []),
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
    }
    if avatar is not None:
        scope["auth"] = avatar
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await application(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    answer = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], start["headers"], answer


@pytest.mark.parametrize("address_factory", [unix_address, tcp_address])
async def test_owned_runner_serves_openapi_in_a_different_process(address_factory):
    async with owned_remote(address_factory()) as app:
        status, _, body = await request(app, "/hello")
        hello = json.loads(body)
        assert status == 200
        assert hello == {"hello": "remote", "pid": app._process.pid}
        assert hello["pid"] != os.getpid()

        status, _, body = await request(app, "/_meta/schema_json")
        schema = json.loads(body)
        assert status == 200
        assert schema["info"]["title"] == "Remote OpenAPI demo"
        assert schema["servers"] == [{"url": "/demo"}]
        assert {"/hello", "/inspect", "/delay"} <= set(schema["paths"])

        status, _, docs = await request(app, "/_meta/docs")
        assert status == 200
        assert b"/demo/_meta/schema_json" in docs


async def test_connect_only_tcp_runner_is_not_owned_by_frontend():
    address = tcp_address()
    async with connected_remote(address) as (app, process):
        status, _, _ = await request(app, "/hello")
        assert status == 200
        await app.on_shutdown()
        assert process.returncode is None
        replacement = RemoteApplication(address=address, code="replacement", mount="demo")
        BaseServer(applications=[replacement])
        await replacement.on_startup()
        try:
            status, _, _ = await request(replacement, "/hello")
            assert status == 200
        finally:
            await replacement.on_shutdown()


async def test_binary_echo_and_ordered_duplicate_inputs_survive_uds():
    async with owned_remote(unix_address()) as app:
        binary = b"\x00\xff\x80raw\x00"
        status, headers, body = await request(app, "/echo", method="POST", body=binary)
        assert status == 200
        assert body == binary
        assert dict(headers)[b"content-type"] == b"application/octet-stream"

        duplicate_headers = [
            (b"x-repeat", b"one"),
            (b"x-repeat", b"two"),
            (b"cookie", b"first=1"),
            (b"cookie", b"second=2"),
        ]
        status, _, body = await request(
            app,
            "/inspect",
            query=b"item=one&item=two&blank=",
            headers=duplicate_headers,
        )
        inspected = json.loads(body)
        assert status == 200
        assert inspected["headers"] == [[n.decode(), v.decode()] for n, v in duplicate_headers]
        assert inspected["query_string"] == "item=one&item=two&blank="
        assert inspected["query"] == {"item": ["one", "two"], "blank": ""}
        assert inspected["cookies"] == {"second": 2}


async def test_trusted_avatar_reaches_protected_route_without_avatar_data():
    avatar = Avatar("alice", ["admin", "operator"])
    avatar.data["private"] = "must not cross"
    async with owned_remote(unix_address()) as app:
        status, _, body = await request(app, "/protected", avatar=avatar)
        assert status == 200
        answer = json.loads(body)
        assert answer["identity"] == "alice"
        assert answer["tags"] == ["admin", "operator"]
        assert "private" not in answer


async def test_protected_route_preserves_http_auth_outcomes():
    async with owned_remote(unix_address()) as app:
        status, _, body = await request(app, "/protected")
        assert status == 401
        assert body == b"route:protected"

        status, _, body = await request(app, "/protected", avatar=Avatar("bob", ["viewer"]))
        assert status == 403
        assert body == b"route:protected"

        status, _, body = await request(app, "/protected", avatar=Avatar("alice", ["admin"]))
        assert status == 200
        assert json.loads(body)["identity"] == "alice"


async def test_wsk_request_body_reaches_remote_application_unchanged():
    serialized = to_tytx({"raw": "browser payload", "number": 7}, "json").encode()
    async with owned_remote(unix_address()) as app:
        status, headers, body = await request(app, "/echo", method="WSK", body=serialized)
        assert status == 200
        assert dict(headers)[b"content-type"] == b"application/json"
        # The remote echo received the original serialized bytes. WSK response
        # adaptation then represents those opaque text bytes as a JSON value.
        assert from_tytx(body.decode(), "json") == serialized.decode()


async def test_remote_mount_declines_raw_websocket_ownership_but_default_wsx_works():
    async with owned_remote(unix_address()) as app:
        assert not hasattr(app, "serve_websocket")
        incoming = [
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1000},
        ]
        outgoing = []

        async def receive():
            message = incoming.pop(0)
            if message["type"] == "websocket.disconnect":
                for _ in range(10):
                    await asyncio.sleep(0)
            return message

        async def send(message):
            outgoing.append(message)

        await app.server.on_websocket(
            {
                "type": "websocket",
                "path": "/demo/",
                "headers": [(b"host", b"example.test")],
                "query_string": b"",
                "subprotocols": [],
            },
            receive,
            send,
        )
        assert outgoing[0]["type"] == "websocket.accept"


async def test_remote_calls_run_concurrently_and_http_failure_is_contained():
    async with owned_remote(unix_address()) as app:
        started = time.monotonic()
        answers = await asyncio.gather(
            *(request(app, "/delay", query=b"seconds=0.12") for _ in range(4))
        )
        elapsed = time.monotonic() - started
        assert all(status == 200 for status, _, _ in answers)
        assert elapsed < 0.4

        status, _, body = await request(app, "/fail")
        assert status == 400
        assert body == b"intentional remote error"


async def test_peer_down_returns_503_while_unrelated_local_mount_answers():
    remote = RemoteApplication(
        address=unix_address(),
        code="demo",
        mount="demo",
        request_timeout=0.1,
    )
    server = BaseServer(
        applications=[LocalApplication(code="local", mount=""), remote]
    )
    status, _, _ = await request(server, "/demo/hello")
    assert status == 503
    status, _, body = await request(server, "/health")
    assert status == 200
    assert json.loads(body) == {"local": True}


async def test_frontend_generated_wsk_errors_are_adapted_at_the_local_endpoint(monkeypatch):
    monkeypatch.setenv("KAJENN_HTTP_MAX_BODY_BYTES", str(8 * 1024 * 1024))
    unavailable = RemoteApplication(
        address=unix_address(), code="down", mount="down", request_timeout=0.05
    )
    BaseServer(applications=[unavailable])
    status, headers, body = await request(unavailable, "/rpc", method="WSK")
    assert status == 503
    assert dict(headers)[b"content-type"] == b"application/json"
    assert from_tytx(body.decode(), "json") == "Remote application unavailable"

    async with owned_remote(unix_address()) as app:
        oversized = b"x" * (8 * 1024 * 1024 + 1)
        status, headers, body = await request(app, "/echo", method="WSK", body=oversized)
        assert status == 413
        assert dict(headers)[b"content-type"] == b"application/json"
        assert from_tytx(body.decode(), "json") == "Request too large"


async def test_owned_application_can_restart_after_connection_close():
    app = RemoteApplication(
        address=unix_address(),
        factory=FACTORY,
        code="demo",
        mount="demo",
        startup_timeout=4,
        shutdown_timeout=1,
        request_timeout=2,
        max_calls=2,
    )
    BaseServer(applications=[app])
    await app.on_startup()
    first_pid = json.loads((await request(app, "/hello"))[2])["pid"]
    await app.on_startup()
    assert json.loads((await request(app, "/hello"))[2])["pid"] == first_pid
    await app.on_shutdown()
    await app.on_startup()
    try:
        second_pid = json.loads((await request(app, "/hello"))[2])["pid"]
        assert second_pid != first_pid
    finally:
        await app.on_shutdown()


async def test_startup_failure_and_owned_shutdown_are_bounded():
    missing = RemoteApplication(
        address=unix_address(),
        code="missing",
        mount="missing",
        startup_timeout=0.12,
        shutdown_timeout=0.1,
        request_timeout=0.03,
    )
    BaseServer(applications=[missing])
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await missing.on_startup()
    assert time.monotonic() - started < 0.5

    async with owned_remote(unix_address()) as app:
        process = app._process
    assert process.returncode is not None


async def test_sigterm_shutdown_bounds_an_active_delayed_request():
    app = RemoteApplication(
        address=unix_address(),
        factory=FACTORY,
        code="demo",
        mount="demo",
        startup_timeout=4,
        shutdown_timeout=0.1,
        request_timeout=3,
    )
    BaseServer(applications=[app])
    await app.on_startup()
    process = app._process
    delayed = asyncio.create_task(request(app, "/delay", query=b"seconds=2"))
    await asyncio.sleep(0.1)
    started = time.monotonic()
    await app.on_shutdown()
    assert time.monotonic() - started < 1.5
    assert process.returncode is not None
    status, _, _ = await asyncio.wait_for(delayed, 1)
    assert status == 503


async def test_tcp_body_above_old_frame_limit_and_configured_refusal(monkeypatch):
    # Both processes inherit policy, including a lower-than-default test ceiling.
    monkeypatch.setenv("KAJENN_FRAME_MAX_BYTES", str(20 * 1024 * 1024))
    async with owned_remote(tcp_address()) as app:
        pid = app._process.pid
        body = b"\x00\xff" * (9 * 1024 * 1024)
        status, _, echoed = await request(app, "/echo", method="POST", body=body)
        assert status == 200 and echoed == body
        stream = app.connection._stream
        status, _, _ = await request(app, "/echo", method="POST", body=b"x" * (21 * 1024 * 1024))
        assert status == 413
        status, _, echoed = await request(app, "/echo", method="POST", body=b"after")
        assert status == 200 and echoed == b"after"
        assert app.connection._stream is stream and app._process.pid == pid
