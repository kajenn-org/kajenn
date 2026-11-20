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
A mixin can declare a ``grammar`` of its own too: ``__init_subclass__``
composes every grammar a class along the MRO writes in its own ``__dict__``,
in MRO order, so the application accepts the mixin's words beside its own and
the more derived class wins on an element of the same name.

An application declares the well-known URLs it answers in the class dict
``well_known`` (public path → route), merged the same way along the MRO: a
mixin's paths stay declared beside the class's own, and the more derived class
wins on the same path. A ``{mount}`` marker in a path is replaced by the
instance's mount; on the root mount ``""`` the segment ``/{mount}`` goes away
whole. The server indexes what ``active_well_known()`` returns.
``default_well_known`` has the same shape: a default answers a path only when
no application declares it, whatever the order of registration.

An application reaches its databases with ``db(name=None)`` (the object the
current call took) and ``async_db(name=None)`` (its awaitable face, for an
``async def`` handler). Names follow the owner rule: ``foo`` is a server
database, ``chat:foo`` the database ``foo`` of the application ``chat``. With
no name, ``db_name`` decides; without it the application's own ``default``
when it declares one, else the server's ``default``.

A method marked ``@broadcast_hook`` is one ``server.applications_broadcast``
may call on every application; the mark counts on any definition of the
method along the MRO, so an override stays subscribed. ``on_shutdown``,
``plugin_configuration`` and ``prometheus_metrics`` are three.

An app READS that subtree back through ``self.config(path)``, which prefixes
``applications.<code>.`` and delegates to the server's own read door: the app
never holds a slice of the tree, only an address in it.

An application declares the tags it owns in ``declared_tags`` and the system
users it needs in ``system_users``; both are empty on the base. The
authentication application creates the missing records. A tag is written by
its full name everywhere, as the author chose it: in ``declared_tags``, in
``auth_rule`` and in the tags of a system user.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from genro_builders.builder import Regex, element

from ._exposition import families_text
from .well_known import WELL_KNOWN_PATTERN

if TYPE_CHECKING:
    from .server import BaseServer
    from .types import Receive, Scope, Send

__all__ = ["ApplicationGrammar", "BaseApplication", "broadcast_hook", "is_broadcast_hook"]

_MISSING = object()

BODY_MODES: dict[str, bool] = {"decoded": False, "raw": True}
"""The ``request(body=...)`` words, as what they decide: raw bytes or decoded."""

ERROR_CODES: dict[str, int] = {"strict": 400, "fastapi": 422}
"""The ``request(error_codes=...)`` words, as the status a rejected value answers."""


def broadcast_hook(method: Callable[..., Any]) -> Callable[..., Any]:
    """Mark ``method`` as a hook ``server.applications_broadcast`` may call.

    The mark says nothing else: the method itself decides what it does. It
    counts on any definition of the method along the MRO, so an override in a
    subclass stays subscribed without repeating the decorator.
    """
    setattr(method, "_kajenn_broadcast_hook", True)  # noqa: B010
    return method


def is_broadcast_hook(cls: type, name: str) -> bool:
    """Whether any definition of ``name`` along ``cls``'s MRO carries ``@broadcast_hook``.

    An override without the decorator stays subscribed, and an unmarked
    definition in a mixin nearer the root of the MRO does not hide the mark.
    A name no class defines is not a broadcast hook.
    """
    return any(getattr(klass.__dict__[name], "_kajenn_broadcast_hook", False)
               for klass in cls.__mro__ if name in klass.__dict__)


# A collection key is a Bag label and a path segment: an identifier, never
# empty, no dots and no spaces. The grammar refuses anything else before
# the node is written.
CODE_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"


class DatabaseGrammar:
    """The ``databases`` section, composed by the server's grammar and by every
    application's (``ApplicationGrammar``): the same two elements at both levels.
    """

    @element(node_label="databases", sub_tags="database", collection_key="code")
    def databases(self, handler_class: type = None) -> None:
        """Collection of databases, each labelled by its ``code``.

        ``handler_class`` is the default of every ``database`` below. At server
        level it is also the default of an application's databases whose own
        section declares none; a database ``foo`` of the application ``chat`` is
        ``chat:foo`` in the server's registry.
        """

    @element(parent_tags="databases", sub_tags="")
    def database(
        self,
        code: Annotated[str, Regex(CODE_PATTERN)] = None,
        handler_class: type = None,
        **params: Any,
    ) -> None:
        """One database: ``code`` (the registry name, an identifier per
        ``CODE_PATTERN``), an optional ``handler_class`` overriding the section
        default (a ``DbHandlerBase`` subclass) and the ``params`` handed to the
        handler's ``add_database``. The handler builds the database — the core
        never imports db drivers."""


