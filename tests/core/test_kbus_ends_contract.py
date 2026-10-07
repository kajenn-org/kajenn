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

"""The ends of the bus over KBusConnector: names, presentation, symmetry, bind.

Hub, client and local member are thin layers now: the hub binds and keeps the
rubric, the client connects and presents itself, the local member is two
queues. Every live link is a ``KBusConnector``, so a member's CALL is served by
the hub exactly as the hub's CALL is served by the member.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import tempfile
from typing import Any

import pytest

import kajenn
from kajenn.kbus import (
    REPLY_METHOD,
    Frame,
    KBusCallError,
    KBusClient,
    KBusConnector,
    KBusHub,
    KBusMember,
    LocalKBus,
)
from kajenn.kbus.control import ControlPayload

CONTROL = ControlPayload()

RETIRED_NAMES = (
    "KajennBusHub",
    "KajennBusClient",
    "KajennBusMember",
    "KajennBusCallError",
    "LocalKajennBus",
    "POST_METHOD",
)


def answer(frame: Frame, data: Any) -> Frame:
    return Frame(
        id=frame.id,
        method=REPLY_METHOD,
        path=frame.path,
        info={"format": "control-json"},
        payload=CONTROL.encode(data),
    )


@pytest.fixture
def socket_dir():
    path = tempfile.mkdtemp(prefix="kbusends_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


class Harness:
    def __init__(self, path: str, **kwargs: Any) -> None:
        self.joined: list[KBusMember] = []
        self.lost: list[str] = []
        self.hub = KBusHub(
            path=path,
            on_member_joined=kwargs.pop("on_member_joined", self.welcome),
            on_member_lost=lambda member: self.lost.append(member.name),
            **kwargs,
        )

    def welcome(self, member: KBusMember) -> dict[str, Any]:
        self.joined.append(member)
        return {"hello": member.name}


@pytest.fixture
async def harness(socket_dir):
    started = Harness(os.path.join(socket_dir, "hub.sock"))
    await started.hub.start()
    yield started
    await started.hub.stop()


# -- names -----------------------------------------------------------------------


def test_the_ends_carry_the_kbus_root():
    kbus = importlib.import_module("kajenn.kbus")
    for name in ("KBusHub", "KBusClient", "KBusMember", "KBusConnector", "LocalKBus",
                 "KBusCallError"):
        assert name in kbus.__all__
    assert kajenn.KBusClient is KBusClient


def test_the_retired_names_are_gone():
    kbus = importlib.import_module("kajenn.kbus")
    for name in RETIRED_NAMES:
        assert not hasattr(kbus, name)
        assert not hasattr(kajenn, name)


# -- presentation ----------------------------------------------------------------


async def test_register_is_answered_and_keeps_every_key(harness):
    client = KBusClient(harness.hub.address, "w-1", presentation={"group": "prod", "token": "t"})
    await client.connect()
    assert client.welcome == {"hello": "w-1"}
    member = harness.hub.members["w-1"]
    assert member.presentation["group"] == "prod"
    assert member.presentation["token"] == "t"
    assert member.presentation["pid"] == os.getpid()
    assert isinstance(member.connector, KBusConnector)
    await client.close()


async def test_a_refused_presentation_fails_the_connect(socket_dir):
    def refuse(member: KBusMember) -> None:
        raise PermissionError("not yours")

    refusing = Harness(os.path.join(socket_dir, "hub.sock"), on_member_joined=refuse)
    await refusing.hub.start()
    client = KBusClient(refusing.hub.address, "intruder")
    with pytest.raises(ConnectionError):
        await client.connect()
    assert "intruder" not in refusing.hub.members
    await refusing.hub.stop()


# -- symmetry --------------------------------------------------------------------


async def test_the_hub_serves_a_call_from_a_member(socket_dir):
    async def on_call(member: KBusMember, frame: Frame) -> Frame:
        return answer(frame, {"from": member.name, "path": frame.path})

    serving = Harness(os.path.join(socket_dir, "hub.sock"), on_call=on_call)
    await serving.hub.start()
    client = KBusClient(serving.hub.address, "w-2")
    await client.connect()
    assert await client.call("/up", {"n": 1}) == {"from": "w-2", "path": "/up"}
    await client.close()
    await serving.hub.stop()


async def test_the_member_serves_a_call_from_the_hub(harness):
    async def on_call(frame: Frame) -> Frame:
        return answer(frame, {"down": CONTROL.decode(frame.payload)})

    client = KBusClient(harness.hub.address, "w-3", on_call=on_call)
    await client.connect()
    assert await harness.hub.call("w-3", "/down", {"n": 2}) == {"down": {"n": 2}}
    await client.close()


async def test_a_call_nobody_serves_raises_the_call_error(harness):
    client = KBusClient(harness.hub.address, "w-4")
    await client.connect()
    with pytest.raises(KBusCallError):
        await client.call("/nobody", {})
    await client.close()


async def test_a_local_member_joins_through_one_call(harness):
    local = LocalKBus("in-process", presentation={"group": "exp"})
    member = await harness.hub.attach_local(local)
    assert member is harness.hub.members["in-process"]
    assert local.welcome == {"hello": "in-process"}
    assert member.presentation["group"] == "exp"
    await local.close()


# -- end of the link -------------------------------------------------------------


async def test_hub_stop_orphans_the_client(harness):
    orphans: list[KBusClient] = []
    client = KBusClient(harness.hub.address, "w-5", on_orphan=orphans.append)
    await client.connect()
    await harness.hub.stop()
    await asyncio.wait_for(client.wait_closed(), 2)
    assert orphans == [client]


async def test_member_close_is_a_member_loss(harness):
    client = KBusClient(harness.hub.address, "w-6")
    await client.connect()
    await client.close()
    deadline = asyncio.get_running_loop().time() + 2
    while "w-6" not in harness.lost:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)
    assert "w-6" not in harness.hub.members


# -- bind ------------------------------------------------------------------------


async def test_the_hub_never_steals_an_existing_path(socket_dir):
    path = os.path.join(socket_dir, "taken.sock")
    with open(path, "w") as taken:
        taken.write("someone else's")
    hub = KBusHub(path=path)
    with pytest.raises(FileExistsError):
        await hub.start()
    with open(path) as still_there:
        assert still_there.read() == "someone else's"


async def test_stop_removes_only_its_own_socket(socket_dir):
    path = os.path.join(socket_dir, "own.sock")
    hub = KBusHub(path=path)
    await hub.start()
    os.unlink(path)
    with open(path, "w") as replaced:
        replaced.write("new owner")
    await hub.stop()
    assert os.path.exists(path)
