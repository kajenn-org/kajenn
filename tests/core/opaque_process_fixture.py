"""Spawned destination/relay fixture for the opaque transport process proof."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from kajenn.channel.frame import Frame, FrameStream


@dataclass(frozen=True)
class EndpointValue:
    label: str


ENDPOINT_DECODE_CALLS = 0


def encode_at_endpoint(value: EndpointValue, binary: bytes) -> bytes:
    from genro_tytx import register_type, to_tytx

    register_type(EndpointValue, "OQ", lambda item: item.label, EndpointValue)
    text = to_tytx(value, "json").encode()
    return struct.pack("!I", len(text)) + text + binary


def decode_at_endpoint(payload: bytes) -> tuple[EndpointValue, bytes]:
    global ENDPOINT_DECODE_CALLS
    from genro_tytx import from_tytx, register_type

    ENDPOINT_DECODE_CALLS += 1
    register_type(EndpointValue, "OQ", lambda item: item.label, EndpointValue)
    text_length = struct.unpack("!I", payload[:4])[0]
    value = from_tytx(payload[4 : 4 + text_length].decode(), "json")
    return value, payload[4 + text_length :]


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


async def open_address(address: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    transport, _, location = address.partition(":")
    if transport == "uds":
        return await asyncio.open_unix_connection(location)
    host, _, port = location.rpartition(":")
    return await asyncio.open_connection(host, int(port))


async def listen(address: str, callback) -> asyncio.Server:
    transport, _, location = address.partition(":")
    if transport == "uds":
        return await asyncio.start_unix_server(callback, path=location)
    host, _, port = location.rpartition(":")
    return await asyncio.start_server(callback, host, int(port))


async def run_destination(listen_address: str, report_path: Path) -> None:
    done = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = FrameStream(reader, writer)
        frame = await stream.read()
        value, binary = decode_at_endpoint(frame.payload)
        report_path.write_text(
            json.dumps(
                {
                    "request_hash": digest(frame.payload),
                    "decoded_label": value.label,
                    "binary_hex": binary.hex(),
                    "endpoint_decode_calls": ENDPOINT_DECODE_CALLS,
                }
            )
        )
        await stream.write(
            Frame(
                id=frame.id,
                method="REPLY",
                path=frame.path,
                info=frame.info,
                payload=frame.payload,
            )
        )
        await stream.close()
        done.set()

    server = await listen(listen_address, serve)
    print("READY", flush=True)
    await done.wait()
    server.close()
    await server.wait_closed()


async def run_relay(listen_address: str, next_address: str, report_path: Path) -> None:
    done = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        inbound = FrameStream(reader, writer)
        request = await inbound.read()
        next_reader, next_writer = await open_address(next_address)
        outbound = FrameStream(next_reader, next_writer)
        before = digest(request.payload)
        await outbound.write(request)
        reply = await outbound.read()
        after = digest(request.payload)
        reply_before = digest(reply.payload)
        await inbound.write(reply)
        report_path.write_text(
            json.dumps(
                {
                    "request_in": before,
                    "request_out": after,
                    "reply_in": reply_before,
                    "reply_out": digest(reply.payload),
                    "endpoint_decode_calls": ENDPOINT_DECODE_CALLS,
                }
            )
        )
        await outbound.close()
        await inbound.close()
        done.set()

    server = await listen(listen_address, serve)
    print("READY", flush=True)
    await done.wait()
    server.close()
    await server.wait_closed()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("relay", "destination"))
    parser.add_argument("listen")
    parser.add_argument("report", type=Path)
    parser.add_argument("--next")
    args = parser.parse_args()
    if args.role == "destination":
        await run_destination(args.listen, args.report)
    else:
        await run_relay(args.listen, args.next, args.report)


if __name__ == "__main__":
    asyncio.run(main())
