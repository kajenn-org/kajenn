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

"""Channel client — the child side of the parent↔child channel.

Knowing how to BE a child is part of what a server IS (SPECIFICATION.md
◆D10): the minimal package ships the frame protocol and this client; the hub
(parent side) lives in the orchestration package and imports the protocol
from below, never the reverse.

``connect()`` retries with short backoff until ``connect_timeout`` (boot
race: the hub socket may not be bound yet) and presents the child with a
REGISTER frame (``data={"name", "pid"}``). Steady state is fire-and-forget
frames in both directions. There is no steady-state reconnection: when the
hub side goes away (EOF — the connection-loss signal),
``on_orphan(client)`` fires and the child is expected to terminate cleanly.
A deliberate ``close()`` fires no orphan signal.

Callbacks (``on_message(frame)``, ``on_orphan(client)``) may be sync or
async; an exception raised by a callback is logged and never severs the
channel — a consumer bug must not fake a member death.

Addresses::

    uds:/path/to/hub.sock     Unix domain socket (default)
    tcp:127.0.0.1:8731        TCP (multi-host door)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Callable

from .control import ControlPayload
from .frame import REGISTER_METHOD, REGISTER_PATH, Frame, FrameStream

__all__ = ["ChannelClient"]


class ChannelClient:
    """Child-side endpoint: connect to the hub, present itself, relay frames."""

    def __init__(
        self,
        address: str,
        name: str,
        *,
        on_message: Callable[..., Any] | None = None,
        on_orphan: Callable[..., Any] | None = None,
        connect_timeout: float = 10.0,
        max_size: int | None = None,
    ) -> None:
        self.address = address
        self.name = name
        self.on_message = on_message
        self.on_orphan = on_orphan
        self.connect_timeout = connect_timeout
        self.max_size = max_size
        self.control_payload = ControlPayload()
        transport, _, rest = address.partition(":")
        self._uds_path: str | None = None
        self._tcp: tuple[str, int] | None = None
        if transport == "uds" and rest:
            self._uds_path = rest
        elif transport == "tcp" and rest:
            host, _, port = rest.rpartition(":")
            if not host or not port.isdigit():
                raise ValueError(f"invalid tcp address: {address!r}")
            self._tcp = (host, int(port))
        else:
            raise ValueError(
                f"invalid channel address: {address!r} (uds:<path> | tcp:<host>:<port>)"
            )
        self._logger = logging.getLogger(__name__)
        self._stream: FrameStream | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._connected = False
        self._closing = False
        self._closed_event = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Whether the channel is up (REGISTER sent, receive loop running)."""
        return self._connected

    @property
    def closed(self) -> bool:
        """Whether the channel ended (either side; ``False`` before connect)."""
        return self._closed_event.is_set()

    async def connect(self) -> None:
        """Connect with boot-time retry/backoff, present the REGISTER frame."""
        if self.connected:
            raise RuntimeError("channel client is already connected")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_timeout
        interval = 0.05
        while True:
            try:
                reader, writer = await self._open_connection()
                break
            except OSError:
                if loop.time() + interval >= deadline:
                    raise ConnectionError(
                        f"hub not reachable at {self.address} within {self.connect_timeout}s"
                    ) from None
                await asyncio.sleep(interval)
                interval = min(interval * 2, 0.5)
        self._stream = FrameStream(
            reader, writer, max_size=self.max_size
        )
        register = Frame(
            method=REGISTER_METHOD,
            path=REGISTER_PATH,
            payload=self.control_payload.encode({"name": self.name, "pid": os.getpid()}),
        )
        await self._stream.write(register)
        self._connected = True
        self._closed_event.clear()
        self._receive_task = asyncio.create_task(self._receive_loop(self._stream))
        self._logger.info("Connected to hub at %s as %s", self.address, self.name)

    async def close(self) -> None:
        """Deliberate local close: no orphan signal."""
        self._closing = True
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._stream is not None:
            await self._stream.close()
            self._stream = None
        self._connected = False
        self._closed_event.set()

    async def wait_closed(self) -> None:
        """Block until the channel ends (either side); the child's main wait."""
        await self._closed_event.wait()

    async def send(self, *, method: str = "POST", path: str = "/", data: Any = None) -> str:
        """Send one frame to the hub (fire-and-forget); returns the frame id.

        A connection dropping mid-send is a dying hub: the frame is lost by
        design and the orphan signal follows on the receive side.
        """
        if self._stream is None or not self.connected:
            raise ConnectionError("not connected")
        frame = Frame(method=method, path=path, payload=self.control_payload.encode(data))
        return await self.send_frame(frame)

    async def send_frame(self, frame: Frame) -> str:
        """Send an already encoded frame without opening its payload."""
        if self._stream is None or not self.connected:
            raise ConnectionError("not connected")
        await self._stream.write(frame)
        return frame.id

    async def _open_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open the transport for the parsed address (uds or tcp)."""
        if self._uds_path is not None:
            return await asyncio.open_unix_connection(self._uds_path)
        host, port = self._tcp
        return await asyncio.open_connection(host, port)

    async def _receive_loop(self, stream: FrameStream) -> None:
        """Read frames until the channel ends; hub gone → orphan.

        A protocol violation from the hub (oversized or non-wsx frame) is a
        clean death: logged, the loop breaks and the orphan path follows —
        the exception never leaves the task. The ``finally`` closes the
        stream so the writer never outlives the loop.
        """
        try:
            while True:
                try:
                    frame = await stream.read()
                except ValueError:
                    self._logger.exception("Protocol violation from the hub; closing the channel")
                    break
                if frame is None:
                    break
                await self._fire(self.on_message, frame)
        except asyncio.CancelledError:
            return
        finally:
            if self._stream is stream:
                self._connected = False
                self._stream = None
            await stream.close()
            self._closed_event.set()
            if not self._closing:
                self._logger.info("Hub connection lost: %s is orphan", self.name)
                await self._fire(self.on_orphan, self)

    async def _fire(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        """Run a sync-or-async callback; a consumer bug must not sever the channel."""
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            self._logger.exception("Channel callback %r failed", callback)
