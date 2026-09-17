# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Process-wide buffered transport policy, read when codecs are constructed.

Configure every peer identically before startup. Spawned workers inherit the
parent environment; independently deployed peers need the same settings.
Values are bytes, except the warning interval (seconds). No capacity is reserved.
"""

import os

DEFAULT_MAX_FRAME_SIZE = 256 * 1024 * 1024
DEFAULT_WARN_FRAME_SIZE = 1024 * 1024


def integer_setting(name: str, default: int, *, minimum: int = 0) -> int:
    value = os.environ.get(name)
    try:
        result = default if value is None else int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def frame_max_size() -> int:
    # Each section length is an unsigned 32-bit integer on the wire.
    value = integer_setting("GNR_ASGI_FRAME_MAX_BYTES", DEFAULT_MAX_FRAME_SIZE, minimum=1)
    if value > 2**32 - 1:
        raise ValueError("GNR_ASGI_FRAME_MAX_BYTES must fit an unsigned 32-bit integer")
    return value


def http_max_body_size() -> int:
    return integer_setting("GNR_ASGI_HTTP_MAX_BODY_BYTES", frame_max_size())


class FrameTooLarge(ValueError):
    """The complete frame exceeds policy; encoding rejects before any write."""

    def __init__(self, size: int, maximum: int) -> None:
        self.size = size
        self.maximum = maximum
        super().__init__(f"frame of {size} bytes exceeds max_size={maximum}")


class HttpBodyTooLarge(ValueError):
    """A buffered HTTP body exceeds the configured endpoint policy."""
