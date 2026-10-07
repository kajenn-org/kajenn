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

The base server is born WITHOUT a KajennBus side. This mixin adds the communication
capability as member objects built by its cooperative ``__init__``:
``parent_kbus`` — the ``KBusClient`` of ◆D10, ARMED iff
``parent=<address>`` was given — and ``children_kbus`` — the ``KBusHub``
this mixin starts in the lifespan when the configuration declares external
applications (``spawner=``). Accessing an unarmed side raises
``RuntimeError`` ("not armed"); a composition WITHOUT the mixin simply lacks
the attributes — a different type, not a ghost. A server with ``parent=``
that is not a role cannot declare external applications.

This is the first REAL proof of the D16 cooperative contract: composed
BEFORE the server class (``class MyServer(KBusMixin, BaseServer)``)
the mixin peels its own kwargs, forwards the rest, and hooks the lifespan
without the base knowing it — ``__call__`` cooperatively intercepts the
``lifespan`` scope: an armed parent side pre-receives ``lifespan.startup``,
connects (and REGISTERs) BEFORE any app hook runs, and disconnects when the
protocol completes at shutdown. An unreachable hub answers
``lifespan.startup.failed`` and re-raises: a child that cannot register must
die, never serve detached.

Registration makes the registrant a child in the tree even when not spawned
by us (communication ≠ process lifecycle, D17): the REGISTER frame presents
``{"name", "pid"}``; the name is derived from the class name and the pid
(child naming proper belongs to the orchestration package).

A route is reachable through the bus as well as through HTTP:
``kbus_call(path, data)`` builds a CALL frame and hands it to
``serve_kbus_frame``, the one place a frame becomes an ASGI scope. In the
server's own process no stream is involved, and the data still crosses as
JSON bytes, so nothing the handler does to its arguments reaches the caller.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import signal
from typing import TYPE_CHECKING, Any

from .asgi_endpoint import BufferedAsgiEndpoint
from .exceptions import HTTPException
from .http_record import HttpRecord
from .kbus import (
    CALL_METHOD,
    EVENT_METHOD,
    REPLY_METHOD,
    Frame,
    KBusCallError,
    KBusClient,
    KBusHub,
)
from .kbus.address import KBusAddress
from .kbus.spawner import SPAWNERS, KBusSpawner
from .middleware.errors import ErrorMiddleware
from .session.avatar import Avatar

if TYPE_CHECKING:
    from .types import Message, Receive, Scope, Send

__all__ = ["KBusMixin"]

#: The lifespan messages after which the server may end the loop.
LIFESPAN_ENDINGS = frozenset({
    "lifespan.startup.failed", "lifespan.shutdown.complete", "lifespan.shutdown.failed"})


