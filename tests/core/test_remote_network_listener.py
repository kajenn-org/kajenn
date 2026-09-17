"""Container listeners are explicit; client destinations remain loopback-only."""

import asyncio

import pytest

from kajenn.channel.frame import Frame, FrameStream
from kajenn.remote_connection import RemoteAddress, RemoteConnection


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1", "::"])
def test_network_listener_requires_explicit_opt_in(host):
    with pytest.raises(ValueError, match="loopback"):
        RemoteAddress(f"tcp:{host}:8765")
    assert RemoteAddress(f"tcp:{host}:8765", allow_network_listener=True).host == host


async def test_listener_opt_in_does_not_enable_non_loopback_clients():
    address = RemoteAddress("tcp:0.0.0.0:8765", allow_network_listener=True)
    with pytest.raises(ValueError, match="loopback destination"):
        await address.connect()
    with pytest.raises(ValueError, match="loopback"):
        RemoteConnection("tcp:192.0.2.1:8765")


async def test_wildcard_listener_serves_a_loopback_client():
    completed = asyncio.Event()

    async def serve(reader, writer):
        stream = FrameStream(reader, writer)
        try:
            request = await stream.read()
            await stream.write(Frame(id=request.id, method="REPLY", path=request.path,
                                     payload=request.payload))
        finally:
            await stream.close()
            completed.set()

    listener = await RemoteAddress("tcp:0.0.0.0:0", allow_network_listener=True).listen(serve)
    port = listener.sockets[0].getsockname()[1]
    connection = RemoteConnection(f"tcp:127.0.0.1:{port}", timeout=2)
    try:
        result = await connection.call(Frame(method="CALL", path="/echo", payload=b"\x00\xff"))
        assert result.payload == b"\x00\xff"
        await asyncio.wait_for(completed.wait(), 2)
    finally:
        await connection.close()
        listener.close()
        await listener.wait_closed()
