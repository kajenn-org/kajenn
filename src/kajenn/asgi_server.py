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

"""AsgiServer — the shipped mono-process server composition (D22, D6, D16).

``AsgiServer`` stacks every core capability mixin over ``BaseServer`` in one
MRO (``CommunicationMixin, AuthMixin, SessionMixin, MiddlewareMixin,
PluginMixin, StorageMixin, TaskMixin, BaseServer``): the complete mono-process
async server of D22. ``TaskMixin`` sits after ``StorageMixin`` (it needs
``server.storage``) and before ``BaseServer`` (its lifespan hook must wrap the
base ``Lifespan``). The future internal (worker) server simply composes the SAME
base WITHOUT the auth mixin (D6 by construction — the base never learned about
the chain).

The server is SELF-CONFIGURING: ``AsgiServer(config=source)`` builds its own
read door — a ``ConfigurationHandler`` over a template NAME, a ``config.py``
path, a recipe class, a recipe instance or a ready handler — derives its
constructor kwargs from it and then runs the ordinary D16 cooperative chain.
Nothing materializes a server from the outside; the class that needs the values
reads them. Explicitly passed kwargs WIN over the configured ones, wholesale per
kwarg (``AsgiServer(config=Recipe, port=0)`` serves the recipe's site on an
OS-assigned port), and the handler stays reachable as ``server.config`` — the
read door applications delegate to.

THE CONFIGURATION ALWAYS EXISTS (#91). ``AsgiServer(applications=[...], ...)``
without a source is a SHORTCUT, not a second way to be born: it takes the
ready-made ``default`` template and writes the kwargs it received into a
``ShortcutConfiguration`` layered on top of it, so ``server.config`` is a
handler here as everywhere and every option is read from the tree. Every option it received is written there and popped, applications
included: the shortcut declares CLASSES with their parameters, and the server
instantiates them off the tree exactly as it does for a written recipe.

Its cooperative ``__init__`` peels the kwargs the frozen Macro 1 ``BaseServer``
does not accept — ``host``/``port``/``external_url`` — and forwards
everything else (``applications``, ``auth``, ``session_store``/``session_ttl``,
``middleware``/``middleware_registry``, ``plugins``/``plugin_registry``,
``storage``/``storage_key``, ``parent``) down the D16 chain. The peeled
``host``/``port`` become the defaults of ``serve``, so a configured server
serves on its configured address unless the caller overrides it.

``host``/``port`` are the LISTENER; ``external_url`` is the server's PUBLIC
base address — the two differ behind a proxy and answer different questions.
The listener says where to bind; the public address is what the server calls
itself when it hands its own URL to a third party. Only one consumer needs it
today (an OIDC provider is given an absolute ``redirect_uri``, RFC 6749
§3.1.2), and it is DECLARED rather than derived from a request: the URI must
match the one registered with the provider — a deployment fact known to
whoever installs — and deriving it from the client-supplied ``Host`` would
build a value the provider then rejects. Missing it with a provider
configured is a boot error (``_check_oidc_external_url``), not an opaque
provider error at the first login.

No application is registered behind the caller's back (D-SA-10, superseding the
"automatic, not configured" half of SPEC D4): the server application is declared
like any other, with its ``app_class`` from ``kajenn_server_app`` and the
code ``_server``. A server that declares none exposes no ``/_server/...`` and
the core imports nothing of that package. The configured databases are
registered at the end of ``__init__``, over the live server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genro_builders.builder import BuilderBase

from .auth import AuthMixin
from .communication import CommunicationMixin
from .config.elements import AsgiServerGrammar
from .config.default_config import DefaultConfig
from .config.handler import ConfigurationHandler
from .config.templates import (
    CONFIGURATION_TEMPLATES,
    DEFAULT_TEMPLATE,
    ShortcutConfiguration,
)
from .db import AsgiDbHandlerBase
from .middleware import MiddlewareMixin
from .plugin_mixin import PluginMixin
from .server import BaseServer
from .session import SessionMixin
from .site_home import SiteHome
from .storage_mixin import StorageMixin
from .tasks import TaskMixin

__all__ = ["AsgiServer"]

ConfigSource = str | Path | type | BuilderBase | ConfigurationHandler


class AsgiServer(
    CommunicationMixin,
    AuthMixin,
    SessionMixin,
    MiddlewareMixin,
    PluginMixin,
    StorageMixin,
    TaskMixin,
    BaseServer,
):
    """The shipped composition: communication + auth + sessions + chain + plugins + storage + base.

    Constructor kwargs peeled here: ``config`` — the configuration source this
    server reads itself from — ``site_name`` and ``site_home`` (the site's
    identity and the folder it owns), ``host`` and ``port`` (the ``serve``
    defaults) and ``external_url`` (the public base address, trailing slash
    stripped).
    Every other kwarg flows to the capability mixins and the base (D16
    cooperative init).
    """

    grammar: type = AsgiServerGrammar

    def __init__(self, config: ConfigSource | None = None, **kwargs: Any) -> None:
        self._config = self._build_config(config, kwargs)
        kwargs = {**self._configured_kwargs(self.config), **kwargs}
        self._site_name: str | None = kwargs.pop("site_name", None)
        site_home = kwargs.pop("site_home", None)
        self._site_home = SiteHome(site_home) if site_home is not None else None
        self._config_host: str | None = kwargs.pop("host", None)
        self._config_port: int | None = kwargs.pop("port", None)
        external_url: str | None = kwargs.pop("external_url", None)
        self._external_url: str | None = external_url.rstrip("/") if external_url else None
        super().__init__(**kwargs)
        self._check_oidc_external_url()
        self._register_configured_databases(self.config)

    def _build_config(
        self, config: ConfigSource | None, kwargs: dict[str, Any]
    ) -> ConfigurationHandler:
        """The read door over ``config``. There is always one.

        A ready handler passes through — its owner already decided its layering.
        A template NAME, a ``config.py`` path, a recipe class or a recipe
        instance becomes the TOP layer of a handler whose parents
        ``DefaultConfig.parents_for()` computes: the package defaults, plus the
        defaults source the recipe declares (``default_config``).

        ``None`` is the SHORTCUT: the constructor kwargs are written into a
        ``ShortcutConfiguration`` layered over the ``default`` template, so a
        server composed in code has the tree a recipe would have produced. The
        shortcut consumes the kwargs it writes, and the deployment's own defaults
        layer is not consulted — the caller composed the site in code.

        A ``config.py`` path is imported ONCE, here: the loaded class both
        answers ``default_config`` and becomes the handler's source, so a
        module-body side effect fires a single time per boot and the class the
        parents were computed from is the class the handler builds."""
        if isinstance(config, ConfigurationHandler):
            return config
        if config is None:
            return ConfigurationHandler(
                ShortcutConfiguration(kwargs),
                parents=[CONFIGURATION_TEMPLATES[DEFAULT_TEMPLATE]],
            )
        defaults = DefaultConfig()
        if isinstance(config, str) and config in CONFIGURATION_TEMPLATES:
            config = CONFIGURATION_TEMPLATES[config]
        elif isinstance(config, (str, Path)):
            config = defaults.recipe_class(config)
        return ConfigurationHandler(config, parents=defaults.parents_for(config))

    def _configured_kwargs(self, config: ConfigurationHandler) -> dict[str, Any]:
        """The constructor kwargs the configuration declares.

        One helper of the read door per section, each mapped to the kwarg the
        owning class peels; a section the recipe omits contributes nothing, so
        the composition's own defaults apply. ``applications`` are instantiated
        HERE — the recipe named the classes and their kwargs, and a recipe error
        surfaces as a boot error instead of a broken server. There is one road:
        a server composed in code declares CLASSES too, through the shortcut.
        """
        kwargs: dict[str, Any] = config.site_kwargs()
        kwargs.update(config.server_kwargs())
        kwargs.update(config.identity_kwargs())
        for name, value in (
            ("middleware", config.middleware_config()),
            ("auth", config.auth_entries()),
            ("plugins", config.plugins_config()),
        ):
            if value is not None:
                kwargs[name] = value
        storage = config.storage_config()
        if storage is not None:
            kwargs["storage"], kwargs["storage_key"] = storage
        entries, default = config.applications()
        kwargs["applications"] = [app_class(**app_kwargs) for app_class, app_kwargs in entries]
        if default is not None:
            kwargs["default"] = default
        return kwargs

    def _register_configured_databases(self, config: ConfigurationHandler) -> None:
        """Build and register the configured database handlers over the live server.

        Each descriptor becomes ``db_handler_class(db_class(**params))``,
        registered by its ``code``; the default handler class is
        ``AsgiDbHandlerBase``. It runs after the cooperative chain because
        ``add_database`` needs the server, not its kwargs.
        """
        for descriptor in config.databases():
            db_class = descriptor["db_class"]
            handler_class = descriptor["db_handler_class"] or AsgiDbHandlerBase
            self.add_database(
                descriptor["code"], handler_class(db_class(**descriptor["params"]))
            )

    @property
    def config(self) -> ConfigurationHandler:
        """The read door over this server's configuration. There is always one.

        Callable as ``server.config("server.host")`` — the four-layer read stack
        of the ``ConfigurationHandler`` — and the door applications delegate to
        with their own ``applications.<code>.`` prefix.
        """
        return self._config

    def _check_oidc_external_url(self) -> None:
        """Refuse to boot when a provider is configured without ``external_url``.

        Runs once the applications are registered, the first moment both facts
        are known — the app carries the configured providers, the server carries
        its public address — and covers the configured and the hand-built server
        with one check. The check reads an attribute (``oidc_providers``) off
        whatever application answers to ``_server``: the core imports no class
        to ask the question. An OIDC provider is handed the ABSOLUTE ``redirect_uri`` it
        must send the browser back to; without a public base address that URI
        cannot be built, so the configuration is incomplete and the server says
        so loudly instead of failing at the first login attempt with a
        provider-side error.
        """
        providers = getattr(self.applications.get("_server"), "oidc_providers", None)
        if providers and self.external_url is None:
            codes = ", ".join(sorted(providers))
            raise ValueError(
                f"oidc provider(s) {codes} configured but the server has no "
                "external_url: OIDC needs the public base URL to build the "
                "absolute redirect_uri (set server(external_url=...))"
            )

    @property
    def site_name(self) -> str | None:
        """The name this site is filed under (``None`` when it has none)."""
        return self._site_name

    @property
    def site_home(self) -> SiteHome | None:
        """The folder this site owns, ``None`` when it is homeless.

        Every site-owned path hangs from it by name (``site_home.static``,
        ``site_home.logs``, ...). An explicit ``site_home=`` kwarg wins over the
        configured ``site(home=...)``, the rule host and port already follow.
        """
        return self._site_home

    @property
    def config_host(self) -> str | None:
        """The host from the config's ``server`` section (``None`` if unset)."""
        return self._config_host

    @property
    def config_port(self) -> int | None:
        """The port from the config's ``server`` section (``None`` if unset)."""
        return self._config_port

    @property
    def external_url(self) -> str | None:
        """The server's public base URL, without a trailing slash (``None`` if unset).

        What the server calls ITSELF when it hands its own address to a third
        party — distinct from the ``host``/``port`` it binds to, which differ
        behind a proxy. Declared in the config's ``server`` section; the only
        consumer today is the OIDC ``redirect_uri``, which must be absolute.
        """
        return self._external_url

    def serve(self, host: str | None = None, port: int | None = None) -> None:
        """Boot uvicorn, defaulting host/port to the configured values.

        The caller's explicit ``host``/``port`` win; otherwise the config's
        ``server`` section is used, falling back to the ``BaseServer`` defaults
        (``127.0.0.1`` and an OS-assigned port).
        """
        resolved_host = host if host is not None else (self.config_host or "127.0.0.1")
        resolved_port = port if port is not None else (self.config_port if self.config_port is not None else 0)
        super().serve(host=resolved_host, port=resolved_port)
