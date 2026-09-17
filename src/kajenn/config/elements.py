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

"""AsgiServerGrammar — the configuration grammar of ``AsgiServer``.

The grammar the server class exposes as ``AsgiServer.grammar``: one
``configuration`` root (the contrib ``ConfigBuilder`` element, OVERRIDDEN here
with the full closed section list) whose sections describe the whole site.
Every section is a singleton (``[0:1]``), so labels are clean and every path is
stable and hand-writable: ``configuration.server``,
``configuration.authentication.credentials``,
``configuration.applications.<code>``.

Authoring conventions inherited from contrib/config:

- attributes are ANNOTATED, so their signature defaults reach the read stack of
  ``ConfigurationHandler`` (an unannotated parameter never enters
  ``call_args_validations``);
- an attribute whose value may come from outside (env, file, url) is annotated
  ``<type> | BagResolver`` and receives the resolver IN PLACE — there are no
  ``^pointer`` strings in this dialect;
- the recipe orchestrates in ``main`` and delegates each section to a method
  taking the PARENT node.

Sections:

- ``server`` — the runtime options (``host``, ``port``, ``external_url``,
  ``max_threads``, ``shutdown_timeout_seconds``) plus the server-domain children ``session``
  (the session TTL) and ``tasks`` (declared by ``TaskGrammar``, the class that
  peels ``tasks=``).
- ``middleware`` — one ``{name: bool | dict}`` switch per middleware.
- ``authentication`` — the identity surface the core owns:
  the ``users``/``tokens`` store descriptors and the
  ``credentials`` handed to ``AuthCore``.
- ``storage`` — the mount point of genro-storage's own grammar: the mounts of
  the server's ``StorageManager``, plus the section's ``storage_key``.
- ``applications`` — the app collection keyed by ``code``; each entry MOUNTS
  the grammar its ``app_class`` carries.
- ``databases`` — one descriptor per database handler.
- ``plugins`` — the router plugins armed on every routed app.
- ``openapi`` — the OpenAPI metadata (grammar only in core 1a).

The SPA pool is NOT a section of this dialect: a pool belongs to the application
that owns it, so its words live in that application's own grammar and its recipe
is written under ``applications.<code>.orchestration.commander``.

A recipe subclasses ``AsgiConfigBuilder`` and overrides ``main(self, root)``;
application classes are imported and passed as objects::

    from myshop.app import Application as Shop

    class ServerConfiguration(AsgiConfigBuilder):
        def main(self, root):
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=8000)
            cfg.middleware(cors=True)
            cfg.applications(default="shop").application(code="shop", app_class=Shop)
"""

from __future__ import annotations

from typing import Annotated, Any

from genro_bag import BagResolver
from genro_builders.builder import Regex, element

from ..tasks.mixin import TaskGrammar

# A collection key is a Bag label and a path segment: an identifier, never
# empty, no dots and no spaces. The grammar refuses anything else before
# the node is written.
CODE_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"


