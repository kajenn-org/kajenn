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

"""The websocket registry: live sockets, and which one a page speaks on (#68 phase 2).

Contract tests. ``WebSocketRegistry`` is the server's picture of what is
connected: sockets in at the accept and out in the ``finally``, and the
``page_id → socket`` association that ``openchannel`` writes so the server can
address one page.

It is NEUTRAL: it does not know the SPA, and it validates nothing. Whether a
page belongs to the connection asking for it is judged by the SPA's own branch,
which holds the commander — the registry only remembers.
"""

from __future__ import annotations

from kajenn.types import Message
from kajenn.websocket import WebSocket, WebSocketRegistry


def socket_named(name: str) -> WebSocket:
    """A facade with nothing behind it: the registry only ever holds it."""

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        return None

    return WebSocket({"type": "websocket", "path": f"/{name}", "headers": []}, receive, send)


class TestTheLiveSockets:
    def test_a_registered_socket_is_in_the_snapshot(self) -> None:
        registry = WebSocketRegistry()
        socket = socket_named("a")
        registry.register(socket)
        assert registry.snapshot() == [socket]

    def test_unregistering_takes_it_out(self) -> None:
        registry = WebSocketRegistry()
        socket = socket_named("a")
        registry.register(socket)
        registry.unregister(socket)
        assert registry.snapshot() == []

    def test_unregistering_one_that_never_arrived_is_silent(self) -> None:
        # The `finally` of a handshake that failed before the accept calls it.
        WebSocketRegistry().unregister(socket_named("a"))

    def test_two_sockets_of_the_same_browser_are_two_entries(self) -> None:
        registry = WebSocketRegistry()
        first, second = socket_named("a"), socket_named("b")
        registry.register(first)
        registry.register(second)
        assert registry.snapshot() == [first, second]


class TestThePageOfASocket:
    def test_a_bound_page_reads_back_its_socket(self) -> None:
        registry = WebSocketRegistry()
        socket = socket_named("a")
        registry.register(socket)
        registry.bind_page("p1", socket)
        assert registry.get_page_socket("p1") is socket

    def test_an_unknown_page_reads_none(self) -> None:
        assert WebSocketRegistry().get_page_socket("p1") is None

    def test_binding_the_same_page_to_the_same_socket_twice_is_idempotent(self) -> None:
        registry = WebSocketRegistry()
        socket = socket_named("a")
        registry.register(socket)
        registry.bind_page("p1", socket)
        registry.bind_page("p1", socket)
        assert registry.get_page_socket("p1") is socket

    def test_a_reconnection_replaces_the_socket_without_an_error(self) -> None:
        # The browser lost its socket and opened a new one: the same page says
        # openchannel again, and the association follows it.
        registry = WebSocketRegistry()
        old, new = socket_named("old"), socket_named("new")
        registry.register(old)
        registry.register(new)
        registry.bind_page("p1", old)
        registry.bind_page("p1", new)
        assert registry.get_page_socket("p1") is new

    def test_one_socket_carries_many_pages(self) -> None:
        # A root page and the frames under it share the connection.
        registry = WebSocketRegistry()
        socket = socket_named("a")
        registry.register(socket)
        registry.bind_page("root", socket)
        registry.bind_page("frame", socket)
        assert registry.get_page_socket("root") is registry.get_page_socket("frame") is socket


class TestWhatDiesWithASocket:
    def test_unregistering_drops_the_pages_bound_to_it(self) -> None:
        registry = WebSocketRegistry()
        socket = socket_named("a")
        registry.register(socket)
        registry.bind_page("p1", socket)
        registry.unregister(socket)
        assert registry.get_page_socket("p1") is None

    def test_it_leaves_alone_the_pages_bound_to_another_socket(self) -> None:
        # The old socket of a reconnected page closes AFTER the new one bound
        # it: the purge must not take the live association with it.
        registry = WebSocketRegistry()
        old, new = socket_named("old"), socket_named("new")
        registry.register(old)
        registry.register(new)
        registry.bind_page("p1", old)
        registry.bind_page("p1", new)
        registry.unregister(old)
        assert registry.get_page_socket("p1") is new

    def test_the_comparison_is_on_the_socket_itself(self) -> None:
        # Two facades over the same kind of scope are two connections.
        registry = WebSocketRegistry()
        first, second = socket_named("a"), socket_named("a")
        registry.register(first)
        registry.register(second)
        registry.bind_page("p1", first)
        registry.unregister(second)
        assert registry.get_page_socket("p1") is first
