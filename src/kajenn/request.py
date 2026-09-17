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

"""HTTP request: one flat class over the ASGI scope, eager body parsing.

``Request`` is HTTP-only — no transport abstraction (the WSX/message transport
is orchestration, out of the core). It wraps the ASGI ``scope`` and, in the
async ``init()``, reads the request once and by itself: headers and cookies
off the scope, the query string, and the whole body pumped from ``receive``
until ``more_body`` is false — always, whatever the content-type, so a
request never leaves unread ASGI messages behind. genro-tytx is used ONLY as
a serializer: header values, query values and multipart fields are hydrated
with ``from_tytx``, an urlencoded body with ``from_qs``, a json/xml/msgpack
body with ``from_tytx(transport=...)``. The transport → media type map lives
in ``media_types``, the inbound content-type is resolved here in
``get_transport``; the protocol reading lives here and nowhere else.

The owning application decides whether the body is decoded at all
(``request(body=...)`` in its own grammar, ``decoded`` by default). Decoded,
it is decoded by content-type:

- json / xml / msgpack (standard or ``application/vnd.tytx+*`` media type) →
  the hydrated value;
- ``application/x-www-form-urlencoded`` → a dict via ``from_qs``;
- ``multipart/form-data`` → a dict: text parts hydrated with ``from_tytx``,
  file parts (those carrying a ``filename``) as ``UploadedFile``; a field
  name repeated across parts collects its values in a list;
- anything else → ``HTTPUnsupportedMediaType`` (415): the core decodes what it
  knows and says so when it does not;
- an empty body → ``None``.

A body that is not what its content-type declares — broken JSON, XML or
msgpack, a malformed form or multipart — raises ``HTTPBadRequest`` (400) with
the reason the decoder gave, inside the request itself, so no decode failure
reaches the client as a 500. An application that declared ``body="raw"``
receives the bytes untouched and decodes them itself: ``decode_body``,
``decode_json``, ``decode_multipart`` and ``get_transport`` are public for it.

``UploadedFile`` is the file a client uploaded in a multipart form: ``name``
(the form field), ``filename`` (as sent by the client), ``content_type``
(declared for that part) and ``data`` (the bytes, whole — no spooling, no
streaming: the body is already resident).

TYTX mode is detected from the ``X-TYTX-Transport`` header; the paired
``Response`` reads ``tytx_mode`` / ``tytx_transport`` to serialize the reply
in the same transport. The owning application creates the request
(``Request(scope, receive, application=app)``, or ``server=`` directly) and
holds the response seam: ``self.response`` is a ``Response`` bound back to it.

``handler_kwargs()`` builds the kwargs a route handler receives: the query is
the base; a form body — urlencoded OR multipart — is merged field by field
(body wins on a clash, files included: ``def upload(self, title, doc)`` gets
``doc`` as an ``UploadedFile``); a hydrated body is passed whole as
``body_data``; an undecoded body as ``body_raw``; an empty body adds nothing.

``db`` is the deferred preparation layer (no ORM yet): on first access it
resolves the server's registered handler for the owning app's ``db_name`` (else
``"default"``) and registers its ``closeConnection`` as a request cleanup (drained
by the server at end of request). ``get_db(name)`` is a plain lookup with no
cleanup registration. Auth and session ride the scope (``scope["auth"]`` — an
``Avatar`` or ``None`` — and ``scope["session"]``), set by the middleware chain.
"""

from __future__ import annotations

import email.policy
import time
import uuid
from collections import defaultdict
from email.parser import BytesParser
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs

from genro_tytx import from_qs, from_tytx

from .exceptions import HTTPBadRequest, HTTPException, HTTPUnsupportedMediaType
from .response import Response
from .session.session import Session

if TYPE_CHECKING:
    from .application import BaseApplication
    from .server import BaseServer
    from .types import Receive, Scope

__all__ = ["Request", "UploadedFile"]


class UploadedFile:
    """A file uploaded in a multipart form: its form field, name, type and bytes."""

    __slots__ = ("name", "filename", "content_type", "data")

    def __init__(self, name: str, filename: str, content_type: str, data: bytes) -> None:
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.data = data

    def __repr__(self) -> str:
        return (
            f"<UploadedFile name={self.name!r} filename={self.filename!r} "
            f"bytes={len(self.data)}>"
        )


