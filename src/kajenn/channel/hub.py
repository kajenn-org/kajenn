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

"""Channel hub — the parent side of the channel, with typed envelopes.

The hub binds the socket the children connect to (``uds:`` in a private
directory, or ``tcp:`` as the multi-host door), keeps the rubric of the
registered members and routes envelopes. It is **transport-only**: it never
interprets an op name and never looks inside ``data`` beyond the three keys
the REPLY contract owns (``result``, ``error``, ``events``). The rubric key
is the full channel name the member declares in its REGISTER frame — the
typing happens at the member, not here.

Three envelope kinds ride the frame protocol of ``frame.py`` (which is not
modified: ``method`` carries the kind, ``path`` the routing key):

- ``CALL`` — a request. ``call()`` parks an ``asyncio.Future`` on the frame
  id and awaits the matching ``REPLY``. A CALL arriving FROM a member is
  served by nobody: it is logged as an unknown envelope.
- ``REPLY`` — the answer to a CALL, reusing its id. ``data`` is
  ``{result | error, events: [...]}`` and ``call()`` returns it **verbatim**:
  the barrier lives outside the transport, so the hub neither folds the
  events nor interprets ``result``/``error``. The consumer does both, in its
  own coroutine, after the future resolves. A CALL has **no default
  deadline**: the internal leg waits, because a member that applied the
  lifecycle MUST report it. A caller with an outer surface to protect passes
  its own ``timeout``. The terminator is member death: on EOF — and on a
  deliberate ``stop()`` — every pending CALL addressed to that member fails
  with ``ConnectionError``.
- ``EVENT`` — fire-and-forget: ``post()`` outbound, ``on_event(member,
  frame)`` inbound. An inbound EVENT is SERVED on its own task: resolving a
  REPLY is O(1) and stays inline, but running a consumer is work, and a slow
  one must not hold the member's receive loop away from the REPLY behind it.
  One task per EVENT also means per-member EVENT ordering is NOT preserved:
  a consumer that needs ordering must provide it itself.

An in-process member joins through ``attach_local(local_channel)`` — the same
REGISTER frame and the same receive loop over a queue-backed codec twin, so
the rubric holds one kind of member however it got here (``local.py``).

Liveness is the frame protocol's: EOF reports connection loss, so a member
whose stream ends is dropped from the rubric and ``on_channel_lost(member)``
fires — sweep and relaunch belong to whoever owns the member, not here. A deliberate
``stop()`` is not a death and fires nothing. A ``ValueError`` from the codec
is a protocol violation of ONE member: that connection is closed, the hub
and its other members are untouched. Callbacks may be sync or async and
their exceptions are logged, never fatal — a consumer bug must not fake a
member death.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import shutil
import tempfile
from typing import Any, Callable

from .control import ControlPayload
from .frame import REGISTER_METHOD, Frame, FrameStream
from .local import LocalChannel, LocalFrameStream

__all__ = [
    "CALL_METHOD",
    "EVENT_METHOD",
    "REPLY_METHOD",
    "ChannelCallError",
    "ChannelHub",
    "ChannelMember",
]

CALL_METHOD = "CALL"
REPLY_METHOD = "REPLY"
EVENT_METHOD = "EVENT"
MAX_PENDING_CALLS = 1024
MAX_EVENT_TASKS = 1024


class ChannelCallError(Exception):
    """A CALL answered with an error REPLY; ``error`` is the member's payload.

    ``payload`` is the whole REPLY the error arrived in: an errored REPLY can
    carry more than its error (a consumer's own delivery keys ride it), and
    whoever turns this exception into a response needs those keys untouched.
    """

    def __init__(
        self, member_name: str, path: str, error: Any, payload: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"call {path} on {member_name} failed: {error}")
        self.member_name = member_name
        self.path = path
        self.error = error
        self.payload = payload or {}


class ChannelMember:
    """A child connection in the rubric, keyed by the name it declared."""

    __slots__ = ("hub", "name", "pid", "stream")

    def __init__(
        self,
        hub: ChannelHub,
        name: str,
        pid: int,
        stream: FrameStream | LocalFrameStream,
    ) -> None:
        self.hub = hub
        self.name = name
        self.pid = pid
        self.stream = stream

    async def write(self, frame: Frame) -> None:
        """Send one frame, surfacing a link failure to its caller."""
        await self.stream.write(frame)

    def __repr__(self) -> str:
        return f"<ChannelMember {self.name} pid={self.pid}>"


class ChannelHub:
    """Parent-side endpoint: binds the socket, tracks members, routes envelopes.

    Give ``path`` for UDS, ``host`` (with ``port=0`` to let the OS choose) for
    TCP, or neither to get a socket in a private 0700 directory the hub owns
    and removes at ``stop()``.
    """

    REGISTER_TIMEOUT = 10.0

    def __init__(
        self,
        *,
        path: str | None = None,
        host: str | None = None,
        port: int = 0,
        on_member_joined: Callable[..., Any] | None = None,
        on_channel_lost: Callable[..., Any] | None = None,
        on_event: Callable[..., Any] | None = None,
        max_size: int | None = None,
        max_pending_calls: int = MAX_PENDING_CALLS,
        max_event_tasks: int = MAX_EVENT_TASKS,
    ) -> None:
        if path is not None and host is not None:
            raise ValueError("give path (uds) or host (tcp), not both")
        if max_pending_calls < 1 or max_event_tasks < 1:
            raise ValueError("max_pending_calls and max_event_tasks must be positive")
        self.on_member_joined = on_member_joined
        self.on_channel_lost = on_channel_lost
        self.on_event = on_event
        self.max_size = max_size
        self.control_payload = ControlPayload()
        self.max_pending_calls = max_pending_calls
        self.max_event_tasks = max_event_tasks
        self.logger = logging.getLogger(__name__)
        self._owned_dir: str | None = None
        if path is None and host is None:
            self._owned_dir = tempfile.mkdtemp(prefix="kajenn_hub_")
            os.chmod(self._owned_dir, 0o700)
            path = os.path.join(self._owned_dir, "hub.sock")
        self.path = path
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._members: dict[str, ChannelMember] = {}
        self._pending: dict[str, tuple[ChannelMember, str, asyncio.Future[Frame]]] = {}
        self._abandoned: dict[str, tuple[ChannelMember, str]] = {}
        self._local_loops: set[asyncio.Task[None]] = set()
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def started(self) -> bool:
        """Whether the socket is bound."""
        return self._server is not None

    @property
    def address(self) -> str:
        """The connectable address (``uds:<path>`` or ``tcp:<host>:<port>``)."""
        if self._server is None:
            raise RuntimeError("hub not started")
        if self.path is not None:
            return f"uds:{self.path}"
        return f"tcp:{self.host}:{self.port}"

    @property
    def members(self) -> dict[str, ChannelMember]:
        """Snapshot of the rubric, by channel name."""
        return dict(self._members)

    async def start(self) -> None:
        """Bind the socket and start accepting children."""
        if self.path is not None:
            self._server = await asyncio.start_unix_server(self._handle_connection, path=self.path)
        else:
            self._server = await asyncio.start_server(self._handle_connection, self.host, self.port)
            self.port = self._server.sockets[0].getsockname()[1]
        self.logger.info("Channel hub listening on %s", self.address)

    async def stop(self) -> None:
        """Deliberate shutdown: close every member without firing channel_lost.

        The members go first: ``Server.wait_closed()`` waits for the
        connection handlers, and a handler parked on a live member's read
        would never return.
        """
        if self._server is None:
            return
        self._closing = True
        for member in list(self._members.values()):
            await member.stream.close()
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._members.clear()
        self._fail_pending(None, "hub stopped")
        if self.path is not None and os.path.exists(self.path):
            os.unlink(self.path)
        if self._owned_dir is not None:
            shutil.rmtree(self._owned_dir, ignore_errors=True)
            self._owned_dir = None
        self.logger.info("Channel hub stopped")

    async def attach_local(self, local: LocalChannel) -> ChannelMember | None:
        """Register an in-process member arriving over a ``LocalChannel``.

        The single attachment point for the local wire: the REGISTER frame and
        the receive loop are the socket ones, so the rubric holds one kind of
        member however it got here.
        """
        member = await self._register_connection(local.hub_stream)
        if member is not None:
            loop_task = asyncio.create_task(self._receive_loop(member))
            self._local_loops.add(loop_task)
            loop_task.add_done_callback(self._local_loops.discard)
        return member

    def resolve(self, name: str) -> ChannelMember | None:
        """The member registered under this channel name, or ``None``."""
        return self._members.get(name)

    async def post(self, name: str, path: str, data: Any = None) -> str:
        """Send one EVENT to one member (fire-and-forget); returns the frame id."""
        member = self._members.get(name)
        if member is None:
            raise LookupError(f"no member named {name!r}")
        frame = Frame(method=EVENT_METHOD, path=path, payload=self.control_payload.encode(data))
        await member.write(frame)
        return frame.id

    async def call(
        self, name: str, path: str, data: Any = None, timeout: float | None = None
    ) -> Any:
        """CALL one member and await its REPLY; returns the REPLY ``data`` verbatim.

        The payload is the member's ``{result | error, events}`` dict, handed
        over untouched — reading it is the consumer's job. ``timeout`` is the
        caller's own deadline and expires with ``TimeoutError``; ``None`` waits
        indefinitely, until the REPLY lands or the member dies
        (``ConnectionError``).
        """
        member = self._members.get(name)
        if member is None:
            raise LookupError(f"no member named {name!r}")
        frame = Frame(method=CALL_METHOD, path=path, payload=self.control_payload.encode(data))
        reply = await self.call_frame(name, frame, timeout=timeout)
        return self.control_payload.decode(reply.payload)

    async def call_frame(self, name: str, frame: Frame, timeout: float | None = None) -> Frame:
        """Send an opaque CALL frame and return its matching opaque REPLY."""
        if frame.method != CALL_METHOD:
            raise ValueError("call_frame requires a CALL frame")
        member = self._members.get(name)
        if member is None:
            raise LookupError(f"no member named {name!r}")
        if len(self._pending) >= self.max_pending_calls:
            raise RuntimeError(f"channel has {self.max_pending_calls} outstanding calls")
        if frame.id in self._pending:
            raise RuntimeError(f"a call with id {frame.id!r} is already pending")
        if frame.id in self._abandoned:
            raise RuntimeError(f"a call with id {frame.id!r} is awaiting a late reply")
        future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending[frame.id] = (member, frame.path, future)
        sent = False
        try:
            # A cancellation during drain cannot prove that no bytes reached
            # the peer, so crossing the write boundary is conservatively sent.
            sent = True
            await member.write(frame)
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        except (TimeoutError, asyncio.CancelledError):
            if (
                sent
                and (not future.done() or future.cancelled())
                and self._members.get(member.name) is member
            ):
                if len(self._abandoned) >= self.max_pending_calls:
                    # Cleanup must not replace this caller's timeout/cancellation.
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await member.stream.close()
                else:
                    self._abandoned[frame.id] = (member, frame.path)
            raise
        finally:
            self._pending.pop(frame.id, None)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Per-connection task: require REGISTER as the first frame, then relay."""
        stream = FrameStream(
            reader, writer, max_size=self.max_size
        )
        member = await self._register_connection(stream)
        if member is not None:
            await self._receive_loop(member)

    async def _register_connection(
        self, stream: FrameStream | LocalFrameStream
    ) -> ChannelMember | None:
        """Read and validate the presentation frame; reject anything else."""
        try:
            frame = await asyncio.wait_for(stream.read(), timeout=self.REGISTER_TIMEOUT)
        except (TimeoutError, ValueError):
            self.logger.warning("Connection rejected: no valid REGISTER frame")
            await stream.close()
            return None
        if frame is None or frame.method != REGISTER_METHOD:
            self.logger.warning("Connection rejected: first frame is not %s", REGISTER_METHOD)
            await stream.close()
            return None
        try:
            info = self.control_payload.decode(frame.payload)
        except ValueError:
            self.logger.warning("Connection rejected: invalid REGISTER control payload")
            await stream.close()
            return None
        if not isinstance(info, dict):
            self.logger.warning("Connection rejected: REGISTER payload is not an object")
            await stream.close()
            return None
        name = info.get("name")
        if not isinstance(name, str) or not name:
            self.logger.warning("Connection rejected: REGISTER without a valid name")
            await stream.close()
            return None
        if name in self._members:
            # Names are minted by whoever spawns the members and never reused,
            # so a name already in the rubric is a newcomer's protocol violation:
            # the registered member is the real one and stays.
            self.logger.warning("Connection rejected: name %s is already registered", name)
            await stream.close()
            return None
        try:
            pid = int(info.get("pid", 0))
        except (TypeError, ValueError, OverflowError):
            self.logger.warning("Connection rejected: REGISTER with invalid pid")
            await stream.close()
            return None
        member = ChannelMember(self, name, pid, stream)
        self._members[name] = member
        await self._fire(self.on_member_joined, member)
        self.logger.info("Member joined: %s", member)
        return member

    async def _receive_loop(self, member: ChannelMember) -> None:
        """Read this member's frames until the channel ends; EOF → channel lost."""
        try:
            while True:
                try:
                    frame = await member.stream.read()
                except ValueError:
                    self.logger.exception(
                        "Protocol violation from %s; closing that member", member.name
                    )
                    break
                if frame is None:
                    break
                await self._dispatch(member, frame)
        except asyncio.CancelledError:
            return
        finally:
            await member.stream.close()
            if self._members.get(member.name) is member:
                del self._members[member.name]
                self._fail_pending(member, f"channel to {member.name} lost")
                self._abandoned = {
                    frame_id: expected
                    for frame_id, expected in self._abandoned.items()
                    if expected[0] is not member
                }
                if not self._closing:
                    self.logger.info("Channel lost: %s", member.name)
                    await self._fire(self.on_channel_lost, member)

    async def _dispatch(self, member: ChannelMember, frame: Frame) -> None:
        """Route one inbound frame by envelope kind: resolve inline, serve on a task.

        A REPLY only hands a payload to a parked future — O(1), so it stays in
        the receive loop. An EVENT runs a consumer, so it goes on its own task
        and the loop returns to the wire; the ref is held here because the
        loop keeps only a weak one. One task per EVENT means per-member EVENT
        ordering is not preserved: an ordering-sensitive consumer provides its own.
        """
        if frame.method == REPLY_METHOD:
            await self._resolve_reply(member, frame)
        elif frame.method == EVENT_METHOD:
            if len(self._event_tasks) >= self.max_event_tasks:
                self.logger.warning(
                    "Dropping EVENT %s from %s: event task limit %s reached",
                    frame.path,
                    member.name,
                    self.max_event_tasks,
                )
                return
            task = asyncio.create_task(self._fire(self.on_event, member, frame))
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
        else:
            self.logger.warning("Unknown envelope %s from %s", frame.method, member.name)

    async def _resolve_reply(self, member: ChannelMember, frame: Frame) -> None:
        """Hand the REPLY payload to the parked future, verbatim.

        A REPLY whose caller already went away — its deadline expired, or it
        was cancelled — is dropped: nobody is left to read the envelope.
        """
        abandoned = self._abandoned.get(frame.id)
        if abandoned is not None:
            if abandoned == (member, frame.path):
                del self._abandoned[frame.id]
            else:
                self.logger.warning(
                    "REPLY %s from %s does not match its abandoned CALL", frame.id, member.name
                )
                await member.stream.close()
            return
        parked = self._pending.get(frame.id)
        if parked is None or parked[2].done():
            self.logger.debug("REPLY %s from %s has no parked caller", frame.id, member.name)
            return
        if parked[0] is not member or parked[1] != frame.path:
            self.logger.warning(
                "REPLY %s from %s does not match its pending CALL", frame.id, member.name
            )
            await member.stream.close()
            return
        parked[2].set_result(frame)

    def _fail_pending(self, member: ChannelMember | None, reason: str) -> None:
        """Fail one member's pending CALLs (``None`` = all) with ``ConnectionError``.

        The entries stay in ``_pending``: each caller pops its own in the
        ``finally`` of ``call_frame()``.
        """
        for expected_member, _path, future in self._pending.values():
            if (member is None or expected_member is member) and not future.done():
                future.set_exception(ConnectionError(reason))

    async def _fire(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        """Run a sync-or-async callback; a consumer bug must not sever the channel."""
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            self.logger.exception("Channel callback %r failed", callback)