class ApplicationGrammar(DatabaseGrammar):
    """The configuration grammar every application inherits.

    Four elements: ``parameters``, for the free options a plain app needs — an
    application with nothing of its own still has a mountable grammar (an EMPTY
    grammar class is rejected by builders) — ``request``, the two options
    every application has about the requests the core serves for it,
    ``databases``, the application's own databases (from ``DatabaseGrammar``),
    and ``plugin_configuration``, the plugin options this installation gives
    to the routes of the application.
    A richer app subclasses this to add its own vocabulary.
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

    @element(sub_tags="configure")
    def plugin_configuration(self, plugin: Annotated[str, Regex(CODE_PATTERN)]) -> None:
        """The options this installation gives to the routes for one plugin.

        ``plugin`` names a plugin of the application router (``auth``,
        ``channel``, ``openapi``, one armed by the ``plugins`` section). Each
        ``configure`` child gives its options to one route or to several.
        """

    @element(parent_tags="plugin_configuration", sub_tags="")
    def configure(self, route: str, **options: Any) -> None:
        """The options of the block's plugin for ``route``.

        ``route`` is the path of a route relative to the application, as in
        the URL after the mount and without a leading slash
        (``"products/list"``), or several such paths separated by commas.
        ``options`` are the parameters of that plugin's ``configure``
        (``rule`` for ``auth``, ``channels`` for ``channel``): they replace
        what ``@route(...)`` wrote for those routes.
        """


class BaseApplication:
    """Base class for applications attached to a ``BaseServer``.

    Constructor kwargs peeled here: ``code`` — the application's identity,
    empty meaning the class name lowercased — ``mount`` — the URL prefix
    it answers under, ``None`` meaning the same as the code — and ``db_name``
    — the database ``db()`` answers without a name, ``None`` meaning the
    default rule. All three default to the class attributes below, so a
    subclass can set them declaratively.

    ``well_known`` and ``default_well_known`` are read from the class dicts
    along the MRO, merged base first, and stored on the instance with
    ``{mount}`` resolved; a path that does not match ``WELL_KNOWN_PATTERN``
    raises ``ValueError`` naming it. A default answers only a path that no
    application declares in ``well_known``.
    """

    code: str = ""
    mount: str | None = None
    #: Whether the server can do none of its work without this application:
    #: when it fails, only the management application keeps answering.
    mandatory: bool = False
    db_name: str | None = None
    grammar: type = ApplicationGrammar
    well_known: dict[str, str] = {}
    default_well_known: dict[str, str] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Only what classes WROTE counts: a grammar composed here sits in its
        # class's __dict__ too, and stands for the declared grammars it joins.
        declared: list[type] = []
        for klass in cls.__mro__:
            grammar = klass.__dict__.get("grammar")
            if grammar is None:
                continue
            declared.extend(grammar.__bases__ if grammar.__dict__.get("_kajenn_composed") else [grammar])
        declared = list(dict.fromkeys(declared))
        # A grammar already a base of another one would break the composed MRO.
        bases = tuple(
            grammar
            for grammar in declared
            if not any(other is not grammar and issubclass(other, grammar) for other in declared)
        )
        if len(bases) == 1:
            cls.grammar = bases[0]
        else:
            cls.grammar = type(f"{cls.__name__}Grammar", bases, {"_kajenn_composed": True})

    def __init__(self, **kwargs: Any) -> None:
        cls = type(self)
        code: str = kwargs.pop("code", cls.code) or cls.__name__.lower()
        mount: str | None = kwargs.pop("mount", cls.mount)
        self.db_name = kwargs.pop("db_name", cls.db_name)
        self.mandatory: bool = bool(kwargs.pop("mandatory", cls.mandatory))
        self.code = code
        # ``is None`` and never truthiness: ``mount=""`` IS the site root, and
        # ``mount or code`` would silently move a root app to ``/code``.
        self.mount = code if mount is None else mount
        self.well_known: dict[str, str] = self.resolve_well_known("well_known")
        self.default_well_known: dict[str, str] = self.resolve_well_known("default_well_known")
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

    def database_name(self, name: str | None = None) -> str:
        """The registry name ``db(name)`` resolves to.

        A given ``name`` stands as it is (``foo`` server, ``chat:foo``
        application). ``None`` is ``db_name`` when set, else ``<code>:default``
        when this application declares a ``default``, else ``default``.
        """
        if name is not None:
            return name
        if self.db_name is not None:
            return self.db_name
        own = f"{self.code}:default"
        return own if own in self.server.databases else "default"

    def db(self, name: str | None = None) -> Any:
        """The object database ``name`` gives the current call (``database_name`` rule).

        Taken once per name per call and released at the end of the call; for
        a sync handler. An ``async def`` handler uses ``async_db``.
        """
        return self.server.take_database(self.database_name(name))

    def async_db(self, name: str | None = None) -> Any:
        """The awaitable face of ``db(name)``: the same taken object, through its handler."""
        resolved = self.database_name(name)
        taken = self.server.take_database(resolved)
        return self.server.databases[resolved].async_view(taken)

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

    def resolve_well_known(self, attribute: str) -> dict[str, str]:
        """The class dicts named ``attribute`` along the MRO, merged base first.

        ``{mount}`` is replaced by ``self.mount``; on the root mount ``""`` the
        segment ``/{mount}`` goes away whole. A path that does not match
        ``WELL_KNOWN_PATTERN`` raises ``ValueError`` naming it.
        """
        cls = type(self)
        merged: dict[str, str] = {}
        for klass in reversed(cls.__mro__):
            merged.update(klass.__dict__.get(attribute, {}))
        resolved: dict[str, str] = {}
        for declared, target in merged.items():
            if self.mount:
                path = declared.replace("{mount}", self.mount)
            else:
                path = declared.replace("/{mount}", "")
            if not WELL_KNOWN_PATTERN.match(path):
                raise ValueError(
                    f"{cls.__name__}: {declared!r} is not a well-known path "
                    "(/.well-known/..., /robots.txt, /favicon.ico, /healthz, /livez, /readyz)"
                )
            resolved[path] = target
        return resolved

    def active_well_known(self) -> dict[str, str]:
        """The well-known paths the server indexes for this application: path → route.

        The base answers every declared path (a copy of ``well_known``). The
        server calls it after attaching the application, so a subclass can read
        its own configuration and leave out what is not configured.
        """
        return dict(self.well_known)

    def active_default_well_known(self) -> dict[str, str]:
        """The default well-known paths the server indexes: path → route.

        A copy of ``default_well_known``, called like ``active_well_known``.
        A default answers only a path no application declares.
        """
        return dict(self.default_well_known)

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

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        """The tags this application owns: one dict per tag.

        Each dict has ``code``, the full tag name the author also writes in
        ``auth_rule``, and optionally ``description``, ``reserved`` and
        ``parent`` (the full name of another tag). The base declares none.
        """
        return []

    @property
    def self_registration(self) -> dict[str, Any] | None:
        """What an external login entering through this application does with an unknown person.

        ``None`` (the base): the application declares nothing and the
        ``self_registration`` setting of ``_auth`` applies. A dict with
        ``status`` and ``tags``: the person is registered with that status and
        those tags, and a known user entering through this application gets
        the tags it lacks. ``{}``: an unknown person is refused. The login
        names the application with ``?app=<code>`` on the ``start`` route of
        an OIDC provider of ``_auth``.
        """
        return None

    @property
    def system_users(self) -> list[dict[str, Any]]:
        """The system users this application needs: one complete user record each.

        A record carries ``identity``, ``status``, ``tags`` and ``groups``;
        every tag is written by its full name. The base declares none.
        """
        return []

    def _broadcast_callable(self, name: str) -> Callable[..., Any] | None:
        """The callable ``applications_broadcast`` runs for the hook ``name``, or ``None``.

        The bound method when ``name`` is a ``@broadcast_hook`` of this class;
        ``None`` skips the application.
        """
        return getattr(self, name) if is_broadcast_hook(type(self), name) else None

    @broadcast_hook
    def plugin_configuration(self) -> None:
        """Hook that gives the routes the plugin options of this installation.

        The server reaches it through ``applications_broadcast`` at startup,
        before any ``on_startup``; alone, on an application added to a running
        server and on one whose ``plugin_configuration`` changed. The default
        does nothing: an application without a router has no plugin to
        configure. Override as sync or async.
        """

    def on_startup(self) -> None:
        """Lifecycle hook run at server startup. Override as sync or async."""

    @broadcast_hook
    def on_shutdown(self) -> None:
        """Lifecycle hook run at server shutdown. Override as sync or async.

        The lifespan reaches it through ``server.applications_broadcast``.
        """

    @broadcast_hook
    def prometheus_metrics(self) -> str:
        """This application's Prometheus families, as exposition text.

        ``_server/metrics`` reads it on every application at every scrape and
        adds the label ``app=<code>`` to every sample. The base contributes
        nothing (``""``). An application overrides it, builds its families
        with ``prometheus_client`` and returns ``generate_latest(...)`` text.
        It never uses the module-level ``REGISTRY``: it creates its metric
        objects with ``registry=None`` or registers them on a
        ``CollectorRegistry`` of its own. Override as sync or async.

        In the dedicated process of a ``spawner`` application
        (``server.kbus_role`` set) the base returns the families of that
        process (``server.process_metrics``: build, process, ``WorkPool``,
        tasks, scheduler, startup), with the names the server process uses; an
        override keeps them through ``super().prometheus_metrics()``. In the
        server process it returns ``""``: ``ServerApplication`` writes them.
        """
        if self.server.kbus_role is None:
            return ""
        return families_text(self.server.process_metrics.collect())

    def on_remove(self) -> None:
        """Lifecycle hook run when the server removes this application.

        It runs after the application is disabled — new requests answer 503 —
        and before it is taken away: the application closes its calls in
        progress and releases what it holds, and returns when it has
        finished. ``on_shutdown`` is not called. Override as sync or async.
        """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point: concrete applications must implement it."""
        raise NotImplementedError(f"{type(self).__name__} does not implement the ASGI callable")
