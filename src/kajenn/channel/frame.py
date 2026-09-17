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
"""Versioned channel frames with bounded JSON routing information and opaque bytes.

The frame layer never interprets payload bytes. Socket and in-process streams
share :class:`FrameCodec`, including strict version, length and JSON checks.
"""

from __future__ import annotations
import asyncio
import copy
import json
import math
import logging
import time
import struct
import uuid
from typing import Any

from ..transport_limits import (
    DEFAULT_MAX_FRAME_SIZE, DEFAULT_WARN_FRAME_SIZE, FrameTooLarge,
    frame_max_size, integer_setting,
)

CHANNEL_MAGIC = b"GNRF"
CHANNEL_VERSION = 1
HEADER = struct.Struct("!4sBII")
HEADER_SIZE = HEADER.size
MAX_FRAME_SIZE = DEFAULT_MAX_FRAME_SIZE
_logger = logging.getLogger(__name__)
REGISTER_METHOD = "REGISTER"
REGISTER_PATH = "/register"
RESERVED_INFO_KEYS = frozenset({"id", "method", "path"})
MAX_ROUTING_STRING = 4096
ALLOWED_METHODS = frozenset({"REGISTER", "POST", "CALL", "REPLY", "EVENT"})
__all__ = [
    "CHANNEL_MAGIC",
    "CHANNEL_VERSION",
    "HEADER_SIZE",
    "MAX_FRAME_SIZE",
    "FrameTooLarge",
    "REGISTER_METHOD",
    "REGISTER_PATH",
    "Frame",
    "FrameCodec",
    "FrameStream",
]


class Frame:
    """An immutable routing record and opaque byte payload."""

    __slots__ = ("_id", "_method", "_path", "_info", "_payload")

    def __init__(
        self,
        *,
        id: str | None = None,
        method: str = "POST",
        path: str = "/",
        info: dict[str, Any] | None = None,
        payload: bytes = b"",
    ) -> None:
        codec = FrameCodec()
        self._id = codec.validate_routing_string("id", str(uuid.uuid4()) if id is None else id)
        self._method = codec.validate_routing_string("method", method)
        if self._method not in ALLOWED_METHODS:
            raise ValueError(f"unsupported frame method {self._method!r}")
        self._path = codec.validate_routing_string("path", path)
        if info is None:
            info = {}
        if not isinstance(info, dict):
            raise TypeError("frame info must be a JSON object")
        collision = RESERVED_INFO_KEYS.intersection(info)
        if collision:
            raise ValueError(f"frame info contains reserved keys: {', '.join(sorted(collision))}")
        try:
            codec.validate_json(info)
        except RecursionError as exc:
            raise ValueError("frame info nesting exceeds decoder capacity") from exc
        if not isinstance(payload, bytes):
            raise TypeError("frame payload must be bytes")
        self._info = copy.deepcopy(info)
        self._payload = payload

    @property
    def id(self) -> str:
        return self._id

    @property
    def method(self) -> str:
        return self._method

    @property
    def path(self) -> str:
        return self._path

    @property
    def info(self) -> dict[str, Any]:
        return copy.deepcopy(self._info)

    @property
    def payload(self) -> bytes:
        return self._payload

    def encode(self) -> bytes:
        return FrameCodec().encode(self)

    def __repr__(self) -> str:
        return f"<Frame {self.method} {self.path} id={self.id}>"


