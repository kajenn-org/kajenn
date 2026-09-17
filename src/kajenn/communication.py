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

"""Communication capability: the first mixin over the base server (D17).

The base server is born WITHOUT channels. This mixin adds the communication
capability as member objects built by its cooperative ``__init__``:
``parent_channel`` — the ``ChannelClient`` of ◆D10, ARMED iff
``parent=<address>`` was given — and ``children_channel`` — the hub side, a
placeholder member the orchestration package arms (the minimal package knows
how to BE a child, not how to HAVE children). Accessing an unarmed side
raises ``RuntimeError`` ("not armed"); a composition WITHOUT the mixin simply
lacks the attributes — a different type, not a ghost.

This is the first REAL proof of the D16 cooperative contract: composed
BEFORE the server class (``class MyServer(CommunicationMixin, BaseServer)``)
the mixin peels its own kwargs, forwards the rest, and hooks the lifespan
without the base knowing it — ``__call__`` cooperatively intercepts the
``lifespan`` scope: an armed parent side pre-receives ``lifespan.startup``,
connects (and REGISTERs) BEFORE any app hook runs, and disconnects when the
protocol completes at shutdown. An unreachable hub answers
``lifespan.startup.failed`` and re-raises: a child that cannot register must
die, never serve detached.

Registration makes the registrant a child in the tree even when not spawned
by us (communication ≠ process lifecycle, D17): the REGISTER frame presents
``{"name", "pid"}``; the name is derived from the class name and the pid
(child naming proper belongs to the orchestration package).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .channel import ChannelClient

if TYPE_CHECKING:
    from .types import Message, Receive, Scope, Send

__all__ = ["CommunicationMixin"]


class CommunicationMixin:
    """Communication capability mixin, composed BEFORE a server class.

    Constructor kwargs peeled here: ``parent`` — the address of the parent
    hub (``uds:<path>`` | ``tcp:<host>:<port>``); when given, the parent
    side is armed with a ``ChannelClient``.
    """

    def __init__(self, **kwargs: Any) -> None:
        parent: str | None = kwargs.pop("parent", None)
        super().__init__(**kwargs)
        self._parent_channel: ChannelClient | None = None
        if parent is not None:
            name = f"{type(self).__name__.lower()}-{os.getpid()}"
            self._parent_channel = ChannelClient(parent, name)
        self._children_channel: Any = None

    @property
    def parent_armed(self) -> bool:
        """Whether the parent side was armed (``parent=`` given at init)."""
        return self._parent_channel is not None

    @property
    def parent_channel(self) -> ChannelClient:
        """The channel to the parent hub; unarmed access is an error."""
        if self._parent_channel is None:
            raise RuntimeError("parent_channel is not armed (no parent= at init)")
        return self._parent_channel

    @property
    def children_channel(self) -> Any:
        """The hub side; a placeholder the orchestration package arms."""
        if self._children_channel is None:
            raise RuntimeError(
                "children_channel is not armed (the hub side lives in the orchestration package)"
            )
        return self._children_channel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Hook the lifespan without the base knowing it (the D16 proof).

        An armed parent side pre-receives ``lifespan.startup`` (replayed to
        the base handler so the protocol stays intact), connects (and
        REGISTERs) before any app hook runs, and disconnects when the
        protocol completes at shutdown. An unreachable hub answers
        ``lifespan.startup.failed`` and re-raises the ``ConnectionError`` —
        the child dies instead of serving detached. Every other scope passes
        straight through.
        """
        if scope["type"] != "lifespan" or not self.parent_armed:
            await super().__call__(scope, receive, send)
            return
        startup: Message = await receive()
        try:
            await self.parent_channel.connect()
        except ConnectionError as error:
            await send({"type": "lifespan.startup.failed", "message": str(error)})
            raise
        replayed = False

        async def replaying_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return startup
            return await receive()

        try:
            await super().__call__(scope, replaying_receive, send)
        finally:
            await self.parent_channel.close()
