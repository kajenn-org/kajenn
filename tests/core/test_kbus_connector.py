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

"""KBusConnector contract: one live link, symmetric, correlated, never replayed.

The contract merges what ``WorkerConnector`` (kajenn-orchestra) and
``RemoteConnection`` already guarantee: both ends send CALLs and serve the
CALLs they receive, a REPLY is correlated by id and path, an uncertain call is
reported with its ``outcome`` and never replayed, a timed-out id stays reserved
until its late reply, a link loss fails every pending call. The two ends here
are a pair of ``LocalFrameStream`` over two queues; ``Raw`` drives one end by
hand to send what a well-behaved connector never would.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kajenn.kbus.connector import KBusCallCancelled, KBusCallFailed, KBusConnector
from kajenn.kbus.frame import CALL_METHOD, EVENT_METHOD, REGISTER_METHOD, REPLY_METHOD, Frame
from kajenn.kbus.local import LocalFrameStream


def queues() -> tuple[asyncio.Queue, asyncio.Queue]:
    return asyncio.Queue(), asyncio.Queue()


def stream_pair(
    *, a_max_size: int | None = None, b_max_size: int | None = None
) -> tuple[LocalFrameStream, LocalFrameStream, asyncio.Queue, asyncio.Queue]:
    to_a, to_b = queues()
    a = LocalFrameStream(to_a, to_b, max_size=a_max_size, max_queue_size=64)
    b = LocalFrameStream(to_b, to_a, max_size=b_max_size, max_queue_size=64)
    return a, b, to_a, to_b


def call(path: str = "/x", payload: bytes = b"", **kwargs: Any) -> Frame:
    return Frame(method=CALL_METHOD, path=path, payload=payload, **kwargs)


def reply_to(frame: Frame, payload: bytes = b"", **info: Any) -> Frame:
    return Frame(id=frame.id, method=REPLY_METHOD, path=frame.path, info=info, payload=payload)


class Server:
    """An ``on_call`` that echoes, and parks ``/slow`` until released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.events: list[Frame] = []

    async def on_call(self, frame: Frame) -> Frame:
        if frame.path == "/slow":
            self.started.set()
            await self.release.wait()
        if frame.path == "/fail":
            raise ValueError("nope")
        if frame.path == "/big":
            return reply_to(frame, b"x" * 4096)
        return reply_to(frame, frame.payload, echoed=True)

    def on_event(self, frame: Frame) -> None:
        self.events.append(frame)


class Pair:
    def __init__(self, *, a_kwargs: dict | None = None, b_max_size: int | None = None) -> None:
        sa, sb, self.to_a, self.to_b = stream_pair(b_max_size=b_max_size)
        self.lost: list[KBusConnector] = []
        self.server_a = Server()
        self.server_b = Server()
        self.a = KBusConnector(
            sa,
            name="a",
            on_call=self.server_a.on_call,
            on_event=self.server_a.on_event,
            on_lost=self.lost.append,
            **(a_kwargs or {}),
        )
        self.b = KBusConnector(
            sb,
            name="b",
            on_call=self.server_b.on_call,
            on_event=self.server_b.on_event,
            on_lost=self.lost.append,
        )

    def start(self) -> Pair:
        self.a.start()
        self.b.start()
        return self

    async def close(self) -> None:
        await self.a.close()
        await self.b.close()


class Raw:
    """The far end driven by hand: reads CALLs, writes whatever the test says."""

    def __init__(self, stream: LocalFrameStream, queue: asyncio.Queue) -> None:
        self.stream = stream
        self.queue = queue

    async def read(self) -> Frame:
        frame = await asyncio.wait_for(self.stream.read(), 2)
        assert frame is not None
        return frame


def raw_pair(**a_kwargs: Any) -> tuple[KBusConnector, Raw, list[KBusConnector]]:
    sa, sb, to_a, _ = stream_pair()
    lost: list[KBusConnector] = []
    connector = KBusConnector(sa, name="a", on_lost=lost.append, **a_kwargs)
    connector.start()
    return connector, Raw(sb, to_a), lost


async def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition not reached")
        await asyncio.sleep(0.01)


@pytest.fixture
async def pair():
    started = Pair().start()
    yield started
    await started.close()


# -- request/reply -------------------------------------------------------------


async def test_call_returns_the_whole_reply_frame(pair):
    reply = await pair.a.call(call(payload=b"hi"))
    assert reply.method == REPLY_METHOD
    assert reply.payload == b"hi"
    assert reply.info == {"echoed": True}


async def test_both_ends_serve_calls(pair):
    assert (await pair.a.call(call(payload=b"up"))).payload == b"up"
    assert (await pair.b.call(call(payload=b"down"))).payload == b"down"


async def test_a_slow_call_does_not_hold_the_replies_behind_it(pair):
    slow = asyncio.create_task(pair.a.call(call("/slow")))
    await asyncio.wait_for(pair.server_b.started.wait(), 2)
    fast = await asyncio.wait_for(pair.a.call(call(payload=b"fast")), 2)
    assert fast.payload == b"fast"
    assert not slow.done()
    pair.server_b.release.set()
    await asyncio.wait_for(slow, 2)


async def test_a_failing_handler_answers_an_error_reply(pair):
    reply = await pair.a.call(call("/fail"))
    assert "ValueError" in reply.info["error"]
    assert "nope" in reply.info["error"]


async def test_an_end_without_handler_answers_an_error_reply():
    sa, sb, _, _ = stream_pair()
    a = KBusConnector(sa, name="a")
    b = KBusConnector(sb, name="b")
    a.start()
    b.start()
    reply = await a.call(call())
    assert reply.info["error"]
    await a.close()
    await b.close()


