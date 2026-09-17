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

"""Tests for EventHub (core 1e Phase 3): in-memory per-session event fan-out.

Real objects, no mocks: a live ``EventHub`` and real ``asyncio.Queue``s. The
tests are ``async def`` so subscriptions bind to a running event loop, matching
the hub's runtime (one server event loop).
"""

from __future__ import annotations

from kajenn.tasks import EventHub
from kajenn.tasks.hub import QUEUE_MAXSIZE


class TestSubscribePublish:
    """A subscriber receives every event published to its session."""

    async def test_publish_reaches_subscriber(self) -> None:
        hub = EventHub()
        queue = hub.subscribe("s1")
        hub.publish("s1", {"type": "progress", "value": 1})
        assert (await queue.get()) == {"type": "progress", "value": 1}

    async def test_two_subscribers_same_session_both_receive(self) -> None:
        hub = EventHub()
        q1 = hub.subscribe("s1")
        q2 = hub.subscribe("s1")
        hub.publish("s1", {"n": 7})
        assert (await q1.get()) == {"n": 7}
        assert (await q2.get()) == {"n": 7}

    async def test_publish_isolated_per_session(self) -> None:
        hub = EventHub()
        q1 = hub.subscribe("s1")
        hub.subscribe("s2")
        hub.publish("s1", {"only": "s1"})
        assert (await q1.get()) == {"only": "s1"}
        assert hub.subscribe("s2").empty()      # a fresh s2 queue saw nothing


class TestNoSubscriber:
    """Publishing to a session nobody watches is a silent no-op."""

    async def test_publish_without_subscriber_is_noop(self) -> None:
        hub = EventHub()
        hub.publish("ghost", {"lost": True})        # must not raise
        queue = hub.subscribe("ghost")
        assert queue.empty()                         # the earlier event is not replayed


class TestBackpressure:
    """A full queue drops the OLDEST event (progress is a snapshot)."""

    async def test_full_queue_drops_oldest(self) -> None:
        hub = EventHub()
        queue = hub.subscribe("s1")
        for i in range(QUEUE_MAXSIZE + 3):
            hub.publish("s1", {"i": i})
        assert queue.qsize() == QUEUE_MAXSIZE
        first = await queue.get()
        assert first == {"i": 3}                     # 0,1,2 dropped as oldest


class TestUnsubscribe:
    """Unsubscribing removes the queue and cleans up the empty session."""

    async def test_unsubscribe_stops_delivery(self) -> None:
        hub = EventHub()
        queue = hub.subscribe("s1")
        hub.unsubscribe("s1", queue)
        hub.publish("s1", {"after": "unsub"})
        assert queue.empty()

    async def test_unsubscribe_unknown_session_is_noop(self) -> None:
        hub = EventHub()
        queue = hub.subscribe("s1")
        hub.unsubscribe("nope", queue)              # must not raise

    async def test_last_unsubscribe_drops_the_session(self) -> None:
        hub = EventHub()
        queue = hub.subscribe("s1")
        hub.unsubscribe("s1", queue)
        # a fresh subscribe rebuilds a distinct empty queue for the session
        again = hub.subscribe("s1")
        assert again is not queue
        assert again.empty()
