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

"""The base server: the applications it serves, ASGI dispatch, uvicorn boot.

``BaseServer`` is the common substrate of every server (SPECIFICATION.md §4,
D2): it is composed with its applications (``applications=`` kwarg, a list)
and keeps them in a dict keyed by each app's ``code``, plus a private index by
``mount`` — the one demux mechanism of D3. The set of applications is fixed at
construction; registration is internal. At the base,
``authenticate()`` answers nobody (``None``) and ``session()`` answers none
(``None``). It owns exactly one thread pool (D2): ``run_sync()`` dispatches a
blocking handler onto it via ``loop.run_in_executor`` while async handlers stay
on the loop; the pool is provisioned lazily on first use and torn down at
shutdown.

As an ASGI callable, ``__call__`` dispatches on the scope type: ``http`` runs
the D3 demux — a first path segment starting with a dot is hidden and answers
404 on the spot, ``/.well-known/<declared name>`` excepted; else the app
mounted on that segment, with the segment stripped; else the app on the site
root with the full path; else a 307 from ``/`` to the declared ``default``;
else 404 — ``websocket`` runs
``on_websocket``,
whose DEFAULT is the empty socket of D7 (accepts nothing, closes cleanly with
code 1000); ``lifespan`` runs the ``Lifespan`` handler (ordered startup,
reverse shutdown, error isolation). Each http dispatch is registered in the
``RequestRegistry`` (``requests``) for the span of the request — the current
request and the in-flight picture. ``serve()`` boots uvicorn programmatically.

Cooperative init (D16): peels its own kwargs (``applications``,
``max_threads``) and, as the end of the chain, raises ``TypeError`` naming any
leftover kwargs. Mixins go BEFORE ``BaseServer`` in the MRO.

Ownership channel (one direction): registering an application assigns
``app.server = self``; the app-side setter enforces exactly-once.

**The server learns it is leaving at the signal.** ``serve()`` boots
``UvicornServer``, which owns the SIGINT/SIGTERM handlers: the first signal
turns ``state`` to ``shutdown_mode`` and only then raises uvicorn's own exit
flag, so uvicorn's three steps — close the listeners, wait
``shutdown_timeout_seconds``, send ``lifespan.shutdown`` — all run over a
server that already refuses new work. Whatever the state turn makes true,
``leaving`` (an ``asyncio.Event``) makes awaitable: an endless response races
its source against it through ``get_until_leaving`` and ends by itself instead
of being cancelled when the grace runs out. Under uvicorn's own ``--reload``
the child process builds no ``UvicornServer``, so there the ordered shutdown is
not guaranteed (owner, 2026-09-12: accepted for the stateless single-process
mode).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Iterable

import uvicorn

from .application import BaseApplication
from .lifespan import QUITTING, RUNNING, STOPPING, Lifespan
from .pool import WorkPool
from .request_registry import RequestRegistry
from .response import Response
from .websocket import WebSocket, WebSocketRegistry
from .well_known import HIDDEN_SEGMENT_PREFIX, WELL_KNOWN_ROOT, WELL_KNOWN_SEGMENT
from .wsx_payload import SerializedWsxPayload
from .wsx import WsxConnection, WsxEnvelope

if TYPE_CHECKING:
    from types import FrameType

    from .types import ASGIApp, Receive, Scope, Send

REFUSED_RETRY_AFTER_SECONDS = 5
"""The seconds a refused request is told to come back in."""

WEBSOCKET_MAX_CONCURRENT = 16
#: How long uvicorn waits for open connections at shutdown before cancelling them.
SHUTDOWN_TIMEOUT_SECONDS = 5.0
"""How many messages of ONE websocket connection may be served at once.

A setpoint (owner, 2026-09-06: «configurabile default 16»): the ceiling is what
keeps a client that floods from sinking the server.
"""

__all__ = [
    "QUITTING",
    "REFUSED_RETRY_AFTER_SECONDS",
    "RUNNING",
    "STOPPING",
    "WEBSOCKET_MAX_CONCURRENT",
    "BaseServer",
    "UvicornServer",
]


class UvicornServer(uvicorn.Server):
    """uvicorn's server, with the signal handlers owned by the server it serves.

    Bound to the ``BaseServer`` it runs (dual relationship:
    ``self.base_server``). ``handle_exit`` is what the C-level signal handler
    calls: the state turns FIRST — which is the whole point, uvicorn sends the
    lifespan shutdown only after the graceful wait — and uvicorn's own
    ``handle_exit`` then raises the exit flag (or, on a second SIGINT, the
    force flag) exactly as it would have.
    """

    def __init__(self, base_server: BaseServer, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.base_server = base_server

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Turn the served server's state, then let uvicorn start its shutdown."""
        self.base_server.start_leaving()
        super().handle_exit(sig, frame)


