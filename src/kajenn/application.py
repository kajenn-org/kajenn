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

"""App-side contract: the base class every mountable application extends.

``BaseApplication`` is what the server requires of an app (SPECIFICATION.md
§4, D7): an ASGI callable (``__call__`` implemented by concrete subclasses)
with an identity (``code``) and a placement (``mount``), a ``server``
property assigned exactly once by the owning server at attach time (ownership
channel, one direction — a second assignment raises ``RuntimeError``), and
lifecycle hooks ``on_startup``/``on_shutdown`` that subclasses may override
as sync OR async (the caller detects which at call time).

An application is a triplet **code + instance + mount**. ``code`` names it
(the key of ``server.applications``); ``mount`` is the URL prefix it answers
under, and ``""`` is the site root — a legitimate value, never a "missing"
one. Both are class attributes a subclass sets declaratively and a
constructor kwarg overrides per instance, so the same class can be installed
twice under different codes:

.. code-block:: python

    class Shop(RoutedApplication):
        mount = ""          # this app is a site root by design

    Shop(code="outlet", mount="outlet")

Cooperative init (D16): every class in the family implements
``__init__(self, **kwargs)``, peels ITS OWN kwargs and forwards the rest via
``super().__init__(**rest)``. Mixins go BEFORE the base in the MRO; this base
is the end of the chain and raises ``TypeError`` naming any leftover kwargs.

Every application also carries its own CONFIGURATION GRAMMAR as the class
attribute ``grammar``, inherited by MRO like ``code``/``mount``: the site recipe
mounts it at the ``application(app_class=...)`` line (subbuilder by reference),
so the app declares its own vocabulary and the site dialect never validates it.
``ApplicationGrammar`` is the minimal one every app inherits — a single
``parameters`` element for free options — and a richer app subclasses it.

An app READS that subtree back through ``self.config(path)``, which prefixes
``applications.<code>.`` and delegates to the server's own read door: the app
never holds a slice of the tree, only an address in it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_builders.builder import element

if TYPE_CHECKING:
    from .server import BaseServer
    from .types import Receive, Scope, Send

__all__ = ["ApplicationGrammar", "BaseApplication"]

_MISSING = object()

BODY_MODES: dict[str, bool] = {"decoded": False, "raw": True}
"""The ``request(body=...)`` words, as what they decide: raw bytes or decoded."""

ERROR_CODES: dict[str, int] = {"strict": 400, "fastapi": 422}
"""The ``request(error_codes=...)`` words, as the status a rejected value answers."""


class ApplicationGrammar:
    """The configuration grammar every application inherits.

    Two elements: ``parameters``, for the free options a plain app needs — an
    application with nothing of its own still has a mountable grammar (an EMPTY
    grammar class is rejected by builders) — and ``request``, the two options
    every application has about the requests the core serves for it. A richer
    app subclasses this to add its own vocabulary.
    """

    @element(node_label="parameters")
    def parameters(self, **options: Any) -> None:
        """Free application options, read back as
        ``applications.<code>.parameters.<name>``."""

    @element(node_label="request")
    def request(self, body: str = "decoded", error_codes: str = "strict") -> None:
        """How this application takes a request body, and which codes it answers.

        ``body`` is ``"decoded"`` (the default: the core decodes by content-type
        and the fields become handler kwargs) or ``"raw"`` (the handler receives
        the bytes as ``body_raw`` and decodes them itself — the core's decode
        helpers on ``Request`` stay public for exactly that).

        ``error_codes`` is ``"strict"`` (the default: a value rejected by
        validation is a 400 like every other failure the core judges — 422
        belongs to the handler, for a domain rule) or ``"fastapi"`` (that one
        case answers 422, the convention a FastAPI client expects).
        """


class BaseApplication:
    """Base class for applications attached to a ``BaseServer``.

    Constructor kwargs peeled here: ``code`` — the application's identity,
    empty meaning the class name lowercased — and ``mount`` — the URL prefix
    it answers under, ``None`` meaning the same as the code. Both default to
    the class attributes below, so a subclass can set them declaratively.
    """

    code: str = ""
    mount: str | None = None
    grammar: type = ApplicationGrammar

    def __init__(self, **kwargs: Any) -> None:
        cls = type(self)
        code: str = kwargs.pop("code", cls.code) or cls.__name__.lower()
        mount: str | None = kwargs.pop("mount", cls.mount)
        self.code = code
        # ``is None`` and never truthiness: ``mount=""`` IS the site root, and
        # ``mount or code`` would silently move a root app to ``/code``.
        self.mount = code if mount is None else mount
        self._server: BaseServer | None = None
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(
                f"{type(self).__name__}.__init__() got unexpected keyword arguments: {unexpected}"
            )
        super().__init__()

    @property
    def server(self) -> BaseServer | None:
        """The server that owns this app (``None`` until attached)."""
        return self._server

    @server.setter
    def server(self, value: BaseServer) -> None:
        """Assign the owning server once; a second assignment raises ``RuntimeError``."""
        if self._server is not None:
            raise RuntimeError(f"{type(self).__name__} is already owned by a server")
        self._server = value

    def config(self, path: str, default: Any = _MISSING) -> Any:
        """Read this application's own configuration through the server's read door.

        Paths are relative to the application: ``self.config("parameters.title")``
        reads ``applications.<code>.parameters.title``, so an app addresses its
        own mounted subtree and never a slice of someone else's. The four-layer
        read stack (written value → signature default → call-site ``default`` →
        noisy ``KeyError``) is the handler's, untouched.

        With nothing to read — no server, or a server built bare — the call-site
        ``default`` answers, and its absence raises the same noisy ``KeyError``.
        """
        full_path = f"applications.{self.code}.{path}"
        handler = getattr(self.server, "config", None)
        if handler is None:
            if default is _MISSING:
                raise KeyError(
                    f"missing config value '{full_path}': "
                    f"{type(self).__name__} is not attached to a configured server"
                )
            return default
        if default is _MISSING:
            return handler(full_path)
        return handler(full_path, default=default)

    @property
    def raw_body(self) -> bool:
        """True when this application takes the request body bytes untouched.

        Read from the configuration and nowhere else: the word is
        ``request(body="raw")`` under ``applications.<code>``, written by the
        site recipe or by the shortcut that builds one. The default,
        ``"decoded"``, is the grammar element's own and leaves the decoding to
        the core; a word the map does not know raises a noisy ``KeyError`` at
        the first request, naming what was written.
        """
        return BODY_MODES[self.config("request.body", default="decoded")]

    @property
    def validation_error_status(self) -> int:
        """The status a value rejected by validation answers: 400, or 422.

        Read from the configuration and nowhere else: the word is
        ``request(error_codes="fastapi")`` under ``applications.<code>``. The
        default, ``"strict"``, answers 400 like every other failure the core
        judges.
        """
        return ERROR_CODES[self.config("request.error_codes", default="strict")]

    @property
    def handshake_cookie(self) -> str | None:
        """The cookie a websocket handshake must carry to reach this application.

        Returns:
            The cookie's name, or ``None`` — this application gates nothing at
            the handshake, which is the base answer.

        The path of a handshake names its home application, and the server asks
        THAT application whether a connection is admissible before accepting
        one. An application whose every message is a request of a known user
        names its cookie here, and a handshake without it is accepted and
        closed with 1008, so the browser reads why.
        """
        return None

    @property
    def well_known_names(self) -> tuple[str, ...]:
        """The discovery documents this application answers under ``/.well-known/``.

        Returns:
            The names, empty here — an application that serves none, which is
            the base answer.

        The server reads this at mount time and keeps one name → application
        index; a routed application answers with the children of its
        ``_well_known`` branch.
        """
        return ()

    @property
    def app_snapshot(self) -> dict[str, Any]:
        """This app as the monitor sees it, at the instant it is read.

        The monitor aggregates one entry per mounted application by reading
        this on each. Subclasses extend it with their own panel data
        (registers, pool, gauges) on top of these identity facts.
        """
        return {"class": type(self).__name__, "code": self.code, "mount": self.mount}

    @property
    def app_panel(self) -> dict[str, Any]:
        """Which panel renders this app on the monitor shell: a class constant.

        The presentation complement of ``app_snapshot``: the snapshot carries
        the data (polled), this says who draws it (fetched once). ``panel``
        names a renderer in the shell's registry and an unknown name falls
        back to the generic one; ``src`` — when a subclass declares it — is
        the module URL the shell imports to learn that renderer.
        """
        return {"panel": "generic"}

    def on_startup(self) -> None:
        """Lifecycle hook run at server startup. Override as sync or async."""

    def on_shutdown(self) -> None:
        """Lifecycle hook run at server shutdown. Override as sync or async."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point: concrete applications must implement it."""
        raise NotImplementedError(f"{type(self).__name__} does not implement the ASGI callable")
