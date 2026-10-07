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

"""KBusHub — the parent side of the KajennBus.

The hub binds the socket the children connect to (``uds:`` in a private
directory, or ``tcp:``) through ``KBusAddress``: an existing pathname is never
stolen and ``stop()`` unlinks only the socket the hub created. It keeps the
rubric of the registered members, keyed by the name each member declares.

A member presents itself with a REGISTER frame (``{"name", "pid", ...}``,
kept whole as ``presentation``); the hub answers with a REPLY on the same id
and path whose payload is what ``on_member_joined(member)`` returns. An
exception from that callback is the refusal: an error REPLY, then the link
closes. On a non-loopback TCP address the hub needs a ``secret`` and refuses
a REGISTER whose ``presentation["secret"]`` is not it; on uds and loopback no
secret is checked. The secret never stays in ``presentation``. On a network
hub the REGISTER is read with a ceiling of ``REGISTER_MAX_SIZE`` bytes; the
member's link then uses ``max_size``. From then on every member is one ``KBusConnector``: the hub calls the
member and serves the member's calls with ``on_call(member, frame)``, and
EVENTs go to ``on_event(member, frame)``.

A member whose link ends is dropped from the rubric and
``on_member_lost(member)`` fires — sweep and relaunch belong to whoever owns
the member, not here. A deliberate ``stop()`` is not a death and fires
nothing: it stops listening, refuses any REGISTER, and closes every member,
including the connections still presenting themselves. An in-process member
joins through ``attach_local(local_kbus)``, over the same REGISTER path.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import inspect
import logging
import os
import shutil
import tempfile
from typing import Any, Callable

from .address import KBusAddress
from .callback import run_callback
from .client import control_call_frame, control_event_frame, control_reply_data
from .connector import KBusConnector
from .control import ControlPayload
from .frame import (
    REGISTER_METHOD,
    REPLY_METHOD,
    Frame,
    FrameStream,
    FrameStreamProtocol,
)
from .local import LocalKBus

__all__ = ["KBusHub", "KBusMember"]


class KBusMember:
    """A registered member: its presentation and the link to it."""

    __slots__ = ("connector", "hub", "name", "pid", "presentation")

    def __init__(
        self, hub: KBusHub, name: str, pid: int, presentation: dict[str, Any]
    ) -> None:
        self.hub = hub
        self.name = name
        self.pid = pid
        self.presentation = presentation
        self.connector: KBusConnector | None = None

    def __repr__(self) -> str:
        return f"<KBusMember {self.name} pid={self.pid}>"


class KBusHub:
    """Parent-side endpoint: binds the socket, keeps the rubric, links members.

    Give ``path`` for UDS, ``host`` (with ``port=0`` to let the OS choose) for
    TCP, or neither to get a socket in a private 0700 directory the hub owns
    and removes at ``stop()``.
    """

    REGISTER_TIMEOUT = 10.0
    REGISTER_MAX_SIZE = 65536

    def __init__(
        self,
        *,
        path: str | None = None,
        host: str | None = None,
        port: int = 0,
        on_member_joined: Callable[..., Any] | None = None,
        on_member_lost: Callable[..., Any] | None = None,
        on_call: Callable[..., Any] | None = None,
        on_event: Callable[..., Any] | None = None,
        max_size: int | None = None,
        secret: str | None = None,
    ) -> None:
        if path is not None and host is not None:
            raise ValueError("give path (uds) or host (tcp), not both")
        self.on_member_joined = on_member_joined
        self.on_member_lost = on_member_lost
        self.on_call = on_call
        self.on_event = on_event
        self.max_size = max_size
        self.secret = secret
        self.control_payload = ControlPayload()
        self.logger = logging.getLogger(__name__)
        self._owned_dir: str | None = None
        if path is None and host is None:
            self._owned_dir = tempfile.mkdtemp(prefix="kajenn_hub_")
            os.chmod(self._owned_dir, 0o700)
            path = os.path.join(self._owned_dir, "hub.sock")
        self.path = path
        self.host = host
        self.port = port
        self._kbus_address: KBusAddress | None = None
        self._server: asyncio.Server | None = None
        self._members: dict[str, KBusMember] = {}
        self._handshakes: set[FrameStreamProtocol] = set()
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
    def members(self) -> dict[str, KBusMember]:
        """Snapshot of the rubric, by member name."""
        return dict(self._members)

    async def start(self) -> None:
        """Bind the socket and start accepting children.

        Raises:
            FileExistsError: the UDS pathname already exists.
            ValueError: a non-loopback TCP address without ``secret``.
        """
        location = f"uds:{self.path}" if self.path is not None else f"tcp:{self.host}:{self.port}"
        self._kbus_address = KBusAddress(location, allow_network_listener=True)
        if self._kbus_address.network and not self.secret:
            raise ValueError(f"a KajennBus hub on {location} needs a secret")
        self._closing = False
        self._server = await self._kbus_address.listen(self._handle_connection)
        if self.path is None:
            self.port = self._server.sockets[0].getsockname()[1]
        self.logger.info("KajennBus hub listening on %s", self.address)

    async def stop(self) -> None:
        """Deliberate shutdown: close every member without firing member_lost."""
        if self._server is None:
            return
        self._closing = True
        self._server.close()
        for stream in list(self._handshakes):
            await stream.close()
        for member in list(self._members.values()):
            await member.connector.close()
        await self._server.wait_closed()
        self._server = None
        self._members.clear()
        self._kbus_address.unlink_owned_socket()
        if self._owned_dir is not None:
            shutil.rmtree(self._owned_dir, ignore_errors=True)
            self._owned_dir = None
        self.logger.info("KajennBus hub stopped")

    async def attach_local(self, local: LocalKBus) -> KBusMember:
        """Register and connect an in-process member in one call.

        Raises:
            ConnectionError: the hub refused the member.
        """
        connecting = asyncio.create_task(local.connect())
        try:
            member = await self._register_connection(local.hub_stream)
        except BaseException:
            connecting.cancel()
            await asyncio.gather(connecting, return_exceptions=True)
            raise
        await connecting
        return member

    def resolve(self, name: str) -> KBusMember | None:
        """The member registered under this member name, or ``None``."""
        return self._members.get(name)

    async def post(self, name: str, path: str, data: Any = None) -> None:
        """Send one control-json EVENT to one member."""
        await self._link(name).post(control_event_frame(path, data))

    async def call(
        self, name: str, path: str, data: Any = None, timeout: float | None = None
    ) -> Any:
        """CALL one member with control-json ``data``; returns the decoded REPLY payload.

        Raises:
            LookupError: no member has this name.
            KBusCallError: the REPLY carried ``info["error"]``.
        """
        reply = await self.call_frame(name, control_call_frame(path, data), timeout)
        return control_reply_data(reply)

    async def call_frame(self, name: str, frame: Frame, timeout: float | None = None) -> Frame:
        """Send a CALL frame to one member and return its whole REPLY frame."""
        return await self._link(name).call(frame, timeout)

    def _link(self, name: str) -> KBusConnector:
        member = self._members.get(name)
        if member is None:
            raise LookupError(f"no member named {name!r}")
        return member.connector

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Per-connection task: the first frame must be a REGISTER.

        On a network hub the REGISTER is read with ``REGISTER_MAX_SIZE``.
        """
        stream = FrameStream(reader, writer, max_size=self.max_size)
        register = stream
        if self._kbus_address is not None and self._kbus_address.network:
            register = FrameStream(
                reader, writer, max_size=min(self.REGISTER_MAX_SIZE, stream.max_size))
        await self._register_connection(stream, register)

    async def _register_connection(
        self, stream: FrameStreamProtocol, register: FrameStreamProtocol | None = None
    ) -> KBusMember | None:
        """Read the presentation, answer it, and start the member's link.

        ``register`` reads the REGISTER frame (``stream`` when not given).
        Anything but a valid REGISTER of a new name, or a REGISTER reaching a
        closing hub, closes the stream.
        """
        self._handshakes.add(stream)
        try:
            return await self._admit(stream, register or stream)
        finally:
            self._handshakes.discard(stream)

    async def _admit(
        self, stream: FrameStreamProtocol, register: FrameStreamProtocol
    ) -> KBusMember | None:
        """The REGISTER handshake of one connection; the member, or ``None``."""
        try:
            frame = await asyncio.wait_for(register.read(), timeout=self.REGISTER_TIMEOUT)
        except (OSError, TimeoutError, ValueError):
            self.logger.warning("Connection rejected: no valid REGISTER frame")
            await stream.close()
            return None
        presentation = None if self._closing else self._presentation(frame)
        if presentation is None:
            await stream.close()
            return None
        secret = presentation.pop("secret", None)
        member = KBusMember(self, presentation["name"], presentation["pid"], presentation)
        member.connector = KBusConnector(
            stream,
            name=member.name,
            on_call=None if self.on_call is None else lambda call: self.on_call(member, call),
            on_event=None if self.on_event is None else lambda event: self.on_event(member, event),
            on_lost=lambda _connector: self._member_lost(member),
        )
        self._members[member.name] = member
        try:
            network = self._kbus_address is not None and self._kbus_address.network
            if network and not (
                isinstance(secret, str)
                and hmac.compare_digest(secret.encode(), self.secret.encode())
            ):
                raise PermissionError("the network secret does not match")
            welcome = self.on_member_joined(member) if self.on_member_joined else None
            if inspect.isawaitable(welcome):
                welcome = await welcome
            if self._closing:
                raise ConnectionError("the hub is stopping")
            reply = Frame(
                id=frame.id,
                method=REPLY_METHOD,
                path=frame.path,
                info={"format": "control-json"},
                payload=self.control_payload.encode({} if welcome is None else welcome),
            )
        except asyncio.CancelledError:
            self._members.pop(member.name, None)
            await stream.close()
            raise
        except Exception as exc:
            self.logger.warning("Member %s refused: %s", member.name, exc)
            reply = Frame(
                id=frame.id,
                method=REPLY_METHOD,
                path=frame.path,
                info={"error": f"{type(exc).__name__}: {exc}"},
            )
            self._members.pop(member.name, None)
            with contextlib.suppress(OSError):
                await stream.write(reply)
            await stream.close()
            return None
        try:
            await stream.write(reply)
        except OSError:
            self._members.pop(member.name, None)
            await stream.close()
            return None
        if self._closing:
            self._members.pop(member.name, None)
            await stream.close()
            return None
        member.connector.start()
        self.logger.info("Member joined: %s", member)
        return member

    def _presentation(self, frame: Frame | None) -> dict[str, Any] | None:
        """The REGISTER payload with a valid name and pid, or ``None`` (logged)."""
        if frame is None or frame.method != REGISTER_METHOD:
            self.logger.warning("Connection rejected: first frame is not %s", REGISTER_METHOD)
            return None
        try:
            presentation = self.control_payload.decode(frame.payload)
        except ValueError:
            self.logger.warning("Connection rejected: invalid REGISTER control payload")
            return None
        if not isinstance(presentation, dict):
            self.logger.warning("Connection rejected: REGISTER payload is not an object")
            return None
        name = presentation.get("name")
        if not isinstance(name, str) or not name:
            self.logger.warning("Connection rejected: REGISTER without a valid name")
            return None
        if name in self._members:
            # A name is unique while registered: a relaunched process registers
            # the same code again only after its predecessor left the rubric,
            # so a name already in the rubric is a newcomer's protocol violation:
            # the registered member is the real one and stays.
            self.logger.warning("Connection rejected: name %s is already registered", name)
            return None
        try:
            presentation["pid"] = int(presentation.get("pid", 0))
        except (TypeError, ValueError, OverflowError):
            self.logger.warning("Connection rejected: REGISTER with invalid pid")
            return None
        return presentation

    async def _member_lost(self, member: KBusMember) -> None:
        if self._members.get(member.name) is member:
            self._members.pop(member.name)
            if not self._closing:
                self.logger.info("KajennBus member lost: %s", member.name)
                await run_callback(self.on_member_lost, member, logger=self.logger)