class BaseServer:
    """Base server owning the applications it was composed with.

    Constructor kwargs peeled here: ``applications`` — the applications this
    server serves — ``default`` — the ``code`` of the application ``/``
    redirects to when nothing answers the root (an unknown code raises
    ``ValueError``) — ``max_threads`` — the pool's worker count, handed to
    ``WorkPool`` (``None`` keeps the stdlib default) — ``websocket`` — the
    websocket options, ``{"origins": [...], "max_concurrent": 16}`` — and
    ``shutdown_timeout_seconds`` — how long uvicorn waits for open connections
    to finish before it cancels them at shutdown (5.0). Without a bound, one
    endless response — an SSE stream a client never closes — holds the server
    for ever and the lifespan shutdown never runs (measured 2026-09-08).
    """

    def __init__(self, **kwargs: Any) -> None:
        applications: Iterable[BaseApplication] = kwargs.pop("applications", ())
        default: str | None = kwargs.pop("default", None)
        max_threads: int | None = kwargs.pop("max_threads", None)
        debug: bool | str = kwargs.pop("debug", False)
        websocket: dict[str, Any] = kwargs.pop("websocket", None) or {}
        shutdown_timeout: float = float(
            kwargs.pop("shutdown_timeout_seconds", None) or SHUTDOWN_TIMEOUT_SECONDS
        )
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(
                f"{type(self).__name__}.__init__() got unexpected keyword arguments: {unexpected}"
            )
        super().__init__()
        self._applications: dict[str, BaseApplication] = {}
        self._by_mount: dict[str, BaseApplication] = {}
        self._well_known: dict[str, BaseApplication] = {}
        self._databases: dict[str, Any] = {}
        self._uvicorn: UvicornServer | None = None
        self._pool = WorkPool(self, max_threads=max_threads)
        self._lifespan = Lifespan(self)
        self._registry = RequestRegistry(self)
        self._websockets = WebSocketRegistry()
        self._websocket_origins: list[str] = list(websocket.get("origins") or [])
        self._websocket_max_concurrent: int = int(
            websocket.get("max_concurrent") or WEBSOCKET_MAX_CONCURRENT
        )
        self._shutdown_timeout_seconds = shutdown_timeout
        self._state = RUNNING
        self._leaving = asyncio.Event()
        self.shutdown_mode = STOPPING
        """What ``state`` becomes at the lifespan shutdown when nobody chose first.

        ``STOPPING`` — down dry — unless the trigger declares its exit saves:
        the ``--reload`` launcher sets ``QUITTING`` here, the deliberate command
        will set ``state`` itself before the shutdown arrives.
        """
        self.debug = debug
        """The declared usage mode: False, True, or the parameters it was given.

        A flag and nothing else (owner, 2026-08-25): the core branches on it
        nowhere. It exists so future readers — extra middleware, extra checks —
        can behave differently knowing the server runs in debug.
        """
        for app in applications:
            self.register_application(app)
        self._default = default
        if default is not None and default not in self.applications:
            raise ValueError(f"default names no served application: {default!r}")

    @property
    def applications(self) -> dict[str, BaseApplication]:
        """The served applications keyed by their ``code``."""
        return self._applications

    @property
    def root_application(self) -> BaseApplication | None:
        """The application on the site root (``mount == ""``), ``None`` if there is none.

        It answers ``/`` and every path no other mount claims. A server of
        mounts only has none: then ``/`` redirects to the ``default`` if one is
        declared, and an unclaimed path is a 404.
        """
        return self.application_at("")

    @property
    def default_application(self) -> BaseApplication | None:
        """The application ``/`` redirects to, ``None`` if no ``default`` was declared.

        It elects nothing: the redirect is the whole of its meaning, and it is
        only consulted when no application answers the root.
        """
        return self.applications[self._default] if self._default is not None else None

    def application_at(self, mount: str) -> BaseApplication | None:
        """The application answering under the URL prefix ``mount`` (``None`` if none)."""
        return self._by_mount.get(mount)

    @property
    def well_known_applications(self) -> dict[str, BaseApplication]:
        """The discovery names served under ``/.well-known/``, each with its application."""
        return self._well_known

    def register_application(self, app: BaseApplication) -> None:
        """Register ``app`` under its ``code`` and its ``mount``.

        Assigns the ownership channel (``app.server = self``), then indexes the
        discovery names the app declares (``well_known_names``): a name two
        applications declare belongs to the LAST registered — a fixed rule, no
        option. Internal: the set of applications is fixed at construction, so
        the callers are ``__init__`` and the composition layers building a
        server. A claimed code and a claimed mount both raise ``ValueError``.
        """
        mount = app.code if app.mount is None else app.mount
        if app.code in self.applications:
            raise ValueError(f"application code already claimed: {app.code}")
        if mount in self._by_mount:
            raise ValueError(f"mount already claimed: {mount!r}")
        app.server = self
        for name in app.well_known_names:
            self._well_known[name] = app
        self.applications[app.code] = app
        self._by_mount[mount] = app

    @property
    def databases(self) -> dict[str, Any]:
        """Database handlers keyed by their config ``code`` (may be empty)."""
        return self._databases

    def add_database(self, code: str, handler: Any) -> None:
        """Register ``handler`` under ``code``. A claimed code raises ``ValueError``."""
        if code in self.databases:
            raise ValueError(f"database code already registered: {code}")
        self.databases[code] = handler

    @property
    def lifespan(self) -> Lifespan:
        """The ``Lifespan`` handler managing this server's startup/shutdown."""
        return self._lifespan

    @property
    def pool(self) -> WorkPool:
        """The server's single thread pool for blocking (sync) handlers."""
        return self._pool

    @property
    def requests(self) -> RequestRegistry:
        """The registry of in-flight requests and the current one."""
        return self._registry

    async def run_sync(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Dispatch blocking ``fn`` onto the pool (the app-side sync protocol).

        Apps call ``self.server.run_sync(...)`` for blocking work so it runs
        off the event loop; async handlers simply stay on the loop and never
        touch the pool.
        """
        return await self.pool.run(fn, *args)

    def authenticate(self, request: Any) -> Any:
        """Base answer: nobody (``None``). Auth capabilities override this."""
        return None

    def session(self, request: Any) -> Any:
        """Base answer: none (``None``). Session capabilities override this."""
        return None

    def get_middleware(self, middleware_class: type) -> Any:
        """Base answer: none (``None``). The middleware capability overrides this."""
        return None

    @property
    def websockets(self) -> WebSocketRegistry:
        """The live websockets, and which one each page speaks on."""
        return self._websockets

    async def send_message(self, page_id: str, path: str, data: Any = None) -> bool:
        """Write one message of the server's own onto the socket a page speaks on.

        Args:
            page_id: the page to address.
            path: what the client routes the message on, the way this server
                routes what the client sends.
            data: the payload, as a Python value.

        Returns:
            ``True`` when the message was written to a socket, ``False`` when
            that page speaks on none or its socket is already closed.

        The message has the shape of a request and carries NO ``id``: it is not
        an answer, and nobody answers it — a page that wants to reply sends an
        rpc of its own, on the ``reply_path`` it asked for or on a path of its
        choosing. DELIVERED means written to the socket, never executed by the
        page: nothing here waits for anything.
        """
        socket = self.websockets.get_page_socket(page_id)
        if socket is None or not socket.connected:
            return False
        envelope = WsxEnvelope(method="WSK", path=path, data=data, page_id=page_id)
        await socket.send_text(envelope.encode())
        return True

    async def send_serialized_message(
        self, page_id: str, path: str, payload: SerializedWsxPayload
    ) -> bool:
        """Forward an explicitly serialized application value to its page."""
        socket = self.websockets.get_page_socket(page_id)
        if socket is None or not socket.connected:
            return False
        await socket.send_text(WsxEnvelope(method="WSK", path=path,
                                          serialized_data=payload, page_id=page_id).encode())
        return True

    @property
    def websocket_origins(self) -> list[str]:
        """The Origins a handshake may come from; empty means same-origin only."""
        return self._websocket_origins

    @property
    def websocket_max_concurrent(self) -> int:
        """How many messages of ONE connection may be in flight at once."""
        return self._websocket_max_concurrent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point: dispatch on the scope type.

        ``http`` runs the D3 demux (registering the request in ``requests`` for
        the span of the dispatch); ``websocket`` runs ``on_websocket`` (the
        empty socket by default); ``lifespan`` runs the ``Lifespan`` handler.
        Any other type is an ASGI protocol error.

        ``state`` is read FIRST: anything but ``RUNNING`` takes nothing new in
        charge, and this branch renders the refusal the way HTTP says it — 503
        and ``Retry-After``. Another transport reads the same state and renders
        its own. Whatever the middleware chain answers by itself never reaches
        here, so it is neither registered nor refused.
        """
        scope_type = scope["type"]
        if scope_type == "http":
            if self.state != RUNNING:
                await Response(
                    content="Server restarting",
                    status_code=503,
                    media_type="text/plain",
                    headers={"retry-after": str(REFUSED_RETRY_AFTER_SECONDS)},
                )(scope, receive, send)
                return
            item = self.requests.register(scope)
            try:
                app, target = self.demux(scope)
                await app(target, receive, send)
            finally:
                item.run_cleanups()
                self.requests.unregister(item)
        elif scope_type == "websocket":
            await self.on_websocket(scope, receive, send)
        elif scope_type == "lifespan":
            await self.lifespan(scope, receive, send)
            # The lifespan handler returns once shutdown is acked; tear the
            # pool down here (a no-op unless a sync dispatch provisioned it).
            self.pool.shutdown(wait=True)
        else:
            raise ValueError(f"unsupported ASGI scope type: {scope_type}")

    def demux(self, scope: Scope) -> tuple[ASGIApp, Scope]:
        """D3 demux: pick what answers an http scope, and the scope it receives.

        One rule, four branches: the first path segment matching a mount → that
        app, with the segment stripped from ``path`` (the forwarded path is
        rebuilt from the same remainder used to find the segment, so ``//api/x``
        forwards ``/x``); else the application on the site root, with the full
        path unchanged; else, for ``/`` itself with a ``default`` declared, a
        **307** to that application's mount carrying the query string over;
        else **404**. ``/`` on a server WITH a root application matches its
        empty mount in the first branch, which forwards the same ``/``.

        The hidden paths come first (#88): a first segment starting with a dot
        never takes those branches, and the rule is the server's own — always
        on, no middleware, no switch. A path without a dot is ordinary, the
        conventional probes included (``/favicon.ico``, ``/robots.txt``): the
        server silences none of them.
        """
        path = scope["path"]
        rest = path.lstrip("/")
        segment, _, remainder = rest.partition("/")
        if segment.startswith(HIDDEN_SEGMENT_PREFIX):
            return self.demux_hidden(segment, remainder, scope)
        app = self.application_at(segment)
        if app is not None:
            sub_scope = dict(scope)
            sub_scope["path"] = "/" + remainder
            return app, sub_scope
        root = self.root_application
        if root is not None:
            return root, scope
        default = self.default_application if not rest else None
        if default is not None:
            return self.redirect_to_default(default, scope), scope
        return Response(content="Not Found", status_code=404, media_type="text/plain"), scope

    def demux_hidden(self, segment: str, remainder: str, scope: Scope) -> tuple[ASGIApp, Scope]:
        """What serves a hidden first segment: a declared document, else **404**.

        Nothing of a site lives under a dotted segment — ``/.git/config``,
        ``/.env`` — so the answer is 404 and no application is reached: not a
        mount, not the root application, not the ``default`` redirect. The one
        exception is RFC 8615: when ``segment`` is ``.well-known`` and the
        first segment of ``remainder`` is a name some application declared at
        mount time, the request goes to that application rebuilt as
        ``/_well_known/<name>/<rest>``, the path its own router already
        resolves. No translation is invented.
        """
        if segment == WELL_KNOWN_SEGMENT:
            app = self.well_known_applications.get(remainder.partition("/")[0])
            if app is not None:
                sub_scope = dict(scope)
                sub_scope["path"] = f"/{WELL_KNOWN_ROOT}/{remainder}"
                return app, sub_scope
        return Response(content="Not Found", status_code=404, media_type="text/plain"), scope

    def redirect_to_default(self, app: BaseApplication, scope: Scope) -> Response:
        """A 307 to ``app``'s mount, preserving the query string.

        307 and not 301/302: the method and the body must survive the hop, so a
        ``POST /`` reaches the default application as a POST.
        """
        location = f"/{app.mount}/"
        query = scope.get("query_string", b"")
        if query:
            location = f"{location}?{query.decode('latin-1')}"
        return Response(status_code=307, headers={"location": location})

    async def on_websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Live one websocket connection: the motor, or the application's own hands.

        One ``WsxConnection`` per socket does the whole thing (#68): it judges
        the handshake, accepts it, turns every message into a request the demux
        routes like any other, and answers the ones that carry an ``id``. The
        connection is registered in ``websockets`` for its whole life.

        The state is judged FIRST, above the demux, for every websocket: a
        server that is not ``RUNNING`` takes no new connection in charge, and
        the handshake is turned away before the accept. The browser sees the
        handshake fail with no readable code — 1013 exists only after an accept,
        and in the raw mode the accept belongs to the application — but the
        state is the machine's business and not the protocol's, exactly as it
        is on the http branch.

        The exception is an application that wants the socket ITSELF: the
        handshake's path names it through the same demux, and if it defines
        ``serve_websocket`` it is handed the raw scope, receive and send, with
        the segment of its mount already taken off the path. Nothing else of the
        motor runs then — no accept, no Origin gate, no registry: an application
        that takes the socket takes all of it, and the core does not half-serve
        a connection it does not hold. It is the admitted mode of the design,
        the one a hosted framework with a websocket protocol of its own reaches
        the server by.
        """
        if self.state != RUNNING:
            await WebSocket(scope, receive, send).refuse(1013, "server restarting")
            return
        app, target = self.demux(scope)
        raw_seam = getattr(app, "serve_websocket", None)
        if raw_seam is not None:
            await raw_seam(target, receive, send)
            return
        await WsxConnection(self, scope, receive, send).serve()

    @property
    def state(self) -> str:
        """``RUNNING``, ``QUITTING`` or ``STOPPING`` — read by the entry point."""
        return self._state

    @state.setter
    def state(self, state: str) -> None:
        """Write the state, and set ``leaving`` the moment it is no longer RUNNING."""
        self._state = state
        if state != RUNNING:
            self._leaving.set()

    @property
    def leaving(self) -> asyncio.Event:
        """Set once the state has left RUNNING, and never cleared again.

        What an endless response watches: the state alone says nothing to
        somebody parked on a queue, and at the signal there are seconds, not
        minutes, before uvicorn cancels whoever is still there.
        """
        return self._leaving

    def start_leaving(self) -> None:
        """Turn the state to ``shutdown_mode``; a state somebody chose is left alone."""
        if self.state == RUNNING:
            self.state = self.shutdown_mode

    async def get_until_leaving(self, queue: asyncio.Queue[Any]) -> Any | None:
        """The queue's next item, or ``None`` as soon as the server starts leaving.

        The way a source of an endless response reads its feed: whichever of the
        two comes first wins and the other is cancelled, so nothing is left
        pending behind the caller.
        """
        item = asyncio.ensure_future(queue.get())
        leaving = asyncio.ensure_future(self._leaving.wait())
        try:
            await asyncio.wait({item, leaving}, return_when=asyncio.FIRST_COMPLETED)
            return item.result() if item.done() else None
        finally:
            for pending in (item, leaving):
                pending.cancel()

    @property
    def shutdown_timeout_seconds(self) -> float:
        """How long uvicorn waits for open connections at shutdown before cancelling them."""
        return self._shutdown_timeout_seconds

    @property
    def uvicorn_server(self) -> UvicornServer | None:
        """The ``UvicornServer`` once ``serve()`` has built it (else ``None``).

        Callers that boot the server in a background thread read the bound port
        from ``uvicorn_server.servers[0].sockets[0].getsockname()`` after
        ``uvicorn_server.started`` turns true.
        """
        return self._uvicorn

    def serve(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Boot uvicorn programmatically, serving this server (blocking).

        Builds ``uvicorn.Config``/``UvicornServer`` — the subclass owning the
        signal handlers, so the state leaves RUNNING at the signal and not at
        the end of the graceful wait — and runs it. ``port=0``
        lets the OS assign an ephemeral port, discoverable via
        ``uvicorn_server`` once started. ``shutdown_timeout_seconds`` bounds
        uvicorn's wait for open connections, so a response that never ends
        cannot keep the lifespan shutdown — and the applications' own stop —
        from running.
        """
        self._uvicorn = UvicornServer(
            self,
            uvicorn.Config(
                self,
                host=host,
                port=port,
                timeout_graceful_shutdown=self.shutdown_timeout_seconds,
            ),
        )
        self._uvicorn.run()
