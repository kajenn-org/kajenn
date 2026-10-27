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

"""HEAD requests (issue #77).

A ``HEAD`` request reaches the same handler as ``GET``. The answer carries the
same status and headers, content-length included, and an empty body: the
content-length describes the bytes the ``GET`` would send.

Every request drives a REAL ``AsgiServer`` composition at the ASGI level.
"""

from __future__ import annotations

from typing import Any

from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.types import Message

ASSET = bytes(range(256)) * 4


class AssetApp(RoutedApplication):
    """The application under test: a non-empty resource and an empty one."""

    code = "assets"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.plug("pydantic")

    @route()
    def asset(self, _request=None) -> bytes:
        """A cached binary resource whose length the handler also declares."""
        response = _request.response
        response.set_header("cache-control", "public, max-age=31536000, immutable")
        response.set_header("content-length", str(len(ASSET)))
        return ASSET

    @route()
    def text(self) -> str:
        """A text resource with no header set by the handler."""
        return "cafè"

    @route()
    def nothing(self) -> None:
        """An empty resource."""
        return None


def server() -> AsgiServer:
    return AsgiServer(applications=[(AssetApp, {"mount": ""})])


class TestHeadMatchesGet:
    """HEAD answers with the headers of GET and no body."""

    async def test_get_sends_the_resource(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        sent = await http_request(server(), "/asset")
        assert response_status(sent) == 200
        assert response_body(sent) == ASSET
        assert response_headers(sent)[b"content-length"] == str(len(ASSET)).encode()

    async def test_head_keeps_the_get_content_length(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        sent = await http_request(server(), "/asset", method="HEAD")
        assert response_status(sent) == 200
        assert response_body(sent) == b""
        assert response_headers(sent)[b"content-length"] == str(len(ASSET)).encode()

    async def test_head_headers_equal_get_headers(self, http_request) -> None:
        def comparable(sent: list[Message]) -> list[tuple[bytes, bytes]]:
            # Each request opens its own session: the cookie value differs.
            return [
                (name, b"" if name == b"set-cookie" else value)
                for name, value in sent[0]["headers"]
            ]

        get = await http_request(server(), "/asset")
        head = await http_request(server(), "/asset", method="HEAD")
        assert comparable(head) == comparable(get)

    async def test_head_keeps_the_cache_headers(self, http_request, response_headers) -> None:
        sent = await http_request(server(), "/asset", method="HEAD")
        headers = response_headers(sent)
        assert headers[b"cache-control"] == b"public, max-age=31536000, immutable"
        assert headers[b"content-type"] == b"application/octet-stream"

    async def test_head_of_text_counts_encoded_bytes(
        self, http_request, response_headers, response_body
    ) -> None:
        sent = await http_request(server(), "/text", method="HEAD")
        assert response_body(sent) == b""
        assert response_headers(sent)[b"content-length"] == str(len("cafè".encode())).encode()

    async def test_head_of_empty_resource_is_zero(
        self, http_request, response_headers, response_body
    ) -> None:
        sent = await http_request(server(), "/nothing", method="HEAD")
        assert response_body(sent) == b""
        assert response_headers(sent)[b"content-length"] == b"0"

    async def test_head_sends_one_start_and_one_body(self, http_request) -> None:
        sent = await http_request(server(), "/asset", method="HEAD")
        assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