class Request:
    """An ASGI HTTP request: scope wrapper with eager, TYTX-aware body parsing."""

    __slots__ = (
        "_scope",
        "_receive",
        "_server",
        "_application",
        "_db",
        "_headers",
        "_cookies",
        "_query",
        "_data",
        "_id",
        "_external_id",
        "_tytx_mode",
        "_tytx_transport",
        "_created_at",
        "response",
    )

    def __init__(
        self,
        scope: Scope,
        receive: Receive,
        *,
        server: BaseServer | None = None,
        application: BaseApplication | None = None,
    ) -> None:
        self._scope = scope
        self._receive = receive
        self._server = server
        self._application = application
        self._db: Any = None
        self._headers: dict[str, Any] = {}
        self._cookies: dict[str, str] = {}
        self._query: dict[str, Any] = {}
        self._data: Any = None
        self._id: str = ""
        self._external_id: str | None = None
        self._tytx_mode: bool = False
        self._tytx_transport: str | None = None
        self._created_at: float = time.time()
        self.response: Response = Response(request=self)

    async def init(self) -> None:
        """Read headers, cookies, query and body from the scope (once).

        Then derives TYTX mode, the request id (``x-request-id`` header or a
        fresh uuid4) and the optional client correlation id (``x-external-id``).
        """
        cookie_header = self.read_headers()
        self._cookies = self.decode_cookies(cookie_header)
        self._query = self.decode_query(self._scope.get("query_string", b"").decode("latin-1"))
        body = await self.read_body()
        self._data = self.parse_body(body)
        transport = self._headers.get("x-tytx-transport")
        if transport:
            self._tytx_mode = True
            self._tytx_transport = str(transport).lower()
        request_id = self._headers.get("x-request-id")
        self._id = str(request_id) if request_id else str(uuid.uuid4())
        external_id = self._headers.get("x-external-id")
        self._external_id = str(external_id) if external_id is not None else None

    def read_headers(self) -> str:
        """Fill the header map off the scope and hand back the raw cookie header.

        Keys are lowercased and values TYTX-hydrated; ``cookie`` stays out of the
        map — it is the one header ``decode_cookies`` owns.
        """
        cookie_header = ""
        for name, value in self._scope.get("headers", []):
            key = name.decode("latin-1").lower()
            text = value.decode("latin-1")
            if key == "cookie":
                cookie_header = text
            else:
                self._headers[key] = from_tytx(text)
        return cookie_header

    def decode_cookies(self, cookie_header: str) -> dict[str, str]:
        """Split a ``Cookie`` header into its morsels, values TYTX-hydrated."""
        cookies: SimpleCookie = SimpleCookie()
        cookies.load(cookie_header)
        return {key: from_tytx(morsel.value) for key, morsel in cookies.items()}

    def decode_query(self, query_string: str) -> dict[str, Any]:
        """Split a query string, values TYTX-hydrated (a repeated key gives a list)."""
        parsed = parse_qs(query_string, keep_blank_values=True)
        return {
            key: from_tytx(values[0]) if len(values) == 1 else [from_tytx(v) for v in values]
            for key, values in parsed.items()
        }

    async def read_body(self) -> bytes:
        """Pump the ASGI body messages until ``more_body`` is false, joined once."""
        chunks: list[bytes] = []
        while True:
            message = await self._receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                return b"".join(chunks)

    def get_transport(self, content_type: str) -> Literal["json", "xml", "msgpack"] | None:
        """The TYTX transport a content-type names, or ``None`` for the others.

        Substring matching, so the standard media type (``application/json``) and
        the TYTX one (``application/vnd.tytx+json``) resolve to the same transport.
        """
        if "json" in content_type:
            return "json"
        if "xml" in content_type:
            return "xml"
        if "msgpack" in content_type:
            return "msgpack"
        return None

    def parse_body(self, body: bytes) -> Any:
        """The body as the owning application wants it: decoded, or the bytes.

        An application declaring ``request(body="raw")`` — and one serving a
        request nobody owns — keeps the bytes (an empty body is ``None``);
        every other application gets ``decode_body`` on the declared
        content-type.
        """
        application = self._application
        if application is not None and application.raw_body:
            return body or None
        return self.decode_body(body, str(self.content_type or ""))

    def decode_body(self, body: bytes, content_type: str) -> Any:
        """Decode the body bytes by content-type.

        A json/xml/msgpack body comes back hydrated and a form body (urlencoded
        or multipart) as a dict of fields; an empty body is ``None``. The media
        type decides case-insensitively, while the multipart parser gets the
        header as sent — its boundary is case-sensitive.

        A body that is not what its content-type declares raises
        ``HTTPBadRequest`` with the reason the decoder gave — a JSON decoder
        that answers the text it could not parse is read as the failure it is —
        and a content-type with no decoder raises ``HTTPUnsupportedMediaType``.
        Public, and callable by an application that took the body raw.
        """
        if not body:
            return None
        media = content_type.lower()
        transport = self.get_transport(media)
        try:
            if transport == "msgpack":
                return from_tytx(body, transport=transport)
            if transport == "xml":
                return from_tytx(body.decode("utf-8"), transport=transport)
            if transport == "json":
                return self.decode_json(body.decode("utf-8"))
            if "x-www-form-urlencoded" in media:
                return from_qs(body.decode("latin-1"))
            if "multipart/form-data" in media:
                return self.decode_multipart(body, content_type)
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPBadRequest(f"body is not valid {media}: {error}") from error
        raise HTTPUnsupportedMediaType(f"no decoder for content-type {media or '(none)'}")

    def decode_json(self, text: str) -> Any:
        """Hydrate a JSON body, refusing the text a broken document decodes to.

        ``from_tytx`` answers the input itself when the JSON does not parse, so
        a result identical to the text it was given IS the parse failure.
        """
        decoded = from_tytx(text, transport="json")
        if isinstance(decoded, str) and decoded == text:
            raise HTTPBadRequest("body is not valid json")
        return decoded

    def decode_multipart(self, body: bytes, content_type: str) -> dict[str, Any]:
        """Split a multipart form body into its fields, keyed by form name.

        A part carrying a ``filename`` becomes an ``UploadedFile``, a text part is
        TYTX-hydrated, and a name repeated across parts collects a list.

        The stdlib parser is lenient where HTTP is not: a body that yields no
        part at all, and a part that names no form field, are malformed and
        raise ``HTTPBadRequest`` — a nameless field would otherwise reach the
        handler as a keyword that is not a string.
        """
        raw = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
        form = BytesParser(policy=email.policy.HTTP).parsebytes(raw)
        fields: defaultdict[str, list[Any]] = defaultdict(list)
        for part in form.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name is None:
                raise HTTPBadRequest("multipart body carries a part with no form name")
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if filename is None:
                fields[name].append(from_tytx(payload.decode("utf-8")))
            else:
                fields[name].append(UploadedFile(name, filename, part.get_content_type(), payload))
        if not fields:
            raise HTTPBadRequest("body is not a valid multipart form")
        return {name: values[0] if len(values) == 1 else values for name, values in fields.items()}

    @property
    def id(self) -> str:
        """Correlation id: the ``x-request-id`` header, or a generated uuid4."""
        return self._id

    @property
    def method(self) -> str:
        """HTTP method (uppercased)."""
        return str(self._scope.get("method", "GET")).upper()

    @property
    def path(self) -> str:
        """Request path."""
        return str(self._scope.get("path", "/"))

    @property
    def headers(self) -> dict[str, Any]:
        """Request headers (lowercase keys), values hydrated by TYTX."""
        return self._headers

    @property
    def cookies(self) -> dict[str, str]:
        """Request cookies parsed from the ``Cookie`` header."""
        return self._cookies

    @property
    def query(self) -> dict[str, Any]:
        """Query parameters (typed via TYTX)."""
        return self._query

    @property
    def data(self) -> Any:
        """Parsed body: hydrated value, raw bytes, or ``None`` when empty."""
        return self._data

    @property
    def content_type(self) -> str | None:
        """``Content-Type`` header value, or ``None``."""
        return self._headers.get("content-type")

    @property
    def external_id(self) -> str | None:
        """Client-provided correlation id (``x-external-id`` header)."""
        return self._external_id

    @property
    def tytx_mode(self) -> bool:
        """True when the request declared a TYTX transport."""
        return self._tytx_mode

    @property
    def tytx_transport(self) -> str | None:
        """TYTX transport (``json``/``xml``/``msgpack``), or ``None``."""
        return self._tytx_transport

    @property
    def created_at(self) -> float:
        """Wall-clock timestamp captured at construction."""
        return self._created_at

    @property
    def age(self) -> float:
        """Seconds elapsed since construction."""
        return time.time() - self._created_at

    @property
    def scope(self) -> Scope:
        """The raw ASGI scope."""
        return self._scope

    @property
    def server(self) -> BaseServer | None:
        """The owning server (passed directly, or via the owning application)."""
        if self._server is not None:
            return self._server
        return self._application.server if self._application is not None else None

    @property
    def application(self) -> BaseApplication | None:
        """The application that created this request (``None`` if unbound)."""
        return self._application

    def avatar(self, key: str = Session.ROOT_AVATAR_KEY) -> Any:
        """The identity acting on this request under ``key`` (an ``Avatar``) or ``None``.

        With no argument — the root slot — it is the effective identity the auth
        chain resolved for this request: header credentials or the session's root
        avatar, read from the scope. Any other key is a sub-login, looked up in
        the session's keyed avatars (``None`` without a session).
        """
        if key == Session.ROOT_AVATAR_KEY:
            return self._scope.get("auth")
        session = self.session
        return session.avatar(key) if session is not None else None

    @property
    def auth_tags(self) -> list[str]:
        """Authorization tags of the current identity (empty when anonymous)."""
        avatar = self.avatar()
        return list(avatar.tags) if avatar is not None else []

    @property
    def session(self) -> Any:
        """The session object attached by ``SessionMiddleware``, or ``None``."""
        return self._scope.get("session")

    @property
    def db(self) -> Any:
        """The default db handler for the owning app, or ``None`` (lazy).

        Resolves ``server.databases[name]`` where ``name`` is the owning
        application's ``db_name`` attribute if set, else ``"default"``. On the
        first successful resolution it registers ``handler.closeConnection`` as a
        request cleanup (drained by the server at end of request). Returns
        ``None`` when there is no server or no handler under that name.

        Preparation layer only: no pooling, no transactions, no per-app registry.
        """
        if self._db is not None:
            return self._db
        server = self.server
        if server is None:
            return None
        name = getattr(self._application, "db_name", None) or "default"
        handler = server.databases.get(name)
        if handler is None:
            return None
        self._db = handler
        current = server.requests.current
        if current is not None:
            current.add_cleanup(handler.closeConnection)
        return handler

    def get_db(self, name: str) -> Any:
        """Look up a registered db handler by ``name`` (no cleanup registration)."""
        server = self.server
        if server is None:
            return None
        return server.databases.get(name)

    def handler_kwargs(self) -> dict[str, Any]:
        """Build the kwargs a route handler is called with (query + body).

        The query params are the base. The body adds to them by content-type,
        not by Python shape: a form body — ``x-www-form-urlencoded`` or
        ``multipart/form-data`` — arrives as a dict of hydrated fields (files
        included, as ``UploadedFile``) and is merged field by field, the body
        winning on a name clash; a hydrated body (JSON/XML/msgpack) is passed
        whole as ``body_data``; the bytes of a raw body are passed as
        ``body_raw``; an empty body adds nothing.
        """
        kwargs: dict[str, Any] = dict(self._query)
        data = self._data
        if data is None:
            return kwargs
        if isinstance(data, bytes):
            kwargs["body_raw"] = data
            return kwargs
        content_type = (self.content_type or "").lower()
        if "x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            if isinstance(data, dict):
                kwargs.update(data)
        else:
            kwargs["body_data"] = data
        return kwargs

    def __repr__(self) -> str:
        return f"<Request id={self._id!r} method={self.method} path={self.path!r}>"
