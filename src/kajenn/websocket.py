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

"""The websocket facade: one ASGI socket as an object, and nothing above it.

``WebSocket`` wraps the three things an ASGI server hands a websocket
application — the scope, ``receive`` and ``send`` — and gives them a shape a
reader can follow: ``accept()`` consumes the connect and answers it,
``receive_text()`` and ``receive_bytes()`` read one message, ``send_text()``
and ``send_bytes()`` write one, ``close()`` ends the connection once, and
iterating the object yields the incoming texts until the client leaves.

**It knows nothing of WSX.** The protocol lives in ``wsx.py`` and the motor in
the server; this object is the transport, so the admitted raw seam — an
application that wants the socket itself — is served by the same class
(`internals/10_server/055_websocket/decisions.md`).

**The state is one boolean.** ``connected`` is true between the accept and the
end, and everything that depends on the state reads it: an accept happens once,
a close writes once, and a read or a write with nothing accepted raises. There
is no exported state type, because the three readers of the state are those
three rules and each is a question with a yes or a no (owner, 2026-09-06; the
precedent is ``WorkerConnector.connected``).

**A disconnect is an exception, never a value.** Every read raises
``WebSocketDisconnect`` when the client is gone, so a read loop never has to
check what it got back, and the iterator ends on it.

**The handshake facts are read once, in the constructor.** The path, the
headers, the cookies and the subprotocols the client offered come off the
scope — headers lowercased and TYTX-hydrated, cookies split out of the
``Cookie`` header, exactly as ``Request`` does for HTTP.

``WebSocketRegistry`` is the server's picture of what is connected: the live
sockets, and the ``page_id → socket`` association a page writes when it opens
its channel, so the server can address one page later. It is NEUTRAL — it does
not know the SPA and validates nothing: whether a page belongs to the
connection asking for it is judged by the application that holds the pool.
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any, AsyncIterator

from genro_tytx import from_tytx

from .exceptions import WebSocketDisconnect
from .types import Receive, Scope, Send

__all__ = ["WebSocket", "WebSocketRegistry"]


class WebSocket:
    """One ASGI websocket connection, as an object."""

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Args:
        scope: the ASGI websocket scope of the handshake.
        receive: the ASGI receive callable.
        send: the ASGI send callable.
        """
        self.scope = scope
        self.asgi_receive = receive
        self.asgi_send = send
        self.accepted_subprotocol: str | None = None
        self._connected = False
        self._closed = False
        self._headers: dict[str, Any] = {}
        self._cookies: dict[str, str] = {}
        self.read_handshake()

    def read_handshake(self) -> None:
        """Fill the header and cookie maps off the scope.

        Keys are lowercased and values TYTX-hydrated; ``cookie`` stays out of
        the map and becomes ``cookies``, the way ``Request`` reads an HTTP
        request. Acts on the instance; called by ``__init__``.
        """
        cookie_header = ""
        for name, value in self.scope.get("headers") or []:
            key = name.decode("latin-1").lower()
            text = value.decode("latin-1")
            if key == "cookie":
                cookie_header = text
            else:
                self._headers[key] = from_tytx(text)
        if cookie_header:
            morsels: SimpleCookie = SimpleCookie()
            morsels.load(cookie_header)
            self._cookies = {name: morsel.value for name, morsel in morsels.items()}

    @property
    def connected(self) -> bool:
        """Whether this socket is accepted and not yet closed."""
        return self._connected

    @property
    def path(self) -> str:
        """The path of the handshake — what names the home application."""
        return str(self.scope.get("path", "/"))

    @property
    def headers(self) -> dict[str, Any]:
        """The handshake headers, lowercase keys, values hydrated by TYTX."""
        return self._headers

    @property
    def cookies(self) -> dict[str, str]:
        """The cookies of the handshake, from its ``Cookie`` header."""
        return self._cookies

    @property
    def subprotocols(self) -> tuple[str, ...]:
        """The subprotocols the client offered, in the order it offered them."""
        return tuple(self.scope.get("subprotocols") or ())

    async def accept(
        self, subprotocol: str | None = None, headers: dict[str, str] | None = None
    ) -> None:
        """Consume the connect and accept the connection.

        Args:
            subprotocol: the one to negotiate, when the client offered any.
            headers: response headers of the handshake — the one place a
                websocket can carry a ``Set-Cookie``.

        Raises:
            RuntimeError: this socket was already accepted, or the first
                message on the wire was not ``websocket.connect``.

        Sets ``connected``.
        """
        if self._connected or self._closed:
            raise RuntimeError("this socket cannot accept: it was accepted already")
        message = await self.asgi_receive()
        if message["type"] != "websocket.connect":
            raise RuntimeError(f"expected websocket.connect, got {message['type']}")
        accept: dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol is not None:
            accept["subprotocol"] = subprotocol
            self.accepted_subprotocol = subprotocol
        if headers is not None:
            accept["headers"] = [
                (name.encode("latin-1"), value.encode("latin-1"))
                for name, value in headers.items()
            ]
        await self.asgi_send(accept)
        self._connected = True

    async def refuse(self, code: int = 1008, reason: str = "") -> None:
        """Turn the handshake away without accepting it.

        Args:
            code: the close code the client sees.
            reason: the text that travels with it.

        Raises:
            RuntimeError: this socket was accepted already — turning away what
                is already in is a ``close``, not a refusal.

        The connect is consumed first: a close written before it is read leaves
        that message on the wire. Sets ``connected`` to false, so nothing can
        be written afterwards.
        """
        if self._connected or self._closed:
            raise RuntimeError("this socket cannot refuse: it was accepted already")
        await self.asgi_receive()
        self._closed = True
        await self.asgi_send({"type": "websocket.close", "code": code, "reason": reason})

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """End the connection, once.

        Args:
            code: the websocket close code.
            reason: the text that travels with it.

        Raises:
            RuntimeError: nothing was accepted yet — before the accept a
                handshake is turned away with ``refuse``, which is what a
                hostile Origin gets. Everything judged AFTER the accept — the
                home application's cookie, an invalid credential, a server that
                is not running — is accepted first and closed here with its
                code, so the browser can read why.

        Sets ``connected`` to false. Calling it again writes nothing.
        """
        if self._closed:
            return
        if not self._connected:
            raise RuntimeError("this socket cannot close: it is not accepted")
        self._connected = False
        self._closed = True
        await self.asgi_send({"type": "websocket.close", "code": code, "reason": reason})

    async def receive_text(self) -> str:
        """The next message, as text.

        Returns:
            The message's text.

        Raises:
            RuntimeError: this socket is not connected.
            TypeError: the message carried bytes.
            WebSocketDisconnect: the client is gone.
        """
        message = await self.read_message()
        if message.get("text") is None:
            raise TypeError("this message is binary: read it with receive_bytes()")
        return str(message["text"])

    async def receive_bytes(self) -> bytes:
        """The next message, as bytes.

        Returns:
            The message's bytes.

        Raises:
            RuntimeError: this socket is not connected.
            TypeError: the message carried text.
            WebSocketDisconnect: the client is gone.
        """
        message = await self.read_message()
        if message.get("bytes") is None:
            raise TypeError("this message is text: read it with receive_text()")
        return bytes(message["bytes"])

    async def read_message(self) -> dict[str, Any]:
        """One raw ASGI message, with the disconnect turned into an exception.

        Returns:
            The ``websocket.receive`` message as it came.

        Raises:
            RuntimeError: this socket is not connected.
            WebSocketDisconnect: the client is gone; ``connected`` is false
                from here on.
        """
        if not self._connected:
            raise RuntimeError("this socket is not connected")
        message = await self.asgi_receive()
        if message["type"] == "websocket.disconnect":
            self._connected = False
            self._closed = True
            raise WebSocketDisconnect(message.get("code", 1000), message.get("reason", ""))
        return dict(message)

    async def send_text(self, text: str) -> None:
        """Write one text message.

        Raises:
            RuntimeError: this socket is not connected.
        """
        await self.write_message({"type": "websocket.send", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        """Write one binary message.

        Raises:
            RuntimeError: this socket is not connected.
        """
        await self.write_message({"type": "websocket.send", "bytes": data})

    async def write_message(self, message: dict[str, Any]) -> None:
        """Write one raw ASGI message.

        Args:
            message: the ``websocket.send`` message to write.

        Raises:
            RuntimeError: this socket is not connected — nothing is written to
                a socket nobody accepted, and nothing after a close.
        """
        if not self._connected:
            raise RuntimeError("this socket is not connected")
        await self.asgi_send(message)

    async def __aiter__(self) -> AsyncIterator[str]:
        """The incoming texts, until the client leaves.

        The disconnect ends the loop instead of raising: a read loop's ordinary
        end is the client going away.
        """
        while True:
            try:
                yield await self.receive_text()
            except WebSocketDisconnect:
                return


class WebSocketRegistry:
    """The live sockets of one server, and which one each page speaks on."""

    def __init__(self) -> None:
        self._sockets: list[WebSocket] = []
        self._page_sockets: dict[str, WebSocket] = {}

    def register(self, socket: WebSocket) -> None:
        """Take one accepted socket into the picture.

        Args:
            socket: the facade of a connection that was just accepted.
        """
        self._sockets.append(socket)

    def unregister(self, socket: WebSocket) -> None:
        """Take one socket out, and every page that still speaks on IT.

        Args:
            socket: the connection that ended.

        A page whose association has moved to another socket — a reconnection
        that happened before this one closed — is left alone: the comparison is
        on the socket itself, never on the page. A socket that was never
        registered is no error: the ``finally`` of a handshake that failed
        before the accept comes through here too.
        """
        if socket in self._sockets:
            self._sockets.remove(socket)
        for page_id in [page for page, bound in self._page_sockets.items() if bound is socket]:
            del self._page_sockets[page_id]

    def bind_page(self, page_id: str, socket: WebSocket) -> None:
        """Say that this page speaks on this socket.

        Args:
            page_id: the page opening its channel.
            socket: the connection its messages arrive on.

        A page already bound is REBOUND, with no error: a browser that lost its
        socket and opened a new one says so again, and the association follows
        it. One socket carries as many pages as the browser has under that
        connection.
        """
        self._page_sockets[page_id] = socket

    def get_page_socket(self, page_id: str) -> WebSocket | None:
        """The socket that page speaks on, or ``None`` when it speaks on none.

        Args:
            page_id: the page to address.

        Returns:
            Its socket, or ``None`` — the page never opened a channel, its
            socket is gone, or the page itself is.
        """
        return self._page_sockets.get(page_id)

    def snapshot(self) -> list[WebSocket]:
        """The live sockets, in the order they were accepted."""
        return list(self._sockets)