class KBusMixin:
    """KajennBus capability mixin, composed BEFORE a server class.

    Constructor kwargs peeled here: ``parent`` — the address of the parent
    hub (``uds:<path>`` | ``tcp:<host>:<port>``); when given, the parent
    side is armed with a ``KBusClient``. ``kbus`` — the ``server.kbus``
    options (``address``, ``secret``) of the hub started for the external
    applications.
    """

    def __init__(self, **kwargs: Any) -> None:
        parent: str | None = kwargs.pop("parent", None)
        self.kbus_role: str | None = kwargs.pop("role", None)
        self.kbus_source: str | None = kwargs.pop("kbus_source", None)
        self.kbus_options: dict[str, Any] = kwargs.pop("kbus", None) or {}
        super().__init__(**kwargs)
        self._parent_kbus: KBusClient | None = None
        self._role_stop = asyncio.Event()
        if parent is not None and self.kbus_role is not None:
            code = self.kbus_role.partition(":")[2]
            self._parent_kbus = KBusClient(
                parent, code, on_call=self.serve_kbus_frame,
                on_orphan=lambda _client: self._role_stop.set(),
                secret=os.environ.pop("KAJENN_KBUS_SECRET", None),
                presentation={"role": self.kbus_role,
                              "token": os.environ.pop("KAJENN_KBUS_TOKEN", "")})
        elif parent is not None:
            if self.external_applications:
                raise ValueError(
                    f"parent={parent!r} and an external application "
                    f"({', '.join(self.external_applications)}) cannot be combined: "
                    "a server with a parent hub does not start a hub of its own")
            name = f"{type(self).__name__.lower()}-{os.getpid()}"
            self._parent_kbus = KBusClient(parent, name)
        self._children_kbus: Any = None
        self._kbus_spawners: dict[str, KBusSpawner] = {}
        self._kbus_tokens: dict[str, str] = {}

    @property
    def parent_armed(self) -> bool:
        """Whether the parent side was armed (``parent=`` given at init)."""
        return self._parent_kbus is not None

    @property
    def parent_kbus(self) -> KBusClient:
        """The KajennBus to the parent hub; unarmed access is an error."""
        if self._parent_kbus is None:
            raise RuntimeError("parent_kbus is not armed (no parent= at init)")
        return self._parent_kbus

    @property
    def children_kbus(self) -> Any:
        """The ``KBusHub`` started in the lifespan when external applications exist."""
        if self._children_kbus is None:
            raise RuntimeError(
                "children_kbus is not armed (the hub starts in the lifespan "
                "of a server that declares external applications)"
            )
        return self._children_kbus

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Hook the lifespan without the base knowing it (the D16 proof).

        An armed parent side pre-receives ``lifespan.startup`` (replayed to
        the base handler so the protocol stays intact), connects (and
        REGISTERs) before any app hook runs, and disconnects when the
        protocol completes at shutdown. An unreachable hub answers
        ``lifespan.startup.failed`` and re-raises the ``ConnectionError`` —
        the child dies instead of serving detached. Every other scope passes
        straight through.
        """
        if scope["type"] == "lifespan" and self.external_applications:
            await self._serve_with_children(scope, receive, send)
            return
        if scope["type"] != "lifespan" or not self.parent_armed or self.kbus_role is not None:
            await super().__call__(scope, receive, send)
            return
        startup: Message = await receive()
        try:
            await self.parent_kbus.connect()
        except ConnectionError as error:
            await send({"type": "lifespan.startup.failed", "message": str(error)})
            raise
        replayed = False

        async def replaying_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return startup
            return await receive()

        try:
            await super().__call__(scope, replaying_receive, send)
        finally:
            await self.parent_kbus.close()

    @property
    def external_applications(self) -> dict[str, Any]:
        """The mounts whose application runs in a spawned process, by code."""
        return {code: app for code, app in self.applications.items()
                if getattr(app, "spawner", None) is not None}

    async def _serve_with_children(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Start the hub and the external processes around the base lifespan.

        The hub binds the ``server.kbus`` address — a uds socket in a private
        directory when none is configured — before any application hook runs;
        each external application gets a fresh token and its process is
        ensured without waiting for its REGISTER. A member lost while the
        server runs, or whose process ends without being stopped, is relaunched
        by its spawner. The processes already ensured are stopped and the hub
        closed before ``lifespan.shutdown.complete`` (or a ``failed`` message)
        reaches the server, and when a step of the startup fails: the server
        may end the loop as soon as it reads that message.
        """
        if self.kbus_source is None:
            raise RuntimeError("an external application needs a configuration file or template")
        hub = self._build_hub()
        await hub.start()
        self._children_kbus = hub

        async def stopping_send(message: Message) -> None:
            if message["type"] in LIFESPAN_ENDINGS:
                await self._stop_children(hub)
            await send(message)

        try:
            for code, app in self.external_applications.items():
                spawner = self._kbus_spawners.get(app.spawner)
                if spawner is None:
                    spawner = self._kbus_spawners[app.spawner] = SPAWNERS[app.spawner](
                        self.kbus_source, hub.address,
                        shutdown_timeout=self.shutdown_timeout_seconds,
                        on_exit=self._relaunch_role)
                await spawner.ensure(f"application:{code}", token=self._mint_token(code),
                                     environment=self._spawn_environment)
            await super().__call__(scope, receive, stopping_send)
        finally:
            await self._stop_children(hub)

    async def _stop_children(self, hub: KBusHub) -> None:
        """Stop the processes of the external applications, then close ``hub``.

        A second call finds nothing left to stop: the spawner forgets a stopped
        role and a stopped hub returns at once.
        """
        for code, app in self.external_applications.items():
            started = self._kbus_spawners.get(app.spawner)
            if started is not None:
                await started.stop(f"application:{code}")
        await hub.stop()

    def _build_hub(self) -> KBusHub:
        """The hub on the ``server.kbus`` address, admitting the external members."""
        options: dict[str, Any] = {"secret": self.kbus_options.get("secret")}
        address = self.kbus_options.get("address")
        if address is not None:
            location = KBusAddress(address, allow_network_listener=True)
            options.update(path=location.path, host=location.host, port=location.port or 0)
        return KBusHub(on_member_joined=self._admit_member,
                       on_member_lost=self._relaunch_member,
                       on_call=lambda member, frame: self.serve_kbus_frame(frame),
                       on_event=lambda member, frame: self.serve_kbus_frame(frame),
                       **options)

    @property
    def _spawn_environment(self) -> dict[str, str]:
        """What a spawned process receives besides its token: the hub's secret."""
        secret = self.kbus_options.get("secret")
        return {"KAJENN_KBUS_SECRET": secret} if secret else {}

    def _mint_token(self, code: str) -> str:
        """A fresh token for the process of ``code``; the previous one stops admitting."""
        self._kbus_tokens[code] = secrets.token_hex(32)
        return self._kbus_tokens[code]

    def _relaunch_member(self, member: Any) -> None:
        """Hand a lost external member back to the spawner that started it."""
        if member.name in self.external_applications:
            self._relaunch(member.name, pid=member.pid)

    def _relaunch_role(self, role: str) -> None:
        """Hand a role whose process ended unrequested back to its spawner."""
        self._relaunch(role.partition(":")[2])

    def _relaunch(self, code: str, pid: int | None = None) -> None:
        """Ask the spawner of ``code`` to ensure it again with a new token."""
        app = self.external_applications[code]
        self._kbus_spawners[app.spawner].relaunch(
            f"application:{code}", mint=lambda: self._mint_token(code),
            environment=self._spawn_environment, pid=pid)

    def _admit_member(self, member: Any) -> None:
        """Refuse a member that is not an external application with its token.

        An admitted member's ``well_known`` — the discovery names its
        application answers, absent meaning none — is indexed on the mount of
        that application, replacing the names a previous process presented. A
        value that is not a list of non-empty strings refuses the member.
        """
        token = self._kbus_tokens.get(member.name)
        presented = member.presentation.get("token")
        if token is None or not (
            isinstance(presented, str) and hmac.compare_digest(presented.encode(), token.encode())
        ):
            raise PermissionError(f"member {member.name!r} refused")
        names = member.presentation.get("well_known", [])
        if not (isinstance(names, list)
                and all(isinstance(name, str) and name for name in names)):
            raise PermissionError(
                f"member {member.name!r} refused: well_known is not a list of non-empty strings")
        app = self.external_applications[member.name]
        self.index_well_known(app, names)
        self._kbus_spawners[app.spawner].joined(f"application:{member.name}")

    async def run_role(self) -> None:
        """Live as the process of one role: lifespan, REGISTER, wait, shutdown.

        No HTTP listener is opened. The REGISTER goes to the parent hub after
        the hosted application's ``on_startup`` returned and presents, as
        ``well_known``, the discovery names that application answers; SIGTERM, SIGINT or
        the loss of the parent link closes the link and runs the shutdown.
        """
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
        hosted = self.applications[self.kbus_role.partition(":")[2]]
        self.parent_kbus.presentation["well_known"] = list(hosted.well_known_names)
        try:
            await self.parent_kbus.connect()
            await stop.wait()
        finally:
            await self.parent_kbus.close()
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
        decoded as JSON when it is JSON and as text otherwise. A REPLY carrying
        ``info["error"]`` raises ``KBusCallError`` with that error and no
        status. ``timeout`` bounds the call in seconds: past it the call raises
        ``TimeoutError``.
        """
        frame = self._kbus_frame(CALL_METHOD, path, data, auth, channel)
        async with asyncio.timeout(timeout):
            if self._goes_to_parent(path):
                reply = await self.parent_kbus.call_frame(frame)
            else:
                reply = await self.serve_kbus_frame(frame)
        if "error" in reply.info:
            raise KBusCallError(path, error=reply.info["error"], status=None)
        status = reply.info["status"]
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
        """Run the route at ``path`` with ``data`` as an EVENT: no answer."""
        frame = self._kbus_frame(EVENT_METHOD, path, data, auth, channel)
        if self._goes_to_parent(path):
            await self.parent_kbus.link().post(frame)
        else:
            await self.serve_kbus_frame(frame)

    def _goes_to_parent(self, path: str) -> bool:
        """In a role process, a path outside the hosted application goes up."""
        if self.kbus_role is None:
            return False
        segment = path.lstrip("/").partition("/")[0]
        return self.application_at(segment) is None

    def _kbus_frame(
        self, method: str, path: str, data: Any, auth: Avatar | None, channel: str | None
    ) -> Frame:
        """The frame of a bus call: JSON payload, ``auth`` and ``channel`` when given."""
        info: dict[str, Any] = {"format": "json"}
        if auth is not None:
            info["auth"] = {"identity": auth.identity, "tags": list(auth.tags)}
        if channel is not None:
            info["channel"] = channel
        return Frame(method=method, path=path, info=info, payload=json.dumps(data).encode())

    async def serve_kbus_frame(self, frame: Frame) -> Frame | None:
        """Serve one CALL or EVENT frame through the demux, as an http request.

        The scope is a ``POST`` with a JSON body, the avatar rebuilt from
        ``info["auth"]``, no session, ``"kajenn.kbus": True`` and
        ``"kajenn.channel"`` when ``info`` carries one. A CALL returns a
        REPLY with ``info={"status", "format": "json"}`` and the response body
        as payload; an ``HTTPException`` becomes its status with its detail as
        a JSON string, any other exception a 500. An EVENT returns ``None``.
        """
        info = frame.info
        auth = info.get("auth")
        if info.get("format") == "http":
            return await self._serve_http_frame(frame)
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": frame.path,
            "raw_path": frame.path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "auth": Avatar(auth["identity"], auth["tags"]) if auth is not None else None,
            "session": None,
            "kajenn.kbus": True,
        }
        if "channel" in info:
            scope["kajenn.channel"] = info["channel"]
        item = self.requests.register(scope)
        try:
            try:
                app, sub_scope = self.demux(scope)
                result = await BufferedAsgiEndpoint(app).serve(sub_scope, frame.payload)
                status, body = result["status"], result["body"]
            except HTTPException as refused:
                status, body = refused.status, json.dumps(refused.detail).encode()
            except Exception as failure:
                status = 500
                body = json.dumps(f"{type(failure).__name__}: {failure}").encode()
        finally:
            item.run_cleanups()
            self.requests.unregister(item)
        if frame.method != CALL_METHOD:
            return None
        return Frame(
            id=frame.id,
            method=REPLY_METHOD,
            path=frame.path,
            info={"status": status, "format": "json"},
            payload=body,
        )

    async def _serve_http_frame(self, frame: Frame) -> Frame | None:
        """Serve a CALL carrying an ``HttpRecord`` and answer an ``HttpRecord``.

        ``info["auth"]`` becomes the avatar and ``info["channel"]`` the scope's
        ``"kajenn.channel"``. A raised exception is answered by
        ``_buffered_failure``, as the server's ``ErrorMiddleware`` answers it.
        """
        scope, body = HttpRecord().decode_request(frame.payload)
        auth = frame.info.get("auth")
        scope["auth"] = Avatar(auth["identity"], auth["tags"]) if auth is not None else None
        scope["session"] = None
        scope["kajenn.kbus"] = True
        if "channel" in frame.info:
            scope["kajenn.channel"] = frame.info["channel"]
        item = self.requests.register(scope)
        try:
            try:
                app, sub_scope = self.demux(scope)
                result = await BufferedAsgiEndpoint(app).serve(sub_scope, body)
            except Exception as failure:
                result = await self._buffered_failure(scope, body, failure)
        finally:
            item.run_cleanups()
            self.requests.unregister(item)
        if frame.method != CALL_METHOD:
            return None
        return Frame(
            id=frame.id,
            method=REPLY_METHOD,
            path=frame.path,
            info={"format": "http", "status": result["status"]},
            payload=HttpRecord().encode_response(result),
        )

    async def _buffered_failure(
        self, scope: Scope, body: bytes, failure: Exception
    ) -> dict[str, Any]:
        """The buffered answer of a failed http frame, built by ``ErrorMiddleware``.

        The same middleware answers the request in the server's process, so the
        status, the content type and the body follow the request's ``Accept``
        exactly as they would there.
        """
        async def failing(scope: Scope, receive: Receive, send: Send) -> None:
            raise failure

        return await BufferedAsgiEndpoint(ErrorMiddleware(failing, self)).serve(scope, body)
