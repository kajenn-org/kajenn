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

"""KajennBus error branches, one group per module.

The address refusals and the bind failures of a uds listener; the cancellation
of the task that cancels; the bare member end; the connector's misuse, a write
that fails, a reply that lands while its call times out, the event ceiling and
the late reply on the wrong path; the hub refusing, stopping or losing a member
in the middle of its REGISTER; the spawner interface and the subprocess
spawner's relaunch, backoff and kill; the mixin's failures in the lifespan, in
a role process and in the frames it serves; the remote mount answering 502 and
413.

Failures that real objects cannot produce on demand are injected through
``ScriptedStream``, a ``FrameStreamProtocol`` the test drives by hand, or
through a ``LocalKBus`` whose hub-side stream holds a write that already
landed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.kbus import (
    CALL_METHOD,
    EVENT_METHOD,
    REPLY_METHOD,
    Frame,
    KBusCallError,
    KBusCallFailed,
    KBusConnector,
    KBusHub,
    KBusMember,
    LocalKBus,
)
from kajenn.kbus.address import KBusAddress
from kajenn.kbus.callback import cancel_and_wait
from kajenn.kbus.client import KBusEnd
from kajenn.lifespan import FatalBootError
from kajenn.remote_application import RemoteApplication

from .test_kbus_final_touch import Failing


async def wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition never reached"
        await asyncio.sleep(0.01)


@pytest.fixture
def socket_dir():
    path = tempfile.mkdtemp(prefix="gnrbranch_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


class ScriptedStream:
    """A ``FrameStreamProtocol`` the test drives: frames to read are queued by hand.

    Every write is recorded; with ``write_error`` it raises that error instead,
    and once closed it raises ``BrokenPipeError``.
    """

    def __init__(self, *, write_error: BaseException | None = None) -> None:
        self.inbound: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.written: list[Frame] = []
        self.write_error = write_error
        self.closed = False

    async def read(self) -> Frame | None:
        return await self.inbound.get()

    async def write(self, frame: Frame) -> None:
        if self.closed:
            raise BrokenPipeError("scripted stream closed")
        if self.write_error is not None:
            raise self.write_error
        self.written.append(frame)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.inbound.put_nowait(None)


class AnsweringSlowStream(ScriptedStream):
    """Each CALL written is answered at once, but the write returns only later."""

    async def write(self, frame: Frame) -> None:
        await super().write(frame)
        self.inbound.put_nowait(
            Frame(id=frame.id, method=REPLY_METHOD, path=frame.path, payload=b"late"))
        await asyncio.sleep(0.5)


class LandedWriteStream:
    """A hub-side stream whose write lands, then returns only when ``release`` is set."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.landed = asyncio.Event()
        self.release = asyncio.Event()

    async def read(self) -> Frame | None:
        return await self.inner.read()

    async def write(self, frame: Frame) -> None:
        await self.inner.write(frame)
        self.landed.set()
        await self.release.wait()

    async def close(self) -> None:
        await self.inner.close()


class SlowWelcomeKBus(LocalKBus):
    """A ``LocalKBus`` whose hub side hands the hub a ``LandedWriteStream``."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.landed_stream = LandedWriteStream(super().hub_stream)

    @property
    def hub_stream(self) -> Any:
        return self.landed_stream


class Gate:
    """An async ``on_member_joined`` that waits for ``open`` before welcoming."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.open = asyncio.Event()

    async def __call__(self, member: KBusMember) -> dict[str, Any]:
        self.entered.set()
        await self.open.wait()
        return {"hello": member.name}


def call_frame(path: str = "/x") -> Frame:
    return Frame(method=CALL_METHOD, path=path)


def http_scope(path: str, method: str = "GET") -> dict[str, Any]:
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
            "root_path": "", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}


class Echo(RoutedApplication):
    @route()
    def ping(self, x):
        return {"pong": x}


class FatalStartup(RoutedApplication):
    def on_startup(self):
        raise FatalBootError("cannot prepare")


# -- kbus/address.py ---------------------------------------------------------------------


@pytest.mark.parametrize("address", ["tcp:127.0.0.1:70000", "tcp:10.0.0.1:5"])
def test_a_port_out_of_range_or_a_network_ip_without_opt_in_is_refused(address):
    with pytest.raises(ValueError, match="loopback IP and valid port"):
        KBusAddress(address)


async def test_a_listener_address_refuses_to_open_a_network_client():
    with pytest.raises(ValueError, match="loopback destination"):
        await KBusAddress("tcp:10.0.0.1:5", allow_network_listener=True).connect()


async def test_an_address_that_owns_a_socket_refuses_a_second_listen(socket_dir):
    address = KBusAddress(f"uds:{socket_dir}/a.sock")
    server = await address.listen(lambda reader, writer: None)
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            await address.listen(lambda reader, writer: None)
    finally:
        server.close()
        await server.wait_closed()
        address.unlink_owned_socket()
    assert not os.path.exists(f"{socket_dir}/a.sock")


