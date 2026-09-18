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

"""Contract: a remote runner turns an oversized result into a reply error and keeps serving.

Drives ``kajenn.remote_runner`` against ``examples/remote_openapi``, which live in this
repository.
"""

import asyncio
from unittest.mock import AsyncMock

from kajenn.channel.frame import Frame, FrameCodec
from kajenn.channel.local import LocalFrameStream
from kajenn.http_record import HttpRecord
from kajenn.remote_runner import RemoteApplicationRunner


async def test_remote_runner_oversized_result_is_a_reply_error_and_next_call_succeeds():
    runner = RemoteApplicationRunner("examples.remote_openapi.app:create_application", "tcp:127.0.0.1:0")
    outgoing = asyncio.Queue()
    stream = LocalFrameStream(asyncio.Queue(), outgoing, max_size=2048)
    runner.endpoint.serve = AsyncMock(side_effect=[
        {"status": 200, "headers": [], "body": b"x" * 4096},
        {"status": 200, "headers": [], "body": b"ok"},
    ])
    for expected_error in (True, False):
        request = Frame(method="CALL", path="/http", info={"format": "http", "mount": "demo"},
                        payload=HttpRecord().encode_request({"type": "http", "method": "GET", "path": "/"}, b""))
        await runner._slots.acquire()
        await runner._serve(stream, request)
        reply = FrameCodec().get_frame(await asyncio.wait_for(outgoing.get(), 1))
        assert reply.id == request.id
        assert ("error" in reply.info) == expected_error
        if expected_error:
            assert reply.info["error"] == "FrameTooLarge"
        else:
            assert HttpRecord().decode_response(reply.payload)["body"] == b"ok"
        assert not stream.closed