async def test_a_reply_too_large_becomes_an_error_reply():
    big = Pair(b_max_size=1024).start()
    reply = await big.a.call(call("/big"))
    assert reply.info["error"]
    assert reply.payload == b""
    await big.close()


async def test_an_event_reaches_the_other_end(pair):
    await pair.a.post(Frame(method=EVENT_METHOD, path="/e", payload=b"x"))
    await wait_until(lambda: pair.server_b.events)
    assert pair.server_b.events[0].path == "/e"
    assert pair.server_b.events[0].payload == b"x"


# -- what a call refuses ---------------------------------------------------------


async def test_call_requires_a_call_frame(pair):
    with pytest.raises(ValueError):
        await pair.a.call(Frame(method=EVENT_METHOD, path="/x"))


async def test_a_duplicate_in_flight_id_is_refused(pair):
    first = asyncio.create_task(pair.a.call(call("/slow", id="same")))
    await asyncio.wait_for(pair.server_b.started.wait(), 2)
    with pytest.raises(ValueError):
        await pair.a.call(call("/x", id="same"))
    pair.server_b.release.set()
    await asyncio.wait_for(first, 2)


async def test_past_the_pending_limit_a_call_is_not_sent():
    limited = Pair(a_kwargs={"max_pending": 1}).start()
    first = asyncio.create_task(limited.a.call(call("/slow")))
    await asyncio.wait_for(limited.server_b.started.wait(), 2)
    with pytest.raises(KBusCallFailed) as failed:
        await limited.a.call(call())
    assert failed.value.outcome == "not_sent"
    limited.server_b.release.set()
    await asyncio.wait_for(first, 2)
    await limited.close()


async def test_a_call_on_a_closed_connector_is_not_sent(pair):
    await pair.a.close()
    with pytest.raises(KBusCallFailed) as failed:
        await pair.a.call(call())
    assert failed.value.outcome == "not_sent"


# -- uncertainty: timeout, cancellation, late replies ----------------------------


async def test_a_timeout_is_an_unknown_outcome_and_reserves_the_id(pair):
    with pytest.raises(KBusCallFailed) as failed:
        await pair.a.call(call("/slow", id="late"), timeout=0.05)
    assert failed.value.outcome == "unknown"
    with pytest.raises(ValueError):
        await pair.a.call(call("/x", id="late"))
    pair.server_b.release.set()
    await asyncio.sleep(0.1)
    reply = await pair.a.call(call("/x", id="late", payload=b"again"))
    assert reply.payload == b"again"


async def test_a_cancelled_call_reports_its_outcome(pair):
    task = asyncio.create_task(pair.a.call(call("/slow")))
    await asyncio.wait_for(pair.server_b.started.wait(), 2)
    task.cancel()
    with pytest.raises(KBusCallCancelled) as cancelled:
        await task
    assert cancelled.value.outcome == "unknown"
    assert isinstance(cancelled.value, asyncio.CancelledError)
    pair.server_b.release.set()


async def test_past_the_abandoned_limit_the_link_is_closed():
    limited = Pair(a_kwargs={"max_abandoned": 1}).start()
    for _ in range(2):
        with pytest.raises(KBusCallFailed):
            await limited.a.call(call("/slow"), timeout=0.05)
    await wait_until(lambda: limited.a.closed)
    limited.server_b.release.set()
    await limited.close()


# -- end of the link -------------------------------------------------------------


async def test_link_loss_fails_pending_calls_and_reports_the_loss(pair):
    pending = asyncio.create_task(pair.a.call(call("/slow")))
    await asyncio.wait_for(pair.server_b.started.wait(), 2)
    await pair.b.stream.close()
    with pytest.raises(KBusCallFailed) as failed:
        await asyncio.wait_for(pending, 2)
    assert failed.value.outcome == "unknown"
    await wait_until(lambda: pair.a in pair.lost)
    pair.server_b.release.set()


async def test_a_deliberate_close_is_not_a_loss_on_its_own_side(pair):
    await pair.a.close()
    await wait_until(lambda: pair.b in pair.lost)
    assert pair.a not in pair.lost
    await asyncio.wait_for(pair.a.wait_closed(), 2)


# -- protocol violations ---------------------------------------------------------


async def test_a_reply_on_the_wrong_path_is_a_violation():
    connector, raw, lost = raw_pair()
    pending = asyncio.create_task(connector.call(call("/x")))
    received = await raw.read()
    await raw.stream.write(
        Frame(id=received.id, method=REPLY_METHOD, path="/other", payload=b"")
    )
    with pytest.raises(KBusCallFailed):
        await asyncio.wait_for(pending, 2)
    await wait_until(lambda: connector in lost)


async def test_a_reply_nobody_waits_for_is_dropped():
    connector, raw, lost = raw_pair()
    await raw.stream.write(Frame(id="ghost", method=REPLY_METHOD, path="/x"))
    pending = asyncio.create_task(connector.call(call("/x")))
    received = await raw.read()
    await raw.stream.write(reply_to(received, b"ok"))
    assert (await asyncio.wait_for(pending, 2)).payload == b"ok"
    assert lost == []
    await connector.close()


async def test_a_register_after_the_start_is_a_violation():
    connector, raw, lost = raw_pair()
    await raw.stream.write(Frame(method=REGISTER_METHOD, path="/register"))
    await wait_until(lambda: connector in lost)


async def test_bytes_the_codec_rejects_are_a_violation():
    connector, raw, lost = raw_pair()
    raw.queue.put_nowait(b"not a frame")
    await wait_until(lambda: connector in lost)