class FrameCodec:
    """Encode, decode and validate one version of the frame protocol."""

    def __init__(
        self, *, max_size: int | None = None, warn_size: int | None = None,
        warning_interval: int | None = None,
    ) -> None:
        self.max_size = frame_max_size() if max_size is None else max_size
        self.warn_size = (integer_setting("GNR_ASGI_FRAME_WARN_BYTES", DEFAULT_WARN_FRAME_SIZE)
                          if warn_size is None else warn_size)
        self.warning_interval = (integer_setting("GNR_ASGI_FRAME_WARN_INTERVAL_SECONDS", 60)
                                 if warning_interval is None else warning_interval)
        for name, value, minimum in (("max_size", self.max_size, 1),
                                     ("warn_size", self.warn_size, 0),
                                     ("warning_interval", self.warning_interval, 0)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.max_size > 2**32 - 1:
            raise ValueError("max_size must fit an unsigned 32-bit integer")
        self._last_warning: float | None = None

    def warn_large_frame(self, frame: Frame, size: int, direction: str) -> None:
        if not self.warn_size or size <= self.warn_size:
            return
        now = time.monotonic()
        if self._last_warning is not None and now - self._last_warning < self.warning_interval:
            return
        self._last_warning = now
        snapshot = frame.info.get("worker_snapshot")
        worker = snapshot.get("name") if isinstance(snapshot, dict) else None
        _logger.warning(
            "Large transport frame: bytes=%s threshold=%s direction=%s method=%s path=%s worker=%s",
            size, self.warn_size, direction, frame.method, frame.path, worker,
        )

    def reject_constant(self, value: str) -> None:
        raise ValueError(f"non-finite JSON number {value!r}")

    def object_pairs(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def validate_json(self, value: Any) -> None:
        if value is None or isinstance(value, (str, bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("non-finite numbers are not valid frame info")
            return
        if isinstance(value, list):
            for item in value:
                self.validate_json(item)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("frame info keys must be strings")
                self.validate_json(item)
            return
        raise TypeError(f"frame info value {type(value).__name__} is not JSON-compatible")

    def validate_routing_string(self, name: str, value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_ROUTING_STRING:
            raise ValueError(
                f"frame {name} must be a nonempty string of at most {MAX_ROUTING_STRING} characters"
            )
        return value

    def get_header_lengths(self, header: bytes) -> tuple[int, int]:
        if len(header) != HEADER_SIZE:
            raise ValueError("truncated frame header")
        magic, version, info_length, payload_length = HEADER.unpack(header)
        if magic != CHANNEL_MAGIC:
            raise ValueError("invalid frame magic")
        if version != CHANNEL_VERSION:
            raise ValueError(f"unsupported frame version {version}")
        if info_length + payload_length > self.max_size:
            raise FrameTooLarge(info_length + payload_length, self.max_size)
        return info_length, payload_length

    def encode(self, frame: Frame) -> bytes:
        record = {"id": frame.id, "method": frame.method, "path": frame.path, **frame.info}
        self.validate_json(record)
        try:
            info = json.dumps(record, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(f"invalid frame info: {exc}") from exc
        size = len(info) + len(frame.payload)
        if size > self.max_size:
            raise FrameTooLarge(size, self.max_size)
        header = HEADER.pack(CHANNEL_MAGIC, CHANNEL_VERSION, len(info), len(frame.payload))
        self.warn_large_frame(frame, size, "send")
        return header + info + frame.payload

    def get_frame(self, wire: bytes) -> Frame:
        if len(wire) < HEADER_SIZE:
            raise ValueError("truncated frame header")
        ilength, plength = self.get_header_lengths(wire[:HEADER_SIZE])
        expected = HEADER_SIZE + ilength + plength
        if len(wire) != expected:
            raise ValueError(
                f"truncated or overlong frame: expected {expected} bytes, got {len(wire)}"
            )
        try:
            record = json.loads(
                wire[HEADER_SIZE : HEADER_SIZE + ilength],
                object_pairs_hook=self.object_pairs,
                parse_constant=self.reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ValueError(f"invalid frame info JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError("frame info must be a JSON object")
        try:
            frame_id = record.pop("id")
            method = record.pop("method")
            path = record.pop("path")
        except KeyError as exc:
            raise ValueError(f"frame info missing {exc.args[0]!r}") from exc
        frame = Frame(
            id=frame_id,
            method=method,
            path=path,
            info=record,
            payload=wire[HEADER_SIZE + ilength :],
        )
        self.warn_large_frame(frame, ilength + plength, "receive")
        return frame


class FrameStream:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_size: int | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.codec = FrameCodec(max_size=max_size)
        self.max_size = self.codec.max_size
        self._write_lock = asyncio.Lock()

    async def read(self) -> Frame | None:
        try:
            header = await self.reader.readexactly(HEADER_SIZE)
        except ConnectionResetError:
            return None
        except asyncio.IncompleteReadError as exc:
            if not exc.partial:
                return None
            raise ValueError("truncated frame header") from exc
        ilength, plength = self.codec.get_header_lengths(header)
        try:
            body = await self.reader.readexactly(ilength + plength)
        except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
            raise ValueError("truncated frame body") from exc
        return self.codec.get_frame(header + body)

    async def write(self, frame: Frame) -> None:
        wire = self.codec.encode(frame)
        async with self._write_lock:
            self.writer.write(wire)
            await self.writer.drain()

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
