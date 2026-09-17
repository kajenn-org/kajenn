"""Remote runner identity and socket ownership regression contracts."""

import asyncio
import socket
import uuid
from pathlib import Path

import pytest

from kajenn.remote_connection import RemoteAddress
from kajenn.remote_application import RemoteApplication
from kajenn import BaseServer
from tests.core.test_remote_application import connected_remote, request, tcp_address, unix_address, FACTORY


async def close_client(reader, writer):
    writer.close()
    await writer.wait_closed()


@pytest.fixture
def socket_path():
    path = Path(f"/tmp/gnr-owner-{uuid.uuid4().hex[:10]}.sock")
    yield path
    path.unlink(missing_ok=True)


@pytest.mark.parametrize("kind", ["file", "symlink", "socket"])
async def test_existing_uds_path_is_never_replaced(socket_path, tmp_path, kind):
    sock = None
    if kind == "socket":
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(socket_path))
        sock.listen()
    elif kind == "symlink":
        socket_path.symlink_to(tmp_path / "absent")
    else:
        socket_path.write_text("owned elsewhere")
    before = socket_path.lstat()
    unexpected = None
    try:
        address = RemoteAddress(f"uds:{socket_path}")
        try:
            unexpected = await address.listen(close_client)
        except OSError:
            pass
        else:
            pytest.fail("listener replaced an occupied socket path")
        after = socket_path.lstat()
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    finally:
        if unexpected:
            unexpected.close()
            await unexpected.wait_closed()
        if sock:
            sock.close()


async def test_simultaneous_uds_binds_have_exactly_one_winner(socket_path):
    addresses = [RemoteAddress(f"uds:{socket_path}") for _ in range(2)]
    results = await asyncio.gather(*(a.listen(close_client) for a in addresses), return_exceptions=True)
    servers = [r for r in results if isinstance(r, asyncio.Server)]
    try:
        assert len(servers) == 1
        assert sum(isinstance(r, OSError) for r in results) == 1
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()
        for address in addresses:
            address.unlink_owned_socket()


@pytest.mark.parametrize("address_factory", [unix_address, tcp_address])
async def test_owned_mount_refuses_foreign_runner_without_harming_it(address_factory):
    address = address_factory()
    async with connected_remote(address) as (foreign_app, foreign_process):
        owned = RemoteApplication(address=address, factory=FACTORY, code="owned", mount="demo",
                                  startup_timeout=3, shutdown_timeout=0.5)
        BaseServer(applications=[owned])
        try:
            with pytest.raises((RuntimeError, ConnectionError)):
                await owned.on_startup()
            assert not owned._started
            assert owned._process is None
            assert foreign_process.returncode is None
            # Use a NEW connection: an old open socket would hide address theft.
            await foreign_app.connection.close()
            foreign_app.connection = foreign_app._new_connection()
            status, _, _ = await request(foreign_app, "/hello")
            assert status == 200
        finally:
            await owned.on_shutdown()


async def test_listener_cleanup_removes_only_its_own_inode(socket_path):
    address = RemoteAddress(f"uds:{socket_path}")
    listener = await address.listen(close_client)
    socket_path.unlink()
    socket_path.write_text("replacement owned elsewhere")
    try:
        listener.close()
        await listener.wait_closed()
        address.unlink_owned_socket()
        assert socket_path.read_text() == "replacement owned elsewhere"
    finally:
        listener.close()
        await listener.wait_closed()


async def test_successful_listener_cleanup_allows_reuse(socket_path):
    for _ in range(2):
        address = RemoteAddress(f"uds:{socket_path}")
        listener = await address.listen(close_client)
        assert socket_path.exists()
        listener.close()
        await listener.wait_closed()
        address.unlink_owned_socket()
        assert not socket_path.exists()


async def test_failed_asyncio_listener_setup_cleans_its_bound_socket(socket_path, monkeypatch):
    async def failed_setup(*args, **kwargs):
        assert socket_path.exists()
        raise OSError("listener setup failed")

    monkeypatch.setattr(asyncio, "start_unix_server", failed_setup)
    address = RemoteAddress(f"uds:{socket_path}")
    with pytest.raises(OSError, match="listener setup failed"):
        await address.listen(close_client)
    assert not socket_path.exists()


@pytest.mark.parametrize("address_factory", [unix_address, tcp_address])
async def test_owned_mount_rechecks_identity_after_connection_loss(address_factory):
    from tests.core.test_remote_application import owned_remote

    address = address_factory()
    async with owned_remote(address) as owned:
        process = owned._process
        instance_id = owned._instance_id
        process.terminate()
        await asyncio.wait_for(process.wait(), 3)
        await owned.connection.close()
        owned.connection = owned._new_connection()
        async with connected_remote(address) as (foreign, foreign_process):
            status, _, _ = await request(owned, "/hello")
            assert status == 503
            assert owned.connection._stream is None
            assert owned.connection.expected_instance_id == instance_id
            assert foreign_process.returncode is None
            assert (await request(foreign, "/hello"))[0] == 200