async def test_a_bind_failure_closes_the_socket_and_propagates(socket_dir):
    address = KBusAddress(f"uds:{socket_dir}/missing/a.sock")
    with pytest.raises(OSError):
        await address.listen(lambda reader, writer: None)
    address.unlink_owned_socket()


async def test_a_pathname_replaced_after_the_bind_is_refused(socket_dir, monkeypatch):
    # The replacement races the bind: the stat after the bind is forced to
    # report a regular file, the one thing the listener cannot cause itself.
    path = f"{socket_dir}/a.sock"
    regular = f"{socket_dir}/regular"
    open(regular, "w").close()
    real_lstat = os.lstat

    def lstat(target, *args, **kwargs):
        result = real_lstat(target, *args, **kwargs)
        return real_lstat(regular) if target == path else result

    monkeypatch.setattr(os, "lstat", lstat)
    address = KBusAddress(f"uds:{path}")
    with pytest.raises(OSError, match="replaced"):
        await address.listen(lambda reader, writer: None)


async def test_unlinking_a_socket_already_removed_is_quiet(socket_dir):
    path = f"{socket_dir}/a.sock"
    address = KBusAddress(f"uds:{path}")
    server = await address.listen(lambda reader, writer: None)
    server.close()
    await server.wait_closed()
    os.unlink(path)
    address.unlink_owned_socket()
    assert not os.path.exists(path)


# -- kbus/callback.py --------------------------------------------------------------------


async def test_cancel_and_wait_reraises_the_cancellation_of_its_caller():
    inner_cancelled = asyncio.Event()

    async def slow_to_die():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            inner_cancelled.set()
            await asyncio.sleep(10)

    inner = asyncio.create_task(slow_to_die())
    await asyncio.sleep(0)
    outer = asyncio.create_task(cancel_and_wait(inner))
    await inner_cancelled.wait()
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert inner.cancelled()


# -- kbus/client.py ----------------------------------------------------------------------


async def test_a_bare_member_end_has_no_stream_to_open():
    with pytest.raises(NotImplementedError):
        await KBusEnd("bare").connect()


# -- kbus/connector.py -------------------------------------------------------------------


async def test_a_connector_cannot_start_twice():
    connector = KBusConnector(ScriptedStream(), name="a")
    connector.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            connector.start()
    finally:
        await connector.close()


async def test_a_reply_landed_before_the_timeout_resumed_the_caller_is_the_answer():
    connector = KBusConnector(AnsweringSlowStream(), name="a")
    connector.start()
    try:
        reply = await connector.call(call_frame(), timeout=0.05)
    finally:
        await connector.close()
    assert (reply.method, reply.payload) == (REPLY_METHOD, b"late")


async def test_post_requires_an_event_frame_on_a_connected_link():
    connector = KBusConnector(ScriptedStream(), name="a")
    event = Frame(method=EVENT_METHOD, path="/e")
    with pytest.raises(KBusCallFailed) as not_connected:
        await connector.post(event)
    assert not_connected.value.outcome == "not_sent"
    connector.start()
    try:
        with pytest.raises(ValueError, match="EVENT frame"):
            await connector.post(call_frame())
    finally:
        await connector.close()


async def test_a_post_whose_write_fails_has_an_unknown_outcome():
    connector = KBusConnector(ScriptedStream(write_error=BrokenPipeError("gone")), name="a")
    connector.start()
    try:
        with pytest.raises(KBusCallFailed, match="gone") as failed:
            await connector.post(Frame(method=EVENT_METHOD, path="/e"))
    finally:
        await connector.close()
    assert failed.value.outcome == "unknown"


async def test_past_the_event_ceiling_an_event_is_dropped(caplog):
    stream = ScriptedStream()
    release = asyncio.Event()
    seen: list[str] = []

    async def on_event(frame: Frame) -> None:
        seen.append(frame.path)
        await release.wait()

    connector = KBusConnector(stream, name="a", on_event=on_event, max_event_tasks=1)
    connector.start()
    try:
        stream.inbound.put_nowait(Frame(method=EVENT_METHOD, path="/first"))
        stream.inbound.put_nowait(Frame(method=EVENT_METHOD, path="/second"))
        await wait_until(lambda: "event /second dropped" in caplog.text)
        release.set()
        await asyncio.sleep(0.05)
    finally:
        await connector.close()
    assert seen == ["/first"]


async def test_a_late_reply_on_another_path_ends_the_link():
    stream = ScriptedStream()
    lost: list[KBusConnector] = []
    connector = KBusConnector(stream, name="a", on_lost=lost.append)
    connector.start()
    frame = call_frame("/asked")
    with pytest.raises(KBusCallFailed) as timed_out:
        await connector.call(frame, timeout=0.05)
    assert timed_out.value.outcome == "unknown"
    stream.inbound.put_nowait(Frame(id=frame.id, method=REPLY_METHOD, path="/other"))
    await connector.wait_closed()
    await wait_until(lambda: lost == [connector])


