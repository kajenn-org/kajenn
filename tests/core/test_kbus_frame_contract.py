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

"""Frame layer contract: one home for the methods, no orchestration knowledge,
one stream protocol, one callback runner."""

from __future__ import annotations

import asyncio
import logging

import pytest

from kajenn.kbus.callback import run_callback
from kajenn.kbus.frame import (
    ALLOWED_METHODS,
    CALL_METHOD,
    EVENT_METHOD,
    REGISTER_METHOD,
    REPLY_METHOD,
    Frame,
    FrameCodec,
    FrameStreamProtocol,
)
from kajenn.kbus.local import LocalFrameStream


def test_the_four_methods_live_in_frame():
    assert (REGISTER_METHOD, CALL_METHOD, REPLY_METHOD, EVENT_METHOD) == (
        "REGISTER",
        "CALL",
        "REPLY",
        "EVENT",
    )
    assert ALLOWED_METHODS == frozenset({"REGISTER", "CALL", "REPLY", "EVENT"})


def test_post_is_not_a_frame_method():
    with pytest.raises(ValueError):
        Frame(method="POST")


def test_the_default_method_is_an_event():
    assert Frame().method == EVENT_METHOD


def test_large_frame_warning_reads_no_orchestration_field(caplog):
    codec = FrameCodec(warn_size=1, warning_interval=0)
    frame = Frame(
        method=CALL_METHOD,
        path="/x",
        info={"worker_snapshot": {"name": "w1"}},
        payload=b"abc",
    )
    with caplog.at_level(logging.WARNING, logger="kajenn.kbus.frame"):
        codec.encode(frame)
    assert "Large transport frame" in caplog.text
    assert "worker" not in caplog.text


def test_both_streams_satisfy_the_stream_protocol():
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    outbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    assert isinstance(LocalFrameStream(inbound, outbound), FrameStreamProtocol)


async def test_run_callback_runs_sync_and_async_and_logs_failures(caplog):
    logger = logging.getLogger("kajenn.kbus.test")
    seen: list[int] = []

    async def asynchronous(value: int) -> None:
        seen.append(value)

    def failing(value: int) -> None:
        raise RuntimeError(f"bad {value}")

    await run_callback(seen.append, 1, logger=logger)
    await run_callback(asynchronous, 2, logger=logger)
    with caplog.at_level(logging.ERROR, logger="kajenn.kbus.test"):
        await run_callback(failing, 3, logger=logger)
    await run_callback(None, 4, logger=logger)
    assert seen == [1, 2]
    assert "bad 3" in caplog.text
