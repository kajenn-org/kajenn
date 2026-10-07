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

"""A route called through the bus inside one process.

``server.kbus_call(path, data)`` reaches the application the first path
segment names, through the same demux and the same application call an HTTP
request takes: the handler sees its ``_request``, the avatar, the auth rules
and its ``route_cleanup``. The data crosses as JSON bytes, so nothing the
handler does to its arguments reaches the caller.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from genro_routes import route

import kajenn
from kajenn import AsgiServer, KBusMixin, RoutedApplication
from kajenn.kbus import CALL_METHOD, REPLY_METHOD, Frame, KBusCallError
from kajenn.session.avatar import Avatar


class Billing(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cleanups = 0
        self.notes: list[Any] = []

    @route()
    def total(self, order: int = 0) -> dict[str, int]:
        return {"total": order * 2}

    @route()
    def mutate(self, items: list[int] | None = None) -> dict[str, int]:
        items = items if items is not None else []
        items.append(99)
        return {"count": len(items)}

    @route()
    def seen_by(self, _request=None) -> dict[str, Any]:
        avatar = _request.avatar()
        return {
            "identity": avatar.identity if avatar is not None else None,
            "bus": _request.scope.get("kajenn.kbus"),
            "channel": _request.scope.get("kajenn.channel"),
        }

    @route(auth_rule="admin")
    def secret(self) -> dict[str, str]:
        return {"secret": "s"}

    @route()
    def note(self, text: str = "") -> None:
        self.notes.append(text)

    def route_cleanup(self) -> None:
        self.cleanups += 1


@pytest.fixture
def server() -> AsgiServer:
    return AsgiServer(applications=[(Billing, {"code": "billing"})])


def billing(server: AsgiServer) -> Billing:
    return server.applications["billing"]


def test_the_mixin_carries_the_kbus_name():
    assert kajenn.KBusMixin is KBusMixin
    assert issubclass(AsgiServer, KBusMixin)
    assert not hasattr(kajenn, "CommunicationMixin")


async def test_a_call_reaches_the_route(server):
    assert await server.kbus_call("/billing/total", {"order": 21}) == {"total": 42}


async def test_the_handler_cannot_touch_the_callers_data(server):
    items = [1, 2]
    assert await server.kbus_call("/billing/mutate", {"items": items}) == {"count": 3}
    assert items == [1, 2]


async def test_the_handler_sees_the_avatar_and_the_bus(server):
    seen = await server.kbus_call(
        "/billing/seen_by", {}, auth=Avatar("ann", ["admin"]), channel="mcp"
    )
    assert seen == {"identity": "ann", "bus": True, "channel": "mcp"}


async def test_auth_rules_apply(server):
    with pytest.raises(KBusCallError) as refused:
        await server.kbus_call("/billing/secret", {})
    assert refused.value.status == 401
    with pytest.raises(KBusCallError) as forbidden:
        await server.kbus_call("/billing/secret", {}, auth=Avatar("bob", ["user"]))
    assert forbidden.value.status == 403
    assert await server.kbus_call("/billing/secret", {}, auth=Avatar("ann", ["admin"])) == {
        "secret": "s"
    }


async def test_an_unknown_path_is_a_404(server):
    with pytest.raises(KBusCallError) as missing:
        await server.kbus_call("/billing/nothing", {})
    assert missing.value.status == 404


async def test_a_sync_handler_runs_its_cleanup(server):
    before = billing(server).cleanups
    await server.kbus_call("/billing/total", {"order": 1})
    assert billing(server).cleanups == before + 1


async def test_a_post_runs_the_route_and_returns_nothing(server):
    assert await server.kbus_post("/billing/note", {"text": "hi"}) is None
    assert billing(server).notes == ["hi"]


async def test_one_frame_in_one_frame_out(server):
    frame = Frame(
        method=CALL_METHOD,
        path="/billing/total",
        info={"format": "json"},
        payload=json.dumps({"order": 5}).encode(),
    )
    reply = await server.serve_kbus_frame(frame)
    assert reply.method == REPLY_METHOD
    assert reply.id == frame.id
    assert reply.path == frame.path
    assert reply.info["status"] == 200
    assert reply.info["format"] == "json"
    assert json.loads(reply.payload) == {"total": 10}
