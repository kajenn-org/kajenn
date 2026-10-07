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

"""KBusClient — the child side of the parent↔child KajennBus.

Knowing how to BE a child is part of what a server IS (SPECIFICATION.md
◆D10). ``connect()`` retries with short backoff until ``connect_timeout``
(boot race: the hub socket may not be bound yet), presents the child with a
REGISTER frame (``{"name", "pid", **presentation}``) and waits for the hub's
REPLY: its payload is the ``welcome``, an error REPLY or a closed link fails
the connect with ``ConnectionError``. From then on the link is one
``KBusConnector``: either side calls, either side serves.

There is no steady-state reconnection: when the hub side goes away,
``on_orphan(client)`` fires and the child is expected to terminate cleanly.
A deliberate ``close()`` fires no orphan signal.

Addresses::

    uds:/path/to/hub.sock     Unix domain socket (default)
    tcp:127.0.0.1:8731        TCP, loopback
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

from .address import KBusAddress
from .callback import run_callback
from .connector import KBusCallFailed, KBusConnector
from .control import ControlPayload
from .frame import (
    CALL_METHOD,
    EVENT_METHOD,
    REGISTER_METHOD,
    REGISTER_PATH,
    REPLY_METHOD,
    Frame,
    FrameStream,
    FrameStreamProtocol,
)

__all__ = ["KBusCallError", "KBusClient"]

CONTROL_INFO = {"format": "control-json"}


class KBusCallError(Exception):
    """A CALL answered with an error REPLY; ``error`` is what the REPLY carried."""

    def __init__(self, path: str, error: Any, status: int | None = None) -> None:
        super().__init__(f"call {path} failed: {error}")
        self.path = path
        self.error = error
        self.status = status


def control_call_frame(path: str, data: Any) -> Frame:
    """A CALL carrying ``data`` as control-json."""
    return Frame(
        method=CALL_METHOD, path=path, info=dict(CONTROL_INFO), payload=ControlPayload().encode(data)
    )


def control_event_frame(path: str, data: Any) -> Frame:
    """An EVENT carrying ``data`` as control-json."""
    return Frame(
        method=EVENT_METHOD, path=path, info=dict(CONTROL_INFO), payload=ControlPayload().encode(data)
    )


def control_reply_data(reply: Frame) -> Any:
    """The decoded payload of a REPLY; an ``info["error"]`` raises ``KBusCallError``."""
    if "error" in reply.info:
        raise KBusCallError(reply.path, reply.info["error"])
    return ControlPayload().decode(reply.payload)


class KBusEnd:
    """The member side of one KajennBus link, whatever carries it.

    Subclasses open the stream; this class presents, waits for the welcome,
    and delegates the live link to one ``KBusConnector``.
    """

    def __init__(
        self,
        name: str,
        *,
        presentation: dict[str, Any] | None = None,
        on_call: Callable[..., Any] | None = None,
        on_event: Callable[..., Any] | None = None,
        on_orphan: Callable[..., Any] | None = None,
        connect_timeout: float = 10.0,
        max_size: int | None = None,
    ) -> None:
        self.name = name
        self.presentation = dict(presentation or {})
        self.on_call = on_call
        self.on_event = on_event
        self.on_orphan = on_orphan
        self.connect_timeout = connect_timeout
        self.max_size = max_size
        self.welcome: Any = None
        self.connector: KBusConnector | None = None
        self._logger = logging.getLogger(__name__)
        self._closed_event = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Whether the link is up (welcomed, not ended)."""
        return self.connector is not None and self.connector.connected

    @property
    def closed(self) -> bool:
        """Whether the link ended (either side; ``False`` before connect)."""
        return self._closed_event.is_set()

    async def open_stream(self) -> FrameStreamProtocol:
        """The stream this end presents itself on."""
        raise NotImplementedError

    async def connect(self) -> None:
        """Present the REGISTER frame, wait for the welcome, start the link."""
        if self.connected:
            raise RuntimeError(f"KajennBus end {self.name} is already connected")
        stream = await self.open_stream()
        register = Frame(
            method=REGISTER_METHOD,
            path=REGISTER_PATH,
            info=dict(CONTROL_INFO),
            payload=ControlPayload().encode(
                {"name": self.name, "pid": os.getpid(), **self.presentation}
            ),
        )
        try:
            await stream.write(register)
            reply = await asyncio.wait_for(stream.read(), self.connect_timeout)
            if reply is None or reply.method != REPLY_METHOD or reply.id != register.id:
                raise ConnectionError(f"hub refused {self.name}: no welcome")
            self.welcome = control_reply_data(reply)
        except (KBusCallError, OSError, TimeoutError, ValueError) as exc:
            await stream.close()
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError(f"hub refused {self.name}: {exc}") from exc
        self._closed_event.clear()
        self.connector = KBusConnector(
            stream,
            name=self.name,
            on_call=self.on_call,
            on_event=self.on_event,
            on_lost=self._orphaned,
        )
        self.connector.start()
        self._logger.info("KajennBus end %s connected", self.name)

    async def call(self, path: str, data: Any = None, timeout: float | None = None) -> Any:
        """CALL the hub with control-json ``data``; returns the decoded REPLY payload."""
        return control_reply_data(await self.call_frame(control_call_frame(path, data), timeout))

    async def call_frame(self, frame: Frame, timeout: float | None = None) -> Frame:
        """Send a CALL frame and return the whole REPLY frame."""
        return await self.link().call(frame, timeout)

    async def post(self, path: str, data: Any = None) -> None:
        """Send one control-json EVENT to the hub."""
        await self.link().post(control_event_frame(path, data))

    async def close(self) -> None:
        """Deliberate close: no orphan signal."""
        if self.connector is not None:
            await self.connector.close()
        self._closed_event.set()

    async def wait_closed(self) -> None:
        """Block until the link ends (either side); the member's main wait."""
        await self._closed_event.wait()

    def link(self) -> KBusConnector:
        """The live connector; ``KBusCallFailed(not_sent)`` when there is none."""
        if self.connector is None or not self.connector.connected:
            raise KBusCallFailed(f"KajennBus end {self.name} is not connected", outcome="not_sent")
        return self.connector

    async def _orphaned(self, connector: KBusConnector) -> None:
        self._closed_event.set()
        self._logger.info("Hub side gone: %s is orphan", self.name)
        await run_callback(self.on_orphan, self, logger=self._logger)


class KBusClient(KBusEnd):
    """Child-side endpoint over a socket: ``uds:`` or loopback ``tcp:``.

    ``secret`` is presented in the REGISTER and admits a non-loopback ``tcp:``
    destination: the network listener it opens to requires it.
    """

    def __init__(self, address: str, name: str, *, secret: str | None = None, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        if secret:
            self.presentation["secret"] = secret
        self.address = address
        self.kbus_address = KBusAddress(address, allow_network_client=bool(secret))

    async def open_stream(self) -> FrameStream:
        """Connect with boot-time retry/backoff until ``connect_timeout``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_timeout
        interval = 0.05
        while True:
            try:
                reader, writer = await self.kbus_address.connect()
                return FrameStream(reader, writer, max_size=self.max_size)
            except OSError:
                if loop.time() + interval >= deadline:
                    raise ConnectionError(
                        f"hub not reachable at {self.address} within {self.connect_timeout}s"
                    ) from None
                await asyncio.sleep(interval)
                interval = min(interval * 2, 0.5)
