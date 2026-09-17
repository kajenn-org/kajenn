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

"""HTTP control-flow exceptions tests (Macro 4 Phase 3: the ``headers`` field).

The status/detail contract and the new optional ``headers`` (ASGI byte pairs
forwarded to the response, e.g. a ``WWW-Authenticate`` challenge) carried by
``HTTPException`` and its pre-filled subclasses.
"""

from __future__ import annotations

from kajenn.exceptions import (
    HTTPBadRequest,
    HTTPException,
    HTTPForbidden,
    HTTPNotFound,
    HTTPUnauthorized,
    HTTPUnprocessableContent,
    HTTPUnsupportedMediaType,
    Redirect,
)


class TestHTTPException:
    def test_status_and_detail(self) -> None:
        exc = HTTPException(500, "boom")
        assert (exc.status, exc.detail) == (500, "boom")

    def test_headers_default_to_empty_list(self) -> None:
        assert HTTPException(500).headers == []

    def test_headers_are_carried(self) -> None:
        headers = [(b"www-authenticate", b"Bearer")]
        assert HTTPException(401, "no", headers=headers).headers == headers


class TestSubclasses:
    def test_prefilled_statuses(self) -> None:
        assert HTTPBadRequest().status == 400
        assert HTTPNotFound().status == 404
        assert HTTPUnauthorized().status == 401
        assert HTTPForbidden().status == 403
        assert HTTPUnprocessableContent().status == 422
        assert HTTPUnsupportedMediaType().status == 415

    def test_subclasses_forward_headers(self) -> None:
        challenge = [(b"www-authenticate", b"Bearer")]
        assert HTTPUnauthorized("no", headers=challenge).headers == challenge
        assert HTTPForbidden("no", headers=challenge).headers == challenge
        assert HTTPNotFound("no", headers=challenge).headers == challenge
        assert HTTPBadRequest("no", headers=challenge).headers == challenge

    def test_subclasses_are_http_exceptions(self) -> None:
        assert isinstance(HTTPNotFound(), HTTPException)
        assert isinstance(HTTPBadRequest(), HTTPException)

    def test_bad_request_carries_detail(self) -> None:
        assert HTTPBadRequest("nope").detail == "nope"


class TestRedirect:
    def test_location_and_default_status(self) -> None:
        redirect = Redirect("/elsewhere")
        assert (redirect.status, redirect.location) == (302, "/elsewhere")
        assert redirect.headers == []

    def test_custom_status_and_headers(self) -> None:
        redirect = Redirect("/new", status=301, headers=[(b"x-note", b"moved")])
        assert redirect.status == 301
        assert redirect.headers == [(b"x-note", b"moved")]
