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

"""KajennBus capability: the first mixin over the base server (D17).

The base server is born WITHOUT a KajennBus side. This mixin carries the
applications declared with ``spawner=`` over the kbus library: in the
lifespan of a server that declares one, it builds a ``kbus.Dispatcher`` on
the ``server.kbus`` address and one in-process member named ``server``, and
has each external process started with ONE URL in the environment variable
``KAJENN_KBUS_PARENT``: ``<scheme>://<code>:<token>@<location>``, a fresh
token per spawn. The process connects a ``kbus.Member`` named after its code,
runs its application's ``on_startup``, then calls ``server.joined`` with the
discovery names it answers and its pid. A mount answers 503 until then.

Composed BEFORE the server class (``class MyServer(KBusMixin, BaseServer)``)
the mixin peels its own kwargs, forwards the rest, and hooks the lifespan
without the base knowing it.

A route is reachable through the bus as well as through HTTP:
``kbus_call(path, data)`` builds a ``kbus.Message`` and hands it to
``serve_kbus_message``, the one place a message becomes an ASGI scope. In the
server's own process nothing travels, and the data still crosses as JSON
bytes, so nothing the handler does to its arguments reaches the caller.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import ssl
import tempfile
from typing import TYPE_CHECKING, Any

import kbus

from .asgi_endpoint import BufferedAsgiEndpoint
from .exceptions import HTTPException
from .http_record import HttpRecord
from .kbus import KBusCallError
from .middleware.errors import ErrorMiddleware
from .remote_application import websocket_event, websocket_message
from .session.avatar import Avatar
from .spawner import PARENT_VARIABLE, SPAWNERS, Spawner

if TYPE_CHECKING:
    from .types import Message, Receive, Scope, Send

__all__ = ["KBusMixin"]

#: The lifespan messages after which the server may end the loop.
LIFESPAN_ENDINGS = frozenset({
    "lifespan.startup.failed", "lifespan.shutdown.complete", "lifespan.shutdown.failed"})

#: The name of the server's own member on its dispatcher.
SERVER_MEMBER = "server"


class KBusMixin:
    """KajennBus capability mixin, composed BEFORE a server class.

    Constructor kwargs peeled here: ``role`` — ``application:<code>`` in the
    process that hosts one external application; ``kbus_source`` — the
    configuration the spawned processes load; ``kbus`` — the ``server.kbus``
    options (``address``, ``limits``) of the dispatcher started for the
    external applications.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kbus_role: str | None = kwargs.pop("role", None)
        self.kbus_source: str | None = kwargs.pop("kbus_source", None)
        self.kbus_options: dict[str, Any] = kwargs.pop("kbus", None) or {}
        super().__init__(**kwargs)
        self._role_stop = asyncio.Event()
        self._kbus_member: kbus.Member | None = None
        self._kbus_dispatcher: kbus.Dispatcher | None = None
        self._kbus_address: str | None = None
        self._kbus_socket_dir: str | None = None
        self._kbus_stopping = False
        self._kbus_spawners: dict[str, Spawner] = {}
        self._kbus_pids: dict[str, int] = {}

    @property
    def kbus_member(self) -> kbus.Member | None:
        """This process's member: ``server`` on the server, ``<code>`` in a role process."""
        return self._kbus_member

    @property
    def kbus_dispatcher(self) -> kbus.Dispatcher | None:
        """The dispatcher the external processes join; ``None`` outside its lifespan."""
        return self._kbus_dispatcher

    @property
    def kbus_address(self) -> str | None:
        """The address the dispatcher listens on, once it listens."""
        return self._kbus_address

    @property
    def kbus_socket_dir(self) -> str | None:
        """The private directory of the default unix socket, when one was made."""
        return self._kbus_socket_dir

    @property
    def kbus_stopping(self) -> bool:
        """Whether the external processes are being stopped: no relaunch then."""
        return self._kbus_stopping

    @property
    def kbus_spawners(self) -> dict[str, Spawner]:
        """The spawners started so far, by backend name."""
        return self._kbus_spawners

    @property
    def kbus_pids(self) -> dict[str, int]:
        """The pid of every external process that joined, by application code."""
        return self._kbus_pids

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the lifespan of a server with external applications inside the
        dispatcher's life; every other scope passes straight through."""
        if scope["type"] == "lifespan" and self.external_applications:
            await self._serve_with_children(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    @property
    def external_applications(self) -> dict[str, Any]:
        """The mounts whose application runs in a spawned process, by code."""
        return {code: app for code, app in self.applications.items()
                if getattr(app, "spawner", None) is not None}

    def open_external(self, code: str, message: kbus.Message):
        """Open a stream to the process of the external application ``code``.

        Use it as ``async with``. Raises ``kbus.NoSuchMember`` while that
        process has not joined, and the kbus errors of ``Member.open`` otherwise.
        """
        if code not in self.kbus_pids or self.kbus_member is None:
            raise kbus.NoSuchMember(f"application {code!r} has not joined")
        return self.kbus_member.open(code, message)

    async def _serve_with_children(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Start the dispatcher and the external processes around the base lifespan.

        The dispatcher listens on the ``server.kbus`` address — a unix socket
        in a private directory when none is configured — before any
        application hook runs, and the ``server`` member joins it; each
        external process is ensured with a fresh URL without waiting for it to
        join. A member lost while the server runs, or whose process ends without
        being stopped, is relaunched by its spawner. The processes are stopped
        and the dispatcher closed before ``lifespan.shutdown.complete`` (or a
        ``failed`` message) reaches the server, and when a step of the startup
        fails: the server may end the loop as soon as it reads that message.
        """
        if self.kbus_source is None:
            raise RuntimeError("an external application needs a configuration file or template")
        self._kbus_stopping = False
        dispatcher = self._kbus_dispatcher = kbus.Dispatcher(
            {}, limits=self.kbus_options.get("limits"))
        dispatcher.on_disconnect(self._member_lost)

        async def stopping_send(message: Message) -> None:
            if message["type"] in LIFESPAN_ENDINGS:
                await self._stop_children()
            await send(message)

        try:
            address = self._kbus_listen_address()
            self._kbus_address = await dispatcher.listen(
                address, ssl=self._kbus_server_ssl() if address.startswith("wss://") else None)
            member = kbus.Member(SERVER_MEMBER, secret=secrets.token_hex(32),
                                 handler=self.serve_kbus_call)
            dispatcher.secrets[SERVER_MEMBER] = member.secret
            await member.connect(dispatcher)
            self._kbus_member = member
            for code, app in self.external_applications.items():
                spawner = self.kbus_spawners.get(app.spawner)
                if spawner is None:
                    spawner = self.kbus_spawners[app.spawner] = SPAWNERS[app.spawner](
                        self.kbus_source, shutdown_timeout=self.shutdown_timeout_seconds,
                        on_exit=self._relaunch_role)
                await spawner.ensure(f"application:{code}", url=self._mint_url(code),
                                     environment={})
            await super().__call__(scope, receive, stopping_send)
        finally:
            await self._stop_children()

    def _kbus_server_ssl(self) -> ssl.SSLContext:
        """The TLS context of a ``wss://`` dispatcher: ``certfile`` and ``keyfile``."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.kbus_options["certfile"], self.kbus_options["keyfile"])
        return context

    def _kbus_listen_address(self) -> str:
        """The ``server.kbus`` address, or a unix socket in a fresh 0700 directory."""
        address = self.kbus_options.get("address")
        if address is not None:
            return address
        self._kbus_socket_dir = tempfile.mkdtemp(prefix="kajenn_kbus_")
        os.chmod(self.kbus_socket_dir, 0o700)
        return f"unix://{self.kbus_socket_dir}/kbus.sock"

    async def _stop_children(self) -> None:
        """Stop the processes of the external applications, then close the dispatcher.

        A second call finds nothing left to stop: the spawner forgets a stopped
        role and the dispatcher is dropped once closed.
        """
        self._kbus_stopping = True
        for code, app in self.external_applications.items():
            started = self.kbus_spawners.get(app.spawner)
            if started is not None:
                await started.stop(f"application:{code}")
        self.kbus_pids.clear()
        dispatcher, self._kbus_dispatcher, self._kbus_member = self.kbus_dispatcher, None, None
        if dispatcher is not None:
            await dispatcher.close()
        if self.kbus_socket_dir is not None:
            shutil.rmtree(self.kbus_socket_dir, ignore_errors=True)
            self._kbus_socket_dir = None

    def _mint_url(self, code: str) -> str:
        """A fresh URL for the process of ``code``; the previous token stops admitting."""
        token = secrets.token_hex(32)
        self.kbus_dispatcher.secrets[code] = token
        scheme, _, location = self.kbus_address.partition("://")
        return f"{scheme}://{code}:{token}@{location}"

    async def _member_lost(self, name: str, reason: Exception | None) -> None:
        """Hand a joined external member that disconnected back to its spawner."""
        pid = self.kbus_pids.pop(name, None)
        if pid is not None and not self.kbus_stopping:
            self._relaunch(name, pid=pid)

    def _relaunch_role(self, role: str) -> None:
        """Hand a role whose process ended unrequested back to its spawner."""
        code = role.partition(":")[2]
        self.kbus_pids.pop(code, None)
        self._relaunch(code)

    def _relaunch(self, code: str, pid: int | None = None) -> None:
        """Ask the spawner of ``code`` to ensure it again with a new URL."""
        app = self.external_applications[code]
        self.kbus_spawners[app.spawner].relaunch(
            f"application:{code}", mint=lambda: self._mint_url(code),
            environment={}, pid=pid)

    def _external_joined(self, code: str, meta: dict[str, Any]) -> None:
        """Index the discovery names of a process that joined and record its pid.

        ``meta["well_known"]`` — the names its application answers — replaces
        the names a previous process presented. A value that is not a list of
        non-empty strings is refused with ``kbus.Refused``.
        """
        names = meta.get("well_known")
        if not (isinstance(names, list)
                and all(isinstance(name, str) and name for name in names)):
            raise kbus.Refused(
                f"application {code!r} refused: well_known is not a list of non-empty strings")
        app = self.external_applications[code]
        self.index_well_known(app, names)
        self.kbus_spawners[app.spawner].joined(f"application:{code}")
        self.kbus_pids[code] = meta["pid"]

    async def serve_kbus_call(self, message: kbus.Message, reply: Any) -> None:
        """The handler of the member: an http or websocket stream, ``joined``
        from a process, or a message to serve.

        ``reply`` is a ``kbus.Stream`` for a forwarded http request or
        websocket, ``None`` for a send: the message is served, nothing answers.
        """
        if isinstance(reply, kbus.Stream):
            if message.meta.get("format") == "websocket":
                await self._serve_websocket_stream(message, reply)
            else:
                await self._serve_http_stream(message, reply)
            return
        if message.meta["route"] == "joined":
            self._external_joined(message.meta["from"], message.meta)
            await reply.send(kbus.Message())
            return
        answer = await self.serve_kbus_message(message)
        if reply is not None:
            await reply.send(answer)

    async def run_role(self) -> None:
        """Live as the process of one role: lifespan, join, wait, shutdown.

        No HTTP listener is opened. The URL is popped from
        ``KAJENN_KBUS_PARENT``; the member connects after the hosted
        application's ``on_startup`` returned, then calls ``server.joined``
        with the discovery names that application answers and the pid.
        SIGTERM, SIGINT or the loss of the connection to the server closes the
        member and runs the shutdown.
        """
        scheme, _, rest = os.environ.pop(PARENT_VARIABLE).partition("://")
        credentials, _, location = rest.partition("@")
        code, _, token = credentials.partition(":")
        stop = self._role_stop
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        inbox: asyncio.Queue[Message] = asyncio.Queue()
        outbox: asyncio.Queue[Message] = asyncio.Queue()
        lifespan = asyncio.create_task(
            self({"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
        await inbox.put({"type": "lifespan.startup"})
        started = await outbox.get()
        if started["type"] != "lifespan.startup.complete":
            raise RuntimeError(f"role {self.kbus_role} failed to start: {started}")
        member = kbus.Member(code, secret=token, handler=self.serve_kbus_call,
                             limits=self.kbus_options.get("limits"))
        try:
            await member.connect(f"{scheme}://{location}", ssl=ssl.create_default_context(
                cafile=self.kbus_options.get("cafile")) if scheme == "wss" else None)
            self._kbus_member = member
            await member.call(f"{SERVER_MEMBER}.joined", kbus.Message({
                "well_known": list(self.applications[code].well_known_names),
                "pid": os.getpid()}))
            ended = asyncio.create_task(member.connected.wait_closed())
            stopped = asyncio.create_task(stop.wait())
            await asyncio.wait({ended, stopped}, return_when=asyncio.FIRST_COMPLETED)
            ended.cancel()
            stopped.cancel()
        finally:
            if member.connection is not None:
                await member.close()
            await inbox.put({"type": "lifespan.shutdown"})
            await outbox.get()
            await lifespan

    async def kbus_call(
        self,
        path: str,
        data: Any = None,
        *,
        auth: Avatar | None = None,
        channel: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Call the route at ``path`` with ``data`` and return its decoded answer.

        A status below 400 returns the JSON body (``None`` when empty); any
        other status raises ``KBusCallError`` carrying the status and the body,
        decoded as JSON when it is JSON and as text otherwise. In a role
        process, a call to the server that fails in transport raises
        ``KBusCallError`` with status 503. ``timeout`` bounds the call in
        seconds: past it the call raises ``TimeoutError``.
        """
        message = self._kbus_message(path, data, auth, channel)
        async with asyncio.timeout(timeout):
            if self._goes_to_parent(path):
                try:
                    reply = await self.kbus_member.call(SERVER_MEMBER, message)
                except kbus.Error as failure:
                    raise KBusCallError(path, error=str(failure), status=503) from failure
            else:
                reply = await self.serve_kbus_message(message)
        status = reply.meta["status"]
        if status >= 400:
            try:
                error = json.loads(reply.payload)
            except ValueError:
                error = reply.payload.decode(errors="replace")
            raise KBusCallError(path, error=error, status=status)
        return json.loads(reply.payload) if reply.payload else None

    async def kbus_post(
        self,
        path: str,
        data: Any = None,
        *,
        auth: Avatar | None = None,
        channel: str | None = None,
    ) -> None:
        """Run the route at ``path`` with ``data``: no answer."""
        message = self._kbus_message(path, data, auth, channel)
        if self._goes_to_parent(path):
            await self.kbus_member.send(SERVER_MEMBER, message)
        else:
            await self.serve_kbus_message(message)

    def _goes_to_parent(self, path: str) -> bool:
        """In a role process, a path outside the hosted application goes to the server."""
        if self.kbus_role is None:
            return False
        segment = path.lstrip("/").partition("/")[0]
        return self.application_at(segment) is None

    def _kbus_message(
        self, path: str, data: Any, auth: Avatar | None, channel: str | None
    ) -> kbus.Message:
        """The message of a bus call: JSON payload, ``auth`` and ``channel`` when given."""
        meta: dict[str, Any] = {"format": "json", "path": path}
        if auth is not None:
            meta["auth"] = {"identity": auth.identity, "tags": list(auth.tags)}
        if channel is not None:
            meta["channel"] = channel
        return kbus.Message(meta, json.dumps(data).encode())

    async def serve_kbus_message(self, message: kbus.Message) -> kbus.Message:
        """Serve one message through the demux, as an http request.

        The scope is a ``POST`` of
        ``meta["path"]`` with the JSON payload as body, the avatar rebuilt from
        ``meta["auth"]``, no session, ``"kajenn.kbus": True`` and
        ``"kajenn.channel"`` when ``meta`` carries one. The answer has
        ``meta={"status", "format": "json"}`` and the response body as payload;
        an ``HTTPException`` becomes its status with its detail as a JSON
        string, any other exception a 500.
        """
        meta = message.meta
        auth = meta.get("auth")
        path = meta["path"]
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "auth": Avatar(auth["identity"], auth["tags"]) if auth is not None else None,
            "session": None,
            "kajenn.kbus": True,
        }
        if "channel" in meta:
            scope["kajenn.channel"] = meta["channel"]
        item = self.requests.register(scope)
        try:
            try:
                app, sub_scope = self.demux(scope)
                result = await BufferedAsgiEndpoint(app).serve(sub_scope, message.payload)
                status, body = result["status"], result["body"]
            except HTTPException as refused:
                status, body = refused.status, json.dumps(refused.detail).encode()
            except Exception as failure:
                status = 500
                body = json.dumps(f"{type(failure).__name__}: {failure}").encode()
        finally:
            item.run_cleanups()
            self.requests.unregister(item)
        return kbus.Message({"status": status, "format": "json"}, body)

    async def _serve_http_stream(self, message: kbus.Message, stream: kbus.Stream) -> None:
        """Serve a stream opened with an ``HttpRecord`` and answer on it.

        ``meta["auth"]`` becomes the avatar and ``meta["channel"]`` the scope's
        ``"kajenn.channel"``. The answer is one message ``{"status",
        "headers"}``, one message ``{"more_body"}`` per body chunk with the
        chunk as payload, then the close of this
        direction; a ``WSK`` answer is buffered and adapted first, a raised
        exception answered as the server's ``ErrorMiddleware`` answers it.
        """
        scope, body = HttpRecord().decode_request(message.payload)
        auth = message.meta.get("auth")
        scope["auth"] = Avatar(auth["identity"], auth["tags"]) if auth is not None else None
        scope["session"] = None
        scope["kajenn.kbus"] = True
        if "channel" in message.meta:
            scope["kajenn.channel"] = message.meta["channel"]
        item = self.requests.register(scope)
        try:
            if scope["method"] == "WSK":
                try:
                    app, sub_scope = self.demux(scope)
                    result = await BufferedAsgiEndpoint(app).serve(sub_scope, body)
                except Exception as failure:
                    result = await self._buffered_failure(scope, body, failure)
                await stream.send(kbus.Message(
                    {"status": result["status"], "headers": result["headers"]}))
                await stream.send(kbus.Message({"more_body": False}, result["body"]))
                await stream.close()
                async for _ in stream:
                    pass
                return
            await self._stream_answer(scope, body, stream)
        finally:
            item.run_cleanups()
            self.requests.unregister(item)

    async def _serve_websocket_stream(
        self, message: kbus.Message, stream: kbus.Stream
    ) -> None:
        """Run the ``serve_websocket`` of the demuxed application on ``stream``.

        ``receive`` returns the events the mount forwards, and
        ``websocket.disconnect`` with code 1006 once the stream ends or fails;
        ``send`` forwards each event to the mount. When the application
        returns, this direction closes and the stream is drained until the
        mount closes its own.
        """
        scope = HttpRecord().decode_websocket(message.payload)
        app, target = self.demux(scope)

        async def receive() -> Message:
            try:
                return websocket_event(await anext(stream))
            except (StopAsyncIteration, kbus.Error):
                return {"type": "websocket.disconnect", "code": 1006}

        async def send(event: Message) -> None:
            await stream.send(websocket_message(event))

        await app.serve_websocket(target, receive, send)
        try:
            await stream.close()
            async for _ in stream:
                pass
        except kbus.Error:
            return

    async def _stream_answer(self, scope: Scope, body: bytes, stream: kbus.Stream) -> None:
        """Run the demuxed application with ``send`` bound to ``stream``.

        The other direction of the stream carries no data: its abort, or the
        loss of the link, makes ``receive`` return ``http.disconnect`` and
        cancels the application. An answer that fails after its start, or that
        returns unfinished, aborts the stream.
        """
        requested = False
        closed = False
        left = asyncio.Event()

        async def receive() -> Message:
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": body, "more_body": False}
            await left.wait()
            return {"type": "http.disconnect"}

        async def send(event: Message) -> None:
            nonlocal closed
            if event["type"] == "http.response.start":
                await stream.send(kbus.Message({
                    "status": event["status"],
                    "headers": [[name.decode("latin-1"), value.decode("latin-1")]
                                for name, value in event.get("headers", [])]}))
            elif event["type"] == "http.response.body":
                more_body = event.get("more_body", False)
                await stream.send(kbus.Message({"more_body": more_body}, event.get("body", b"")))
                if not more_body:
                    await stream.close()
                    closed = True

        async def routed(scope: Scope, receive: Receive, send: Send) -> None:
            app, sub_scope = self.demux(scope)
            await app(sub_scope, receive, send)

        answering = asyncio.create_task(ErrorMiddleware(routed, self)(scope, receive, send))

        async def watch() -> None:
            try:
                async for _ in stream:
                    pass
            except kbus.Error:
                left.set()
                answering.cancel()

        watching = asyncio.create_task(watch())
        await asyncio.wait({answering})
        if answering.cancelled():
            await watching
            return
        if answering.exception() is not None or not closed:
            watching.cancel()
            await stream.abort("the answer failed")
            return
        await watching

    async def _buffered_failure(
        self, scope: Scope, body: bytes, failure: Exception
    ) -> dict[str, Any]:
        """The buffered answer of a failed http message, built by ``ErrorMiddleware``.

        The same middleware answers the request in the server's process, so the
        status, the content type and the body follow the request's ``Accept``
        exactly as they would there.
        """
        async def failing(scope: Scope, receive: Receive, send: Send) -> None:
            raise failure

        return await BufferedAsgiEndpoint(ErrorMiddleware(failing, self)).serve(scope, body)