async def test_a_handler_that_returns_no_reply_answers_an_error_reply():
    stream = ScriptedStream()
    connector = KBusConnector(stream, name="a", on_call=lambda frame: None)
    connector.start()
    try:
        frame = call_frame("/served")
        stream.inbound.put_nowait(frame)
        await wait_until(lambda: stream.written)
    finally:
        await connector.close()
    reply = stream.written[0]
    assert reply.id == frame.id
    assert reply.info["error"] == "TypeError: on_call did not return the REPLY of /served"


async def test_an_error_reply_that_cannot_be_written_is_logged(caplog):
    stream = ScriptedStream(write_error=BrokenPipeError("gone"))

    def on_call(frame: Frame) -> Frame:
        raise LookupError("nothing here")

    connector = KBusConnector(stream, name="a", on_call=on_call)
    connector.start()
    try:
        stream.inbound.put_nowait(call_frame("/served"))
        await wait_until(lambda: "reply to /served not sent: gone" in caplog.text)
    finally:
        await connector.close()
    assert "call /served failed: LookupError: nothing here" in caplog.text


# -- kbus/hub.py -------------------------------------------------------------------------


def test_a_member_shows_its_name_and_pid():
    assert repr(KBusMember(KBusHub(), "billing", 42, {})) == "<KBusMember billing pid=42>"


async def test_a_network_hub_without_a_secret_does_not_start():
    hub = KBusHub(host="0.0.0.0")
    with pytest.raises(ValueError, match="needs a secret"):
        await hub.start()
    assert not hub.started


async def test_stop_closes_a_connection_still_presenting_itself(socket_dir):
    hub = KBusHub(path=f"{socket_dir}/hub.sock")
    await hub.start()
    reader, writer = await asyncio.open_unix_connection(f"{socket_dir}/hub.sock")
    try:
        await wait_until(lambda: hub._handshakes)
        await hub.stop()
        assert await asyncio.wait_for(reader.read(), 5) == b""
    finally:
        writer.close()
    assert hub.members == {}


async def test_an_awaitable_welcome_reaches_the_member():
    gate = Gate()
    gate.open.set()
    hub = KBusHub(on_member_joined=gate)
    local = LocalKBus("worker")
    member = await hub.attach_local(local)
    try:
        assert member is hub.resolve("worker")
        assert local.welcome == {"hello": "worker"}
    finally:
        await local.close()


async def test_a_cancelled_attach_cancels_the_member_connect():
    gate = Gate()
    hub = KBusHub(on_member_joined=gate)
    local = LocalKBus("worker")
    attaching = asyncio.create_task(hub.attach_local(local))
    await gate.entered.wait()
    attaching.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attaching
    assert not local.connected
    assert hub.members == {}
    gate.open.set()
    member = await hub.attach_local(LocalKBus("worker"))
    assert member is hub.members["worker"]
    await hub.stop()


async def test_a_hub_stopping_during_the_welcome_refuses_the_member():
    gate = Gate()
    hub = KBusHub(on_member_joined=gate)
    await hub.start()
    attaching = asyncio.create_task(hub.attach_local(LocalKBus("worker")))
    await gate.entered.wait()
    await hub.stop()
    gate.open.set()
    with pytest.raises(ConnectionError):
        await attaching
    assert hub.members == {}


async def test_a_welcome_that_cannot_be_written_drops_the_member():
    gate = Gate()
    hub = KBusHub(on_member_joined=gate)
    local = LocalKBus("worker")
    attaching = asyncio.create_task(hub.attach_local(local))
    await gate.entered.wait()
    await (await local.open_stream()).close()
    gate.open.set()
    with pytest.raises(ConnectionError):
        await attaching
    assert hub.members == {}


async def test_a_hub_stopped_while_the_welcome_is_written_drops_the_member():
    hub = KBusHub()
    await hub.start()
    local = SlowWelcomeKBus("worker")
    attaching = asyncio.create_task(hub.attach_local(local))
    await local.landed_stream.landed.wait()
    await hub.stop()
    local.landed_stream.release.set()
    assert await attaching is None
    assert hub.members == {}
    await local.wait_closed()


# -- kbus/spawner.py ---------------------------------------------------------------------


# -- kbus_mixin.py -----------------------------------------------------------------------


async def test_an_external_application_without_a_configuration_source_does_not_start():
    server = AsgiServer(applications=[(RemoteApplication, {"code": "billing", "spawner": "subprocess"})])
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    await inbox.put({"type": "lifespan.startup"})
    with pytest.raises(RuntimeError, match="configuration file or template"):
        await server({"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put)


async def test_a_call_whose_handler_fails_answers_500():
    server = AsgiServer(applications=[(Failing, {"code": "failing"})])
    with pytest.raises(KBusCallError) as failed:
        await server.kbus_call("/failing/broken")
    assert (failed.value.status, failed.value.error) == (500, "LookupError: gone")


# -- remote_application.py ---------------------------------------------------------------


