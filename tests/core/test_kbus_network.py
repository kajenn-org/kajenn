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

"""The hub's address in the configuration, the network listener and its secret,
and the subprocess spawner's relaunch."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import stat
import tempfile
import time
from pathlib import Path

import pytest

from kajenn import AsgiServer
from kajenn.kbus import KBusClient, KBusHub

from .test_kbus_external_application import RECIPE, get, no_process, running  # noqa: F401


def write_recipe(tmp_path, kbus: str | None) -> str:
    text = RECIPE.replace("SPAWNER", 'spawner="subprocess"')
    if kbus is not None:
        text = text.replace(
            '    site_name = "billingsite"\n',
            f'    site_name = "billingsite"\n\n    def server_section(self, cfg):\n'
            f'        cfg.server().kbus({kbus})\n',
        )
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


async def joined(server: AsgiServer, previous: object = None):
    deadline = time.monotonic() + 30
    while True:
        member = server.children_kbus.resolve("billing")
        if member is not None and member is not previous:
            return member
        assert time.monotonic() < deadline, "the process never registered"
        await asyncio.sleep(0.05)


async def test_the_hub_address_is_configured(tmp_path, no_process):
    # wf:contract: server.kbus(address=...) in the configuration sets the hub's address;
    # wf:contract: without the element the hub binds a uds socket in a private directory.
    directory = tempfile.mkdtemp(prefix="kb")
    socket_path = f"{directory}/hub.sock"
    try:
        configured = write_recipe(tmp_path, f'address="uds:{socket_path}"')
        async with running(configured, wait_member=False) as server:
            assert server.children_kbus.address == f"uds:{socket_path}"
        (tmp_path / "plain").mkdir()
        async with running(write_recipe(tmp_path / "plain", None), wait_member=False) as server:
            private = Path(server.children_kbus.address.removeprefix("uds:")).parent
            assert stat.S_IMODE(private.stat().st_mode) == 0o700
    finally:
        shutil.rmtree(directory)


def test_a_network_listener_without_secret_is_a_boot_error(tmp_path):
    # wf:contract: server.kbus(address="tcp:<non-loopback ip>:<port>") without secret is
    # wf:contract: refused at configuration time with an error naming the missing secret.
    with pytest.raises(ValueError, match="secret"):
        AsgiServer(config=write_recipe(tmp_path, 'address="tcp:0.0.0.0:0"'))


async def test_a_network_register_without_the_secret_is_refused():
    # wf:contract: on a hub listening on a non-loopback address, a REGISTER without the
    # wf:contract: secret, or with another one, is refused and the client's connect fails;
    # wf:contract: with the configured secret the member joins.
    hub = KBusHub(host="0.0.0.0", secret="s3cret")
    await hub.start()
    try:
        port = hub.address.rpartition(":")[2]
        with pytest.raises(ConnectionError):
            await KBusClient(f"tcp:127.0.0.1:{port}", "anonymous").connect()
        with pytest.raises(ConnectionError):
            await KBusClient(hub.address, "intruder", secret="other").connect()
        member = KBusClient(hub.address, "member", secret="s3cret")
        await member.connect()
        assert hub.resolve("member") is not None
        assert "secret" not in hub.resolve("member").presentation
        await member.close()
    finally:
        await hub.stop()


async def test_uds_and_loopback_need_no_secret():
    # wf:contract: a hub on uds or on tcp loopback admits a REGISTER without any secret.
    for hub in (KBusHub(), KBusHub(host="127.0.0.1")):
        await hub.start()
        try:
            client = KBusClient(hub.address, "member")
            await client.connect()
            assert hub.resolve("member") is not None
            await client.close()
        finally:
            await hub.stop()


async def test_an_external_application_works_over_the_network_listener(tmp_path):
    # wf:contract: an application declared with spawner="subprocess" on a server whose hub
    # wf:contract: listens on tcp with a secret answers its mount exactly as over uds; the
    # wf:contract: spawner hands the process the address and the secret.
    config = write_recipe(tmp_path, 'address="tcp:0.0.0.0:0", secret="s3cret"')
    async with running(config) as server:
        assert server.children_kbus.address.startswith("tcp:0.0.0.0:")
        assert await get(server, "/billing/total", b"order=2") == (200, {"total": 4})


async def test_a_lost_spawned_member_is_relaunched(tmp_path):
    # wf:contract: when the process the subprocess spawner started dies, the member loss
    # wf:contract: makes the spawner ensure the role again: a new process with a new token
    # wf:contract: registers and the mount answers again without a server restart.
    async with running(write_recipe(tmp_path, None)) as server:
        first = server.children_kbus.resolve("billing")
        _, state = await get(server, "/billing/prepared_state")
        os.kill(state["pid"], signal.SIGKILL)
        second = await joined(server, first)
        assert second.presentation["token"] != first.presentation["token"]
        status, again = await get(server, "/billing/prepared_state")
    assert status == 200
    assert again["pid"] not in (state["pid"], os.getpid())
