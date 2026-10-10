# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The mount of an application that runs in another process.

``RemoteApplication`` is built by the server for every application declared
with ``spawner=``: it opens one kbus stream per http or WSK request, with an
``HttpRecord`` of the request as the opening message, to the member named
after the application code, writes the answer as its messages arrive, and
answers 503 while that process has not joined. When the real class defines
``serve_websocket``, a websocket on the mount opens one stream as well, and
every ASGI websocket event crosses it as one message, in both directions.

The mount is a transparent wire: the application receives a request as it
would in this process. Every header travels in the record and the child
authenticates on its own face, through the parent's authentication route over
the bus. Only the identity this process holds — a session avatar, an avatar
stamped on the scope — travels as ``meta["auth"]``; the scope's channel, its
``kajenn.kbus`` flag and its ``genro.page_id``/``genro.reply_path`` travel as
``meta["channel"]``, ``meta["kbus"]`` and ``meta["genro"]``.

The kwargs of the declaration are split in two: ``PROXY_OPTIONS`` belong to
this mount, every other kwarg belongs to the real application, built in the
spawned process. The mount resolves ``code`` and ``mount`` as the real class
does, so both processes answer the same URLs.
"""

import asyncio
import json
from typing import Any

import kbus

from .application import BaseApplication
from .spawner import cancel_and_wait
from .asgi_endpoint import BufferedAsgiEndpoint
from .exceptions import ExternalFailure, HTTPException, Redirect, failure_text
from .http_record import HttpRecord
from .middleware.base import headers_dict
from .response import Response
from .transport_limits import HttpBodyTooLarge, http_max_body_size

__all__ = ["PROXY_OPTIONS", "RemoteApplication", "failure_message", "raised_failure",
           "websocket_event", "websocket_message"]

#: The kwargs of an application declaration that belong to its proxy mount:
#: the spawned process builds the real application without them.
PROXY_OPTIONS = frozenset({"spawner", "request_timeout", "max_calls"})


def websocket_message(event: dict[str, Any]) -> kbus.Message:
    """One ASGI websocket event as one stream message.

    ``meta`` carries every key of the event but ``text`` and ``bytes``, with
    ``headers`` as ``[name, value]`` latin-1 text pairs; a text event has the
    UTF-8 text as payload and ``meta["text"] = True``, a bytes event the bytes
    and ``meta["bytes"] = True``.
    """
    meta = {key: value for key, value in event.items() if key not in ("text", "bytes")}
    if "headers" in meta:
        meta["headers"] = [[name.decode("latin-1"), value.decode("latin-1")]
                           for name, value in meta["headers"]]
    if event.get("text") is not None:
        meta["text"] = True
        return kbus.Message(meta, event["text"].encode())
    if event.get("bytes") is not None:
        meta["bytes"] = True
        return kbus.Message(meta, event["bytes"])
    return kbus.Message(meta)


def websocket_event(message: kbus.Message) -> dict[str, Any]:
    """The ASGI websocket event ``websocket_message`` turned into ``message``."""
    event = {key: value for key, value in message.meta.items() if key not in ("text", "bytes")}
    if "headers" in event:
        event["headers"] = [(name.encode("latin-1"), value.encode("latin-1"))
                            for name, value in event["headers"]]
    if message.meta.get("text"):
        event["text"] = message.payload.decode()
    elif message.meta.get("bytes"):
        event["bytes"] = message.payload
    return event


def failure_message(failure: Exception) -> kbus.Message:
    """An exception raised before the answer started, as one stream message.

    ``meta["failure"]`` carries the ``status``, ``detail`` and ``headers`` of
    an ``HTTPException`` (plus ``location`` for a ``Redirect``), or for any
    other exception only its ``"<type>: <message>"`` as ``detail``.
    """
    if not isinstance(failure, HTTPException):
        return kbus.Message({"failure": {"detail": failure_text(failure)}})
    record: dict[str, Any] = {
        "status": failure.status, "detail": failure.detail,
        "headers": [[name.decode("latin-1"), value.decode("latin-1")]
                    for name, value in failure.headers]}
    if isinstance(failure, Redirect):
        record["location"] = failure.location
    return kbus.Message({"failure": record})


def raised_failure(record: dict[str, Any]) -> Exception:
    """The exception ``failure_message`` turned into ``record``.

    Anything but an ``HTTPException`` comes back as an ``ExternalFailure``
    carrying its ``"<type>: <message>"``.
    """
    if "status" not in record:
        return ExternalFailure(record["detail"])
    headers = [(name.encode("latin-1"), value.encode("latin-1"))
               for name, value in record["headers"]]
    if "location" in record:
        return Redirect(record["location"], record["status"], headers)
    return HTTPException(record["status"], record["detail"], headers)


class RemoteApplication(BaseApplication):
    """Forward one mounted application to the member named after its code."""

    #: Read by ``WsxConnection``: the answers this mount hands back were
    #: already adapted at the endpoint that produced them, so they travel on
    #: without a second conversion.
    forwards_payloads = True

    def __init__(self, *, app_class: type[BaseApplication], spawner: str,
                 request_timeout: float = 30.0, max_calls: int = 16,
                 **app_kwargs: Any) -> None:
        """Build the mount of an external application.

        Args:
            app_class: the real application class, built in the spawned process.
            spawner: the backend that starts the process (``"subprocess"``).
            request_timeout: seconds one forwarded call may take.
            max_calls: how many calls may be in flight at once.
            app_kwargs: the real application's kwargs; only ``code`` and
                ``mount`` are read here, resolved against ``app_class`` the way
                ``BaseApplication`` resolves them.
        """
        super().__init__(
            code=app_kwargs.get("code", app_class.code) or app_class.__name__.lower(),
            mount=app_kwargs.get("mount", app_class.mount))
        self.spawner = spawner
        self.request_timeout = request_timeout
        self.max_calls = max_calls
        self._slots = asyncio.Semaphore(max_calls)
        if hasattr(app_class, "serve_websocket"):
            self.serve_websocket = self._forward_websocket

    async def __call__(self, scope, receive, send) -> None:
        """Forward one request to the member and write its answer as it streams.

        A body over the policy ceiling or the kbus frame limit answers 413, an
        error reply answers 502, a process that has not joined, a lost link, a
        process that dies before its first message or a first message slower
        than ``request_timeout`` answers 503. Once the
        answer started, a failure ends it without its terminal body.
        A client that disconnects while the body is being read ends the call
        silently. An exception the application raised before its answer
        started is raised again here, so the middleware of this process answers
        it as it answers an application of its own.

        Raises:
            ValueError: the scope is not ``http``.
        """
        if scope["type"] != "http":
            raise ValueError("a remote application forwards http scopes; websockets go "
                             "through serve_websocket")
        try:
            await self._forward(scope, receive, send)
            return
        except (kbus.FrameTooLarge, HttpBodyTooLarge):
            status, text = 413, "Request too large"
        except (kbus.NoSuchMember, kbus.LinkLost, TimeoutError):
            status, text = 503, "Remote application unavailable"
        except kbus.Error:
            status, text = 502, "Remote application failed"
        await self._send_local_response(status, text, scope, receive, send)

    async def _forward(self, scope, receive, send) -> None:
        """Open a stream to the member with the request and relay its answer.

        The ``max_calls`` slot and ``request_timeout`` bound the wait for the
        first message only, never the length of the answer.
        """
        await self._slots.acquire()
        held = True
        try:
            async with asyncio.timeout(self.request_timeout) as deadline:
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > http_max_body_size():
                        await self._send_local_response(
                            413, "Request too large", scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
                meta: dict[str, Any] = {"format": "http"}
                avatar = self._trusted_avatar(scope)
                if avatar is not None:
                    meta["auth"] = {"identity": avatar.identity, "tags": list(avatar.tags)}
                if "kajenn.channel" in scope:
                    meta["channel"] = scope["kajenn.channel"]
                if "kajenn.kbus" in scope:
                    meta["kbus"] = True
                genro = {key: scope[f"genro.{key}"] for key in ("page_id", "reply_path")
                         if f"genro.{key}" in scope}
                if genro:
                    meta["genro"] = genro
                meta["path"] = self.forwarded_path(scope)
                record = HttpRecord().encode_request(
                    {**scope, "path": meta["path"], "raw_path": meta["path"].encode(),
                     "root_path": ""},
                    bytes(body))
                started = False
                try:
                    async with self.server.open_external(
                            self.code, kbus.Message(meta, record)) as stream:
                        head = await anext(stream, None)
                        if head is None:
                            raise kbus.LinkLost("the answer ended before it started")
                        if "failure" in head.meta:
                            raise raised_failure(head.meta["failure"])
                        deadline.reschedule(None)
                        self._slots.release()
                        held = False
                        started = True
                        await self._relay(head, stream, scope, receive, send)
                except kbus.Aborted as aborted:
                    if not started:
                        raise kbus.LinkLost(aborted.reason) from aborted
                except kbus.Error:
                    if not started:
                        raise
        finally:
            if held:
                self._slots.release()

    def forwarded_path(self, scope) -> str:
        """The path of ``scope`` as the process sees it: the mount put back in front."""
        return "/" + self.mount + scope["path"] if self.mount else scope["path"]

    async def _relay(self, head, stream, scope, receive, send) -> None:
        """Write the answer: the start from ``head``, then one body per message.

        Each message carries the ``more_body`` its application sent, so a
        buffered answer stays one closing body and a chunked one stays chunked.
        The client leaving aborts the stream, except on a ``WSK`` scope, whose
        ``receive`` never reports it; a stream that fails ends the answer
        without its terminal body.
        """
        await send({"type": "http.response.start", "status": head.meta["status"],
                    "headers": [(name.encode("latin-1"), value.encode("latin-1"))
                                for name, value in head.meta["headers"]]})

        async def forward() -> None:
            async for chunk in stream:
                await send({"type": "http.response.body", "body": chunk.payload,
                            "more_body": chunk.meta["more_body"]})

        async def client_left() -> None:
            while (await receive())["type"] != "http.disconnect":
                pass

        forwarding = asyncio.create_task(forward())
        waiting = {forwarding}
        if scope.get("method") != "WSK":
            leaving = asyncio.create_task(client_left())
            waiting.add(leaving)
        await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        if not forwarding.done():
            forwarding.cancel()
            return
        for task in waiting - {forwarding}:
            task.cancel()
        failure = forwarding.exception()
        if isinstance(failure, kbus.Error):
            return
        if failure is not None:
            raise failure
        await stream.close()

    async def _forward_websocket(self, scope, receive, send) -> None:
        """Carry one websocket to the member: every event, both directions.

        The stream opens with a websocket scope record; the client's events
        cross it until the client disconnects, then this direction closes. The
        application's events reach the client until the process closes its
        direction. A process that has not joined, dies or fails closes the
        client's socket with code 1011.
        """
        path = self.forwarded_path(scope)
        record = HttpRecord().encode_websocket(
            {**scope, "path": path, "raw_path": path.encode(), "root_path": ""})
        client_open = True
        application_closed = False

        async def to_application(stream) -> None:
            nonlocal client_open
            while True:
                event = await receive()
                try:
                    await stream.send(websocket_message(event))
                except kbus.Error:
                    return
                if event["type"] == "websocket.disconnect":
                    client_open = False
                    await stream.close()
                    return

        try:
            async with self.server.open_external(self.code, kbus.Message(
                    {"format": "websocket", "path": path}, record)) as stream:
                forwarding = asyncio.create_task(to_application(stream))
                try:
                    async for message in stream:
                        event = websocket_event(message)
                        await send(event)
                        if event["type"] == "websocket.close":
                            application_closed = True
                    await stream.close()
                except BaseException:
                    forwarding.cancel()
                    await asyncio.gather(forwarding, return_exceptions=True)
                    raise
                await cancel_and_wait(forwarding)
        except kbus.Error:
            if client_open and not application_closed:
                await send({"type": "websocket.close", "code": 1011})

    def _trusted_avatar(self, scope) -> Any:
        """The identity this process vouches for, or ``None``.

        An avatar already on the scope (a stamping middleware) or, without an
        ``Authorization`` header, the root avatar of the session: the session
        store lives here and the child cannot read it. A header is never
        verified here: it travels in the record and the child verifies it on
        its own face.
        """
        if scope.get("auth") is not None:
            return scope["auth"]
        if headers_dict(scope).get("authorization"):
            return None
        session = self.server.session(scope)
        return session.avatar() if session is not None else None

    async def _send_local_response(self, status, text, scope, receive, send) -> None:
        """Write an answer this mount built itself, as a JSON string.

        A ``WSK`` scope gets the answer through ``BufferedAsgiEndpoint``, so it
        reaches the browser in the same json shape a forwarded answer has.
        """
        response = Response(content=json.dumps(text), status_code=status,
                            media_type="application/json")
        if scope.get("method") == "WSK":
            result = await BufferedAsgiEndpoint(response).serve(scope, b"")
            response = Response(content=result["body"], status_code=result["status"],
                                headers=result["headers"])
        await response(scope, receive, send)
