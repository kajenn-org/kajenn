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

"""A chunked answer of an external application reaches the client chunk by chunk.

The spawned application answers with a ``StreamingResponse`` (an SSE stream is
one); the server writes each chunk to the client as it arrives, before the
application has finished. A client that leaves stops the application's
iterator; a process that dies mid-answer leaves the answer unfinished. The
client is the ASGI interface itself: ``send`` records what the client would
receive, ``receive`` says when the client leaves.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from kajenn import AsgiServer

from .test_external_application import RECIPE, answering, request, running

STREAMING = '''

from kajenn.sse import SseStream
from kajenn.streaming import StreamingResponse


class Streaming(Billing):
    @route()
    def ticks(self, gate):
        async def chunks():
            yield b"tick-0\\n"
            while not os.path.exists(gate):
                await asyncio.sleep(0.02)
            yield b"tick-1\\n"
            yield b"tick-2\\n"
        return StreamingResponse(chunks(), media_type="text/plain")

    @route()
    def endless(self, marker):
        async def chunks():
            try:
                yield f"{os.getpid()}\\n".encode()
                while True:
                    await asyncio.sleep(0.05)
                    yield b"x"
            finally:
                with open(marker, "w") as handle:
                    handle.write("closed")
        return StreamingResponse(chunks(), media_type="text/plain")

    @route()
    def big(self):
        async def chunks():
            for _ in range(10):
                yield b"y" * 32768
        return StreamingResponse(chunks(), media_type="application/octet-stream")

    @route()
    def events(self):
        async def source():
            for n in range(3):
                yield {"id": str(n), "data": {"n": n}}
        return SseStream(source()).response()
'''


def write_streaming_recipe(tmp_path, *, declaration: str = 'spawner="subprocess"',
                           kbus_options: str | None = None) -> str:
    section = ""
    if kbus_options is not None:
        section = f"\n    def server_section(self, cfg):\n        cfg.server().kbus({kbus_options})\n\n"
    text = (RECIPE.replace("\nclass Local(", STREAMING + "\nclass Local(")
            .replace("SERVER_SECTION", section)
            .replace("app_class=Billing, DECLARATION", f"app_class=Streaming, {declaration}")
            .replace("STARTUP_DELAY", "0.2"))
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


class Client:
    """One HTTP request driven through the server's ASGI interface."""

    def __init__(self, server: AsgiServer, path: str, query: bytes = b"") -> None:
        self.scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "root_path": "", "query_string": query, "headers": [],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
        }
        self.server = server
        self.sent: asyncio.Queue = asyncio.Queue()
        self.leaving = asyncio.Event()
        self.requested = False
        self.task: asyncio.Task | None = None

    async def receive(self) -> dict[str, Any]:
        if not self.requested:
            self.requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.leaving.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        await self.sent.put(message)

    def start(self) -> None:
        self.task = asyncio.create_task(self.server(self.scope, self.receive, self.send))

    async def next(self, seconds: float = 10) -> dict[str, Any]:
        return await asyncio.wait_for(self.sent.get(), seconds)

    async def body_until_end(self, seconds: float = 10) -> bytes:
        body = b""
        while True:
            message = await self.next(seconds)
            if message["type"] == "http.response.body":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    return body


async def wait_for_file(path: Path, seconds: float = 10) -> str:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        await asyncio.sleep(0.05)
    return path.read_text()


async def test_the_first_chunk_arrives_before_the_application_finishes(tmp_path):
    # wf:contract: a spawned route answering a StreamingResponse whose iterator yields one
    # wf:contract: chunk and then waits for a file: the client receives the start (status
    # wf:contract: 200) and that first chunk while the iterator is still waiting; once the
    # wf:contract: file exists, the remaining chunks and the end of the body follow.
    gate = tmp_path / "gate"
    async with running(write_streaming_recipe(tmp_path)) as server:
        client = Client(server, "/billing/ticks", f"gate={gate}".encode())
        client.start()
        start = await client.next()
        first = await client.next()
        assert (start["type"], start["status"]) == ("http.response.start", 200)
        assert first == {"type": "http.response.body", "body": b"tick-0\n", "more_body": True}
        assert not gate.exists()
        gate.write_text("open")
        rest = await client.body_until_end()
        await client.task
    assert rest == b"tick-1\ntick-2\n"


async def test_a_client_leaving_halfway_stops_the_application(tmp_path):
    # wf:contract: a client that disconnects while a spawned endless stream is running
    # wf:contract: makes the application's iterator end: its finally block runs in the
    # wf:contract: spawned process within seconds, and the server's request returns.
    marker = tmp_path / "marker"
    async with running(write_streaming_recipe(tmp_path)) as server:
        client = Client(server, "/billing/endless", f"marker={marker}".encode())
        client.start()
        await client.next()
        await client.next()
        client.leaving.set()
        assert await wait_for_file(marker) == "closed"
        await asyncio.wait_for(client.task, 10)


async def test_an_answer_larger_than_one_frame_streams_through(tmp_path):
    # wf:contract: with server.kbus(max_frame=65536), a spawned StreamingResponse of ten
    # wf:contract: 32 KiB chunks (320 KiB in all) reaches the client whole: each chunk
    # wf:contract: travels on its own, the frame limit binds a chunk and not the answer.
    async with running(write_streaming_recipe(tmp_path, kbus_options="max_frame=65536")) as server:
        client = Client(server, "/billing/big")
        client.start()
        body = await client.body_until_end()
        await client.task
    assert body == b"y" * 327680


async def test_an_sse_stream_reads_the_same_spawned_and_inprocess(tmp_path):
    # wf:contract: GET of a route answering SseStream(...).response() gives the same
    # wf:contract: content-type (text/event-stream) and the same body when the
    # wf:contract: application is spawned and when it is in-process.
    async def read(config: str) -> tuple[str, bytes]:
        async with running(config) as server:
            client = Client(server, "/billing/events")
            client.start()
            start = await client.next()
            body = await client.body_until_end()
            await client.task
        headers = dict(start["headers"])
        return headers[b"content-type"].decode(), body

    spawned = await read(write_streaming_recipe(tmp_path))
    (tmp_path / "inproc").mkdir()
    inprocess = await read(write_streaming_recipe(tmp_path / "inproc",
                                                  declaration='mount="billing"'))
    assert spawned == inprocess
    assert spawned[0].startswith("text/event-stream")


async def test_a_process_dying_mid_answer_leaves_it_unfinished(tmp_path):
    # wf:contract: when the spawned process is killed while its stream is running, the
    # wf:contract: client never receives the end of the body (no http.response.body with
    # wf:contract: more_body False), the server's request ends within seconds, and the
    # wf:contract: relaunched process answers the next request.
    marker = tmp_path / "marker"
    async with running(write_streaming_recipe(tmp_path)) as server:
        client = Client(server, "/billing/endless", f"marker={marker}".encode())
        client.start()
        await client.next()
        first = await client.next()
        pid = int(first["body"].decode())
        os.kill(pid, signal.SIGKILL)
        try:
            await asyncio.wait_for(client.task, 10)
        except Exception:
            pass
        received = []
        while not client.sent.empty():
            received.append(client.sent.get_nowait())
        assert all(m.get("more_body", True) for m in received
                   if m["type"] == "http.response.body")
        await answering(server, other_than=pid)
        assert await request(server, "/billing/total", query=b"order=3") == (200, {"total": 6})
