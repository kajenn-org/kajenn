"""Bounded process-boundary benchmark for the opaque Frame/HTTP record transport."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

from kajenn.channel.frame import Frame, FrameStream
from kajenn.http_record import HttpRecord
from kajenn.remote_connection import RemoteConnection


def request_payload(body: bytes) -> bytes:
    return HttpRecord(max_body_size=2 * 1024 * 1024).encode_request(
        {
            "method": "POST",
            "path": "/echo",
            "raw_path": b"/echo",
            "root_path": "/demo",
            "query_string": b"sample=1",
            "headers": [(b"content-type", b"application/octet-stream")],
            "scheme": "http",
            "server": ("127.0.0.1", 8764),
            "client": ("127.0.0.1", 54321),
            "http_version": "1.1",
        },
        body,
    )


def response_payload(body: bytes) -> bytes:
    return HttpRecord(max_body_size=2 * 1024 * 1024).encode_response(
        {"status": 200, "headers": [("content-type", "application/octet-stream")], "body": body}
    )


def reference_json(body: bytes) -> bytes:
    """A serialization-only size reference: equivalent metadata and a base64 body."""
    return json.dumps(
        {
            "id": "benchmark",
            "method": "CALL",
            "path": "/echo",
            "format": "http",
            "http": {
                "method": "POST",
                "path": "/echo",
                "raw_path": "/echo",
                "root_path": "/demo",
                "query_string": "sample=1",
                "headers": [["content-type", "application/octet-stream"]],
                "scheme": "http",
                "server": ["127.0.0.1", 8764],
                "client": ["127.0.0.1", 54321],
                "http_version": "1.1",
                "body": base64.b64encode(body).decode("ascii"),
            },
        },
        separators=(",", ":"),
    ).encode()


async def open_listener(address: str, callback) -> asyncio.Server:
    if address.startswith("uds:"):
        return await asyncio.start_unix_server(callback, path=address.removeprefix("uds:"))
    host, _, port = address.removeprefix("tcp:").rpartition(":")
    return await asyncio.start_server(callback, host, int(port))


async def serve(address: str, expected: int) -> None:
    completed = 0
    done = asyncio.Event()
    cpu_start = time.process_time()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal completed
        stream = FrameStream(reader, writer)
        while (frame := await stream.read()) is not None:
            _, body = HttpRecord(max_body_size=2 * 1024 * 1024).decode_request(frame.payload)
            await stream.write(
                Frame(
                    id=frame.id,
                    method="REPLY",
                    path=frame.path,
                    info={"format": "http"},
                    payload=response_payload(body),
                )
            )
            completed += 1
            if completed == expected:
                done.set()
        await stream.close()

    server = await open_listener(address, handle)
    print("READY", flush=True)
    await done.wait()
    server.close()
    await server.wait_closed()
    print(
        json.dumps({"server_process_cpu_total_seconds": time.process_time() - cpu_start}),
        flush=True,
    )


def free_tcp_address() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp:127.0.0.1:{probe.getsockname()[1]}"


async def benchmark_transport(
    kind: str, cases: list[tuple[str, int, int]], warmup: int
) -> list[dict]:
    address = (
        free_tcp_address()
        if kind == "tcp"
        else f"uds:{tempfile.mktemp(prefix='gnrbench-', suffix='.sock', dir='/tmp')}"
    )
    expected = sum(iterations for _, _, iterations in cases) + warmup * len(cases)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve()),
        "--server",
        address,
        "--expected",
        str(expected),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": f"{Path.cwd() / 'src'}:{Path.cwd()}"},
    )
    ready = await asyncio.wait_for(process.stdout.readline(), 10)
    if ready != b"READY\n":
        raise RuntimeError((await process.stderr.read()).decode())
    connection = RemoteConnection(address, timeout=10, max_calls=1)
    results = []
    for name, size, iterations in cases:
        body = bytes((index % 251 for index in range(size)))
        payload = request_payload(body)
        frame = Frame(
            id=f"benchmark-{name}",
            method="CALL",
            path="/echo",
            info={"format": "http"},
            payload=payload,
        )
        for _ in range(warmup):
            await connection.call(frame)
        latencies = []
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        for _ in range(iterations):
            started = time.perf_counter_ns()
            reply = await connection.call(frame)
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
            decoded = HttpRecord(max_body_size=2 * 1024 * 1024).decode_response(reply.payload)
            if decoded["body"] != body:
                raise RuntimeError("echo changed the body")
        client_cpu = time.process_time() - cpu_start
        wall = time.perf_counter() - wall_start
        response = Frame(
            id=frame.id,
            method="REPLY",
            path=frame.path,
            info={"format": "http"},
            payload=response_payload(body),
        )
        reference_start = time.process_time()
        for _ in range(iterations):
            reference = reference_json(body)
        reference_cpu = time.process_time() - reference_start
        sorted_latency = sorted(latencies)
        wire_per_roundtrip = len(frame.encode()) + len(response.encode())
        results.append(
            {
                "transport": kind,
                "case": name,
                "body_bytes": size,
                "iterations": iterations,
                "request_wire_bytes": len(frame.encode()),
                "roundtrip_wire_bytes": wire_per_roundtrip,
                "base64_json_reference_bytes": len(reference) + 4,
                "median_ms": statistics.median(latencies),
                "p95_ms": sorted_latency[max(0, int(len(sorted_latency) * 0.95) - 1)],
                "requests_per_second": iterations / wall,
                "body_roundtrip_mib_per_second": (2 * size * iterations) / wall / (1024 * 1024),
                "wire_mib_per_second": wire_per_roundtrip * iterations / wall / (1024 * 1024),
                "client_cpu_seconds": client_cpu,
                "reference_encode_cpu_seconds": reference_cpu,
            }
        )
    await connection.close()
    server_stats = json.loads((await asyncio.wait_for(process.stdout.readline(), 10)).decode())
    if await asyncio.wait_for(process.wait(), 10) != 0:
        raise RuntimeError((await process.stderr.read()).decode())
    for result in results:
        result.update(server_stats)
    if kind == "uds":
        Path(address.removeprefix("uds:")).unlink(missing_ok=True)
    return results


async def run(args: argparse.Namespace) -> None:
    cases = [
        ("small", args.small_bytes, args.small_iterations),
        ("1mib", 1024 * 1024, args.large_iterations),
    ]
    results = []
    for kind in ("uds", "tcp"):
        results.extend(await benchmark_transport(kind, cases, args.warmup))
    print(
        json.dumps(
            {"python": sys.version.split()[0], "pid": os.getpid(), "results": results}, indent=2
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server")
    parser.add_argument("--expected", type=int)
    parser.add_argument("--small-bytes", type=int, default=1024)
    parser.add_argument("--small-iterations", type=int, default=300)
    parser.add_argument("--large-iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args()
    if args.server:
        asyncio.run(serve(args.server, args.expected))
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
