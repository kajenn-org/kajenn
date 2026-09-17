"""Real-process proof that two routing relays preserve application bytes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from kajenn.channel.frame import Frame, FrameStream
from tests.core.opaque_process_fixture import EndpointValue, decode_at_endpoint, encode_at_endpoint


def free_tcp_address() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"tcp:127.0.0.1:{port}"


def addresses(kind: str, root: Path) -> list[str]:
    if kind == "tcp":
        return [free_tcp_address() for _ in range(3)]
    return [f"uds:{root / f'opaque-{index}.sock'}" for index in range(3)]


async def start_fixture(
    fixture: Path, role: str, address: str, report: Path, next_address: str | None = None
):
    command = [sys.executable, str(fixture), role, address, str(report)]
    if next_address is not None:
        command.extend(("--next", next_address))
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{Path.cwd() / 'src'}:{Path.cwd()}"
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    assert await asyncio.wait_for(process.stdout.readline(), 10) == b"READY\n"
    return process


async def connect(address: str):
    transport, _, location = address.partition(":")
    if transport == "uds":
        return await asyncio.open_unix_connection(location)
    host, _, port = location.rpartition(":")
    return await asyncio.open_connection(host, int(port))


@pytest.mark.parametrize("kind", ["uds", "tcp"])
async def test_two_spawned_relays_preserve_worker_only_tytx_and_binary(kind, tmp_path):
    fixture = Path(__file__).with_name("opaque_process_fixture.py")
    socket_root = Path(tempfile.mkdtemp(prefix="gnr-opaque-", dir="/tmp"))
    relay1_address, relay2_address, destination_address = addresses(kind, socket_root)
    reports = [tmp_path / f"report-{index}.json" for index in range(3)]
    processes = []
    try:
        processes.append(
            await start_fixture(fixture, "destination", destination_address, reports[2])
        )
        processes.append(
            await start_fixture(fixture, "relay", relay2_address, reports[1], destination_address)
        )
        processes.append(
            await start_fixture(fixture, "relay", relay1_address, reports[0], relay2_address)
        )
        binary = bytes(range(256)) + b"\x00\xffopaque\x00"
        payload = encode_at_endpoint(EndpointValue("destination-only"), binary)
        expected_hash = hashlib.sha256(payload).hexdigest()
        reader, writer = await connect(relay1_address)
        stream = FrameStream(reader, writer)
        await stream.write(
            Frame(
                id="opaque-process-proof",
                method="CALL",
                path="/echo",
                info={"format": "application/x-test-tytx-binary"},
                payload=payload,
            )
        )
        reply = await asyncio.wait_for(stream.read(), 10)
        value, returned_binary = decode_at_endpoint(reply.payload)
        assert value == EndpointValue("destination-only")
        assert returned_binary == binary
        assert hashlib.sha256(reply.payload).hexdigest() == expected_hash
        await stream.close()
        for process in reversed(processes):
            assert await asyncio.wait_for(process.wait(), 10) == 0
        relay_reports = [json.loads(path.read_text()) for path in reports[:2]]
        assert all(
            report[key] == expected_hash
            for report in relay_reports
            for key in ("request_in", "request_out", "reply_in", "reply_out")
        )
        assert all(report["endpoint_decode_calls"] == 0 for report in relay_reports)
        destination = json.loads(reports[2].read_text())
        assert destination == {
            "request_hash": expected_hash,
            "decoded_label": "destination-only",
            "binary_hex": binary.hex(),
            "endpoint_decode_calls": 1,
        }
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
                await process.wait()
        shutil.rmtree(socket_root, ignore_errors=True)