class AsgiServerGrammar(TaskGrammar):
    """Configuration grammar of ``AsgiServer``: the site layout, reading elsewhere.

    Grammar only — the runtime reads the built tree through
    ``ConfigurationHandler``, never through this class. Capability-owned
    companions are composed explicitly (``TaskGrammar`` — the ``tasks`` child of
    ``server``, owned by ``TaskMixin``).
    """

    @element(
        sub_tags=(
            "site[0:1],server[0:1],middleware[0:1],authentication[0:1],storage[0:1],"
            "applications[0:1],databases[0:1],plugins[0:1],openapi[0:1]"
        ),
        node_label="configuration",
    )
    def configuration(self) -> None:
        """Root element of the configuration document (one per recipe).

        Overrides the contrib root with the full section list of this dialect.
        Each section is a singleton, so its label IS its tag and every path
        below it is stable.
        """

    @element(parent_tags="configuration", sub_tags="")
    def site(
        self,
        name: str | BagResolver = None,
        home: str | BagResolver = None,
    ) -> None:
        """The site's identity: the name it is filed under and the folder it owns.

        ``name`` is the name of its card, ``<KAJENN_HOME>/sites/<name>.json``.

        ``home`` is the site's own folder. Inside it every site-owned path is
        relative and named — the recipe, ``static/``, ``data/frozen_users``,
        ``data/sessions``, ``sockets/``, ``logs/``, ``run/`` — and the server
        hands the folder out as ``server.site_home`` (``SiteHome``). It anchors
        the default ``home:`` storage volume — beside ``site:``, the site's
        folder as the configuration declares it — and with no home declared the
        two coincide.

        A site declares both in its recipe (the ``site_name`` / ``site_home``
        attributes of ``AsgiConfigBuilder`` write this element); the CLI writes
        them from the card. A configuration carrying a ``name`` is a site the
        CLI recognises: ``kajenn serve <path>`` files its card, so the next
        boot is ``kajenn serve <name>``.
        """

    @element(parent_tags="configuration", sub_tags="session[0:1],tasks[0:1],websocket[0:1]")
    def server(
        self,
        host: str | BagResolver = None,
        port: int | BagResolver = None,
        external_url: str | BagResolver = None,
        max_threads: int | BagResolver = None,
        shutdown_timeout_seconds: float | BagResolver = None,
        debug: bool | str | BagResolver = None,
    ) -> None:
        """Server runtime options.

        ``host``/``port`` become the defaults of ``AsgiServer.serve``.

        ``external_url`` is the server's PUBLIC base address — what the server
        calls itself when it hands its own URL to a third party
        (``https://shop.example.com``; a trailing slash is stripped). It is not
        the listener: behind a proxy the bind address and the public address
        differ, and only the latter is meaningful to an outside caller. Required
        when an application declares OIDC providers — the provider is given an
        absolute ``redirect_uri`` — and a boot error when missing there.

        ``max_threads`` sizes the server's thread pool: ``BaseServer`` peels it
        and hands it to ``WorkPool`` (omitted, the stdlib default
        ``min(32, cpu + 4)`` applies).

        ``debug`` is the DECLARED usage mode the core never branches on (the CLI
        spells it ``--debug``): ``True``, or a comma-separated string of
        parameters an application reads for itself.

        ``shutdown_timeout_seconds`` (5.0) bounds how long uvicorn waits for open
        connections at shutdown before cancelling them: one endless response —
        an SSE stream a client never closes — would otherwise hold the process
        for ever, and the lifespan shutdown that stops the applications would
        never run.

        Children are server-domain: ``session`` (the session TTL), ``tasks``
        (the task backbone, declared by ``TaskGrammar``) and ``websocket``.
        """

    @element(parent_tags="server", sub_tags="")
    def websocket(
        self,
        origins: str | BagResolver = None,
        max_concurrent: int | BagResolver = None,
    ) -> None:
        """Websocket options, server-domain like the session and the tasks.

        ``origins`` is the comma-separated list of Origins a handshake may come
        from — ``*`` admits every one, and the default, an empty list, admits
        only the host the handshake came to. A handshake with no ``Origin`` at
        all passes either way: the gate exists against a page on another site,
        not against a client of its own.

        ``max_concurrent`` is how many messages of ONE connection may be served
        at once (default 16). The control ping is answered outside it, so a
        connection whose slots are all busy still answers "are you there".
        """

    @element(parent_tags="server", sub_tags="")
    def session(
        self,
        ttl: int = None,
        store_class: type = None,
        save_path: str = None,
        **params: Any,
    ) -> None:
        """Session options: the lifetime, the store that keeps them, the snapshot.

        ``ttl`` (seconds) becomes the server's ``session_ttl``.
        ``store_class`` names the backend CLASS — the server instantiates it with
        the remaining attributes (``store_class(**params)``), so a deployment
        that keeps sessions elsewhere declares the class here instead of handing
        the server a built object. Omitted, the composition's own
        ``MemorySessionStore`` applies.

        ``save_path`` is the pickle file the sessions are written to at shutdown
        and read back from at startup — the development survival line. Unset,
        the snapshot is disarmed. ``kajenn serve --name <n>`` writes
        ``<home>/sessions/<n>.pickle`` here.

        Server-domain, so it lives under ``server``, not under an application."""

    @element(parent_tags="configuration", sub_tags="")
    def middleware(
        self,
        errors: bool | dict = None,
        logging: bool | dict = None,
        cors: bool | dict = None,
        auth: bool | dict = None,
        session: bool | dict = None,
        **extra: bool | dict,
    ) -> None:
        """Global middleware switches: one ``{name: bool | dict}`` kwarg per
        middleware. A dict value enables the middleware and becomes its
        constructor options. The six declared names are the core's own registry
        (``middleware.default_registry()``); a middleware registered from
        outside (``middleware_registry=``) is written by its own name and rides
        through ``**extra`` — the signature is OPEN so that the switches have
        ONE place, the configuration, whatever registry the class came from."""

    @element(
        parent_tags="configuration",
        sub_tags="users[0:1],tokens[0:1],credentials[0:1]",
        node_label="authentication",
    )
    def authentication(self) -> None:
        """The server's identity surface: the STORES and the header credentials.

        ``users`` and ``tokens`` are the store descriptors ``AuthMixin`` peels
        and ``credentials`` the entries ``AuthCore`` verifies. What asks a
        human for a user and a password is NOT here (D-SA-10): the login policy
        and the OIDC providers are words of the grammar the application owning
        the login surface declares, written under its own ``application``
        element. The server creates no user either (owner, 2026-09-12): there is
        no bootstrap password word.
        """

    @element(parent_tags="authentication", sub_tags="")
    def users(
        self, mount: str = None, prefix: str = None, store_class: type = None, **params: Any
    ) -> None:
        """Identity store: where it keeps its records, and which class keeps them.

        ``mount``/``prefix`` place the records (default ``site:users``);
        ``store_class`` names the CLASS the server builds — ``FileUserStore``
        when omitted — and the remaining attributes are its own kwargs. The
        server hands it the storage: a built store is not a configuration
        value."""

    @element(parent_tags="authentication", sub_tags="")
    def tokens(
        self, mount: str = None, prefix: str = None, store_class: type = None, **params: Any
    ) -> None:
        """Api-key store: the same three words as ``users`` — ``mount``/``prefix``
        for the records, ``store_class`` for the class the server builds
        (``FileApiKeyStore`` when omitted) and its own remaining kwargs."""

    @element(
        parent_tags="authentication",
        sub_tags="basic_user,bearer_token,jwt",
        node_label="credentials",
    )
    def credentials(self) -> None:
        """The header credentials handed to ``AuthCore``.

        Three repeatable children, one per backend. They are NOT a keyed
        collection: the three tags key differently (``username``, ``identity``,
        nothing at all for ``jwt``, which is an ordered list), so the handler
        folds them into ``AuthCore``'s own shapes by reading each child.
        """

    @element(parent_tags="credentials", sub_tags="")
    def basic_user(
        self,
        username: str,
        password: str | BagResolver = None,
        tags: str = None,
    ) -> None:
        """One HTTP Basic user: ``username`` (REQUIRED — the ``AuthCore`` key),
        ``password`` (give it a resolver) and comma-separated ``tags``."""

    @element(parent_tags="credentials", sub_tags="")
    def bearer_token(
        self,
        identity: str,
        token: str | BagResolver = None,
        tags: str = None,
    ) -> None:
        """One static Bearer token: ``identity`` (REQUIRED — the identity the
        token authenticates as), ``token`` (give it a resolver) and
        comma-separated ``tags``."""

    @element(parent_tags="credentials", sub_tags="")
    def jwt(
        self,
        name: str = None,
        secret: str | BagResolver = None,
        public_key: str | BagResolver = None,
        algorithm: str = "HS256",
        tags: str = None,
    ) -> None:
        """One JWT verifier (repeatable, an ORDERED list — the first that
        verifies wins): ``secret`` (shared HMAC material, the only kind that may
        also SIGN) or ``public_key`` (verify only), the ``algorithm``, an
        optional ``name`` and comma-separated ``tags``."""

    @element(parent_tags="configuration", _meta={"subbuilder": "app:grammar"})
    def storage(self, app: type, storage_key: str | BagResolver = None) -> None:
        """The server's storage, and the MOUNT POINT of genro-storage's grammar.

        This dialect declares NO storage vocabulary of its own: ``app``
        (``StorageManager``, REQUIRED — the subbuilder reference reads the call
        site, so it cannot be defaulted in the signature) carries the grammar
        governing this node's children, and the mounts are written in
        genro-storage's own words — one element per protocol, the tag IS the
        protocol. The elements hang DIRECTLY under this node: the envelope is
        transparent to containment, so the foreign ``mounts`` collection is not
        part of the recipe.

        ``storage_key`` is the at-rest key material of the whole section
        (comma-separated Fernet keys — the first encrypts, all decrypt, for
        rotation), and belongs here rather than on ``server`` because it is
        meaningless without the mounts it unlocks. Give it a resolver so the
        secret stays out of the recipe — a resolver, never a lambda, since a
        callback does not serialize::

            from genro_bag.resolvers import EnvResolver
            from genro_storage import StorageManager

            def storage_section(self, cfg):
                s = cfg.storage(app=StorageManager,
                                storage_key=EnvResolver("GENRO_STORAGE_KEY"))
                s.local(name="site", base_path=".")
                s.s3(name="uploads", bucket="shop-media",
                     default_encrypted="shopspa")

        Omitted entirely, the server builds its default manager: the single
        ``site:`` mount on the deployment directory.
        """

    @element(parent_tags="configuration", sub_tags="application", collection_key="code")
    def applications(self, default: str = None) -> None:
        """Collection of applications, each labelled by its ``code``. The
        optional ``default`` names the application ``/`` **redirects to** (307)
        when no application answers the site root; it elects nothing."""

    @element(parent_tags="applications", _meta={"subbuilder": "app_class:grammar"})
    def application(
        self,
        app_class: type,
        code: Annotated[str, Regex(CODE_PATTERN)] = None,
        mount: str = None,
        **app_kwargs: Any,
    ) -> None:
        """One application, and the MOUNT POINT of its own grammar.

        ``app_class`` (the imported class, REQUIRED) carries the grammar
        governing this node's children (``app_class.grammar``, subbuilder by
        reference): the site dialect never validates an app's internal
        vocabulary, the app itself declares it. ``code`` is the collection key —
        an identifier (``CODE_PATTERN``), refused by the grammar otherwise —
        ``mount`` the URL prefix (defaulting to ``code``; ``mount=""`` is the
        site root — the one application answering ``/`` and every unclaimed
        path). Remaining kwargs are the app's own constructor kwargs and stay
        open — the envelope's attributes belong to THIS grammar, only its
        children live in the mounted one.
        """

    @element(parent_tags="configuration", sub_tags="database", collection_key="code")
    def databases(self) -> None:
        """Collection of database descriptors, each labelled by its ``code``."""

    @element(parent_tags="databases", sub_tags="")
    def database(
        self,
        db_class: type,
        code: Annotated[str, Regex(CODE_PATTERN)] = None,
        db_handler_class: type = None,
        **params: Any,
    ) -> None:
        """One database: ``code`` (the registry key, an identifier per
        ``CODE_PATTERN``), ``db_class`` (REQUIRED —
        the grammar rejects a database without it), the optional
        ``db_handler_class`` (``AsgiDbHandlerBase`` when omitted) and the
        connection kwargs handed to ``db_class(**params)``. The ``db_class`` is
        user-provided — the core never imports db drivers."""

    @element(parent_tags="configuration", sub_tags="plugin", collection_key="code")
    def plugins(self, **extra: bool | dict) -> None:
        """Collection of router plugins, each labelled by its ``code``.
        Materialized as the server's plugin switches (``PluginMixin``): the
        server arms every enabled plugin onto each routed app it hosts.

        A plugin is normally one ``plugin`` child. The signature is OPEN as
        well, so a switch may also be written as an attribute of the collection
        — the short form the shortcut uses, and the one a plugin registered
        from outside (``plugin_registry=``) needs."""

    @element(parent_tags="plugins", sub_tags="")
    def plugin(self, code: str = None, enabled: bool = True, **options: Any) -> None:
        """One router plugin: ``code`` (the collection key), optional
        ``enabled`` (set False to leave it unarmed) and arbitrary options handed
        to ``router.plug(code, **options)``."""

    @element(parent_tags="configuration", sub_tags="")
    def openapi(
        self,
        title: str = None,
        version: str = None,
        description: str = None,
    ) -> None:
        """OpenAPI metadata: ``title``, ``version``, ``description``. Grammar
        only in core 1a — the OpenAPI application arrives in core 1c; read and
        skipped here."""
