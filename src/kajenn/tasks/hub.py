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

"""EventHub — in-memory per-session event fan-out (the live courier, ◆D22).

The spool on storage is the SOURCE OF TRUTH for task progress (the worker writes
``progress.json`` at each tick); the hub is the LIVE COURIER that carries the same
event to whoever is watching NOW. A subscriber is a bounded ``asyncio.Queue`` keyed
by ``session_id`` (the launching MCP session). The executor pairs each
``spool.write_progress(...)`` with a ``hub.publish(session_id, event)`` (wired in
Phase 6); the MCP push channel (Phase 6) subscribes on a GET and drains the queue
into an SSE stream.

Fire-and-forget, shaped like ``ChannelClient.send`` (channel/client.py) but pure
in-memory — no transport, no frames, nothing to import. A ``publish`` to a session
with no subscriber is a no-op: progress is not lost, it lives on the spool; the hub
only serves live watchers. The queue is bounded and DROPS THE OLDEST event when
full: progress is idempotent (each event is a snapshot superseding the previous), so
a slow reader loses intermediate frames, never the meaning.

No durable replay log lives here (that reads as orchestration, D22): resumability is
snapshot-baseline — a late subscriber replays the current ``progress.json`` snapshot
(Phase 5/6), then follows the live queue.
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["EventHub", "QUEUE_MAXSIZE"]

QUEUE_MAXSIZE = 256     # bounded per-subscriber buffer; drop-oldest when full


class EventHub:
    """Per-``session_id`` fan-out of progress events over bounded queues.

    Note:
        Pure in-memory state on the instance (``self._subscribers``): a session
        maps to the set of its live queues (one server may serve several SSE
        streams for the same session). No locks — every method runs on the one
        server event loop.
    """

    __slots__ = ("_subscribers",)

    def __init__(self) -> None:
        """Start with no subscribers."""
        self._subscribers: dict[str, set[asyncio.Queue[Any]]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        """Register a fresh bounded queue for ``session_id`` and return it.

        The caller (the SSE handler) drains the queue until it unsubscribes.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        """Drop ``queue`` from ``session_id`` (the SSE stream closed); tidy up empties."""
        queues = self._subscribers.get(session_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            del self._subscribers[session_id]

    def publish(self, session_id: str, event: Any) -> None:
        """Deliver ``event`` to every live queue of ``session_id`` (no-op if none).

        Full queue → drop the oldest event and enqueue the new one: progress is a
        snapshot, so the freshest event is the one that matters.
        """
        for queue in self._subscribers.get(session_id, ()):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
