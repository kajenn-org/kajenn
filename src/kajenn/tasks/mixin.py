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

"""Task capability: the task backbone as a mixin over the base server (◆D22, D16).

``TaskMixin`` is a capability composed over ``BaseServer`` AFTER ``StorageMixin``
(it needs ``server.storage``) and BEFORE ``BaseServer`` (the loop hook must sit
inside the D16 chain so it wraps ``Lifespan``). Its cooperative ``__init__`` peels
``tasks=`` and forwards everything else down the chain (mirroring
``StorageMixin.__init__``); a composition WITHOUT the mixin has no ``tasks``
attribute at all.

``tasks=`` is the on/off switch AND the tuning carrier: absent or truthy → the
``TaskManager`` is armed and the worker loop runs at startup; ``tasks=False`` →
no manager, and the lifespan passes straight through (no loop); a dict —
``{"enabled": ..., "tick_seconds": ..., "mount": ...}`` — peels ``enabled`` and
stashes the rest as ``tasks_config`` for the manager to apply. Storage/session
are on when their mixin is present, so tasks matches: a composed ``AsgiServer``
runs the worker loop out of the box.

``TaskGrammar`` is this mixin's grammar companion (D16: the element is declared
by the class that peels the kwarg). The server's grammar composes it explicitly
(``config/elements.py``) so ``server(...).tasks(enabled=, tick_seconds=,
mount=)`` lifts to this mixin's ``tasks=`` kwarg — the element is a void one, so
a stray child is rejected at the recipe line by the grammar itself.

The manager is built LAZILY on first ``tasks`` access, never in ``__init__``: the
cooperative chain runs ``TaskMixin.__init__`` (composed after ``StorageMixin``) from
INSIDE ``StorageMixin.__init__``, before that mixin has assigned ``server.storage`` —
so the manager, which opens its spool over ``server.storage``, cannot be built until
the whole chain has completed. Lazy construction defers it to the first use (the
lifespan hook, or a caller reaching ``server.tasks``), when the server is fully live.

The loop is server-owned, hooked in ``__call__`` — ``lifespan.py`` is NEVER touched
(ratified). ``__call__`` intercepts the ``lifespan`` scope exactly like
``CommunicationMixin.__call__``: pre-receive ``lifespan.startup``, ``manager.start()``,
replay the startup down ``super().__call__`` (so the base ``Lifespan`` still runs the
app hooks and acks the protocol), and ``await manager.stop()`` in ``finally`` when the
protocol completes at shutdown. Every other scope — and the disabled case — passes
straight through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_bag import BagResolver
from genro_builders.builder import element

from .manager import TaskManager

if TYPE_CHECKING:
    from ..types import Message, Receive, Scope, Send

__all__ = ["TaskGrammar", "TaskMixin"]


class TaskGrammar:
    """Config grammar owned by ``TaskMixin`` (the class that peels ``tasks=``).

    The server's grammar composes this mixin explicitly
    (``config/elements.py``) — no auto-discovery.
    """

    @element(sub_tags="", parent_tags="server")
    def tasks(
        self,
        enabled: bool = True,
        tick_seconds: float | BagResolver = None,
        mount: str | BagResolver = None,
    ) -> None:
        """The task backbone: ``enabled`` (default True — the on/off switch),
        ``tick_seconds`` (the scheduler tick), ``mount`` (explicit task-store
        mount, overriding the by-keys choice). Server-domain, so it lives under
        ``server``. The attributes lift to the server's ``tasks=`` kwarg as a
        dict."""


class TaskMixin:
    """Task capability mixin, composed after ``StorageMixin`` and before the base.

    Constructor kwargs peeled here: ``tasks`` — the on/off switch (default on)
    or the ``{enabled, tick_seconds, mount}`` tuning dict lifted from the
    ``tasks()`` config element. When enabled, builds the ``TaskManager`` and
    starts/stops its worker loop around the ASGI ``lifespan`` protocol.
    """

    grammar: type = TaskGrammar

    def __init__(self, **kwargs: Any) -> None:
        tasks: Any = kwargs.pop("tasks", True)
        super().__init__(**kwargs)
        if isinstance(tasks, dict):
            self._tasks_config: dict[str, Any] = dict(tasks)
            self._tasks_enabled: bool = bool(self._tasks_config.pop("enabled", True))
        else:
            self._tasks_config = {}
            self._tasks_enabled = bool(tasks)
        self._task_manager: TaskManager | None = None      # built lazily (see tasks)

    @property
    def tasks(self) -> TaskManager:
        """The task manager this server owns (built on first access); disabled is an error.

        Lazy: the manager opens its spool over ``server.storage``, which is not yet
        set while the cooperative ``__init__`` chain is still running — so the first
        access after the server is live builds it.
        """
        if not self._tasks_enabled:
            raise RuntimeError("tasks are disabled (tasks=False at init)")
        if self._task_manager is None:
            self._task_manager = TaskManager(self)
        return self._task_manager

    @property
    def tasks_enabled(self) -> bool:
        """Whether the task backbone is armed (``tasks`` not ``False`` at init)."""
        return self._tasks_enabled

    @property
    def tasks_config(self) -> dict[str, Any]:
        """The tuning peeled from a ``tasks=`` dict (``tick_seconds``, ``mount``).

        Empty when ``tasks=`` was a plain switch; the ``TaskManager`` applies it
        at build time.
        """
        return self._tasks_config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Hook the lifespan to start/stop the worker loop (the D16 proof).

        When tasks are enabled the loop starts on ``lifespan.startup`` (replayed to
        the base handler so the protocol stays intact) and stops when the protocol
        completes at shutdown. Disabled, or any non-lifespan scope, passes straight
        through.
        """
        if scope["type"] != "lifespan" or not self._tasks_enabled:
            await super().__call__(scope, receive, send)
            return
        manager = self.tasks
        startup: Message = await receive()
        manager.start()
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
            await manager.stop()
