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

"""KBusConnector — one live KajennBus link, the same at both ends.

Either end sends CALLs and serves the CALLs it receives; a REPLY is correlated
by id and path. A call whose fate is uncertain fails with ``outcome="unknown"``
and is never replayed; one that never reached the write fails with
``outcome="not_sent"``. A timed-out or cancelled call keeps its id reserved
until its late reply arrives or the link ends. A protocol violation ends the
link and fails every pending call.

The connector works over any ``FrameStreamProtocol``: a socket ``FrameStream``,
an in-process ``LocalFrameStream``, or any other object with ``read``,
``write`` and ``close``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from typing import Any, Callable

from .callback import cancel_and_wait, run_callback
from .frame import CALL_METHOD, EVENT_METHOD, REPLY_METHOD, Frame, FrameStreamProtocol

__all__ = ["KBusCallCancelled", "KBusCallFailed", "KBusConnector"]


class KBusCallFailed(ConnectionError):
    """A call that got no reply; ``outcome`` is ``not_sent`` or ``unknown``."""

    def __init__(self, reason: str, *, outcome: str) -> None:
        super().__init__(reason)
        self.outcome = outcome


#: The cancellation of a call: the caller's own ``asyncio.CancelledError``,
#: re-raised unchanged with ``outcome`` set (whether the CALL went out). It is
#: the very class, not a subclass, because ``asyncio.timeout`` turns into
#: ``TimeoutError`` only a cancellation whose type is exactly ``CancelledError``.
KBusCallCancelled = asyncio.CancelledError


class KBusConnector:
    """One symmetric KajennBus link over a frame stream."""

    def __init__(
        self,
        stream: FrameStreamProtocol,
        *,
        name: str,
        on_call: Callable[..., Any] | None = None,
        on_event: Callable[..., Any] | None = None,
        on_lost: Callable[..., Any] | None = None,
        max_pending: int = 1024,
        max_abandoned: int = 256,
        max_event_tasks: int = 1024,
    ) -> None:
        self.stream = stream
        self.name = name
        self.on_call = on_call
        self.on_event = on_event
        self.on_lost = on_lost
        self.max_pending = max_pending
        self.max_abandoned = max_abandoned
        self.max_event_tasks = max_event_tasks
        self._logger = logging.getLogger(__name__)
        self._pending: dict[str, tuple[asyncio.Future[Frame], str]] = {}
        self._abandoned: dict[str, str] = {}
        self._call_tasks: set[asyncio.Task[None]] = set()
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._read_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed_event = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Whether the link is up: started, not closed, not lost."""
        return self._read_task is not None and not self._closing and not self.closed

    @property
    def closed(self) -> bool:
        """Whether the link has ended, by ``close()``, EOF or violation."""
        return self._closed_event.is_set()

    def start(self) -> None:
        """Start the read loop that dispatches every inbound frame."""
        if self._read_task is not None:
            raise RuntimeError(f"KajennBus connector {self.name} already started")
        self._read_task = asyncio.create_task(self._read_loop())

    async def call(self, frame: Frame, timeout: float | None = None) -> Frame:
        """Send one CALL and return its whole REPLY frame.

        Raises:
            ValueError: the frame is not a CALL, or its id is already in flight
                or reserved by an abandoned call.
            KBusCallFailed: no reply; ``outcome`` is ``not_sent`` or ``unknown``.
            KBusCallCancelled: the caller was cancelled; the original
                ``CancelledError`` with ``outcome`` set.
        """
        if frame.method != CALL_METHOD:
            raise ValueError("KBusConnector.call requires a CALL frame")
        if not self.connected:
            raise KBusCallFailed(f"KajennBus link {self.name} is not connected", outcome="not_sent")
        if frame.id in self._pending or frame.id in self._abandoned:
            raise ValueError(f"duplicate in-flight correlation id {frame.id!r}")
        if len(self._pending) >= self.max_pending:
            raise KBusCallFailed(
                f"KajennBus link {self.name} has {self.max_pending} calls in flight",
                outcome="not_sent",
            )
        future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending[frame.id] = (future, frame.path)
        sent = False
        try:
            async with asyncio.timeout(timeout):
                # The write may succeed before it raises: never replay.
                sent = True
                await self.stream.write(frame)
                return await asyncio.shield(future)
        except asyncio.CancelledError as exc:
            if sent and not future.done():
                await self._abandon(frame)
            setattr(exc, "outcome", "unknown" if sent else "not_sent")
            raise
        except KBusCallFailed:
            raise
        except (OSError, TimeoutError) as exc:
            if isinstance(exc, TimeoutError) and future.done() and not future.cancelled():
                # The REPLY landed before the caller resumed: it is the answer.
                return future.result()
            if sent and not future.done():
                await self._abandon(frame)
            raise KBusCallFailed(
                str(exc) or type(exc).__name__, outcome="unknown" if sent else "not_sent"
            ) from exc
        finally:
            self._pending.pop(frame.id, None)
            if not future.done():
                future.cancel()

    async def post(self, frame: Frame) -> None:
        """Send one EVENT; no reply is expected."""
        if frame.method != EVENT_METHOD:
            raise ValueError("KBusConnector.post requires an EVENT frame")
        if not self.connected:
            raise KBusCallFailed(f"KajennBus link {self.name} is not connected", outcome="not_sent")
        try:
            await self.stream.write(frame)
        except OSError as exc:
            raise KBusCallFailed(str(exc) or type(exc).__name__, outcome="unknown") from exc

    async def close(self) -> None:
        """Deliberate close: ends the link without firing ``on_lost``."""
        self._closing = True
        try:
            with contextlib.suppress(Exception):
                await self.stream.close()
            if self._read_task is not None:
                await cancel_and_wait(self._read_task)
            self._finish("KajennBus link closed")
            tasks = self._call_tasks | self._event_tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._closed_event.set()

    async def wait_closed(self) -> None:
        """Block until the link has ended."""
        await self._closed_event.wait()

    async def _abandon(self, frame: Frame) -> None:
        """Reserve the id of a call nobody waits for; past the limit, end the link."""
        if len(self._abandoned) >= self.max_abandoned:
            self._logger.warning(
                "KajennBus link %s: %d calls abandoned without reply; closing the link",
                self.name,
                len(self._abandoned),
            )
            with contextlib.suppress(Exception):
                await self.stream.close()
            return
        self._abandoned[frame.id] = frame.path

    async def _read_loop(self) -> None:
        """Dispatch frames until EOF or a protocol violation."""
        reason = "KajennBus link ended"
        try:
            while (frame := await self.stream.read()) is not None:
                self._dispatch(frame)
        except (OSError, ValueError) as exc:
            reason = f"KajennBus protocol violation: {exc}"
            self._logger.warning("KajennBus link %s: %s; closing the link", self.name, exc)
        finally:
            self._finish(reason)
            if not self._closing:
                # Ended before the stream closes: a call in between fails not_sent.
                self._closed_event.set()
                with contextlib.suppress(Exception):
                    await self.stream.close()
                await run_callback(self.on_lost, self, logger=self._logger)

    def _dispatch(self, frame: Frame) -> None:
        """Route one inbound frame; a violation raises ``ValueError``."""
        if frame.method == REPLY_METHOD:
            self._receive_reply(frame)
        elif frame.method == CALL_METHOD:
            task = asyncio.create_task(self._serve_call(frame))
            self._call_tasks.add(task)
            task.add_done_callback(self._call_tasks.discard)
        elif frame.method == EVENT_METHOD:
            if len(self._event_tasks) >= self.max_event_tasks:
                self._logger.warning(
                    "KajennBus link %s: %d events in progress; event %s dropped",
                    self.name,
                    len(self._event_tasks),
                    frame.path,
                )
                return
            task = asyncio.create_task(run_callback(self.on_event, frame, logger=self._logger))
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
        else:
            raise ValueError(f"unexpected {frame.method} frame on a started link")

    def _receive_reply(self, frame: Frame) -> None:
        """Hand a REPLY to its call; drop it when nobody waits for it."""
        parked = self._pending.get(frame.id)
        if parked is not None:
            future, path = parked
            if frame.path != path:
                raise ValueError(f"reply {frame.id!r} on path {frame.path!r}, call was on {path!r}")
            if not future.done():
                future.set_result(frame)
            return
        path = self._abandoned.pop(frame.id, None)
        if path is not None and frame.path != path:
            raise ValueError(f"late reply {frame.id!r} on path {frame.path!r}, call was on {path!r}")
        if path is None:
            self._logger.debug("KajennBus link %s: reply %s with no caller dropped", self.name, frame.id)

    async def _serve_call(self, frame: Frame) -> None:
        """Serve one inbound CALL; any failure becomes an error REPLY."""
        try:
            if self.on_call is None:
                raise LookupError(f"KajennBus link {self.name} serves no calls")
            reply = self.on_call(frame)
            if inspect.isawaitable(reply):
                reply = await reply
            if not (
                isinstance(reply, Frame)
                and reply.method == REPLY_METHOD
                and reply.id == frame.id
                and reply.path == frame.path
            ):
                raise TypeError(f"on_call did not return the REPLY of {frame.path}")
            await self.stream.write(reply)
            return
        except OSError as exc:
            self._logger.warning("KajennBus link %s: reply to %s not sent: %s", self.name, frame.path, exc)
            return
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._logger.warning("KajennBus link %s: call %s failed: %s", self.name, frame.path, error)
        try:
            await self.stream.write(
                Frame(id=frame.id, method=REPLY_METHOD, path=frame.path, info={"error": error})
            )
        except OSError as exc:
            self._logger.warning("KajennBus link %s: reply to %s not sent: %s", self.name, frame.path, exc)

    def _finish(self, reason: str) -> None:
        """Fail every pending call, release reserved ids, stop served tasks."""
        for future, _ in self._pending.values():
            if not future.done():
                future.set_exception(KBusCallFailed(reason, outcome="unknown"))
        self._abandoned.clear()
        for task in self._call_tasks | self._event_tasks:
            task.cancel()
