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

"""The ready-made configurations, and the shortcut that customises one.

The configuration exists ALWAYS. ``DefaultConfiguration`` is the usual assembly
shipped complete and valid — the listener section, the storage layout and an
empty application collection — and ``CONFIGURATION_TEMPLATES`` names it, so it
can be asked for by name (``AsgiServer(config="default")``, ``kajenn serve
template=default``) exactly like a ``config.py`` path.

``ShortcutConfiguration`` is what makes a server composed in code the SAME road
as a server written as a recipe: it is an ordinary recipe whose ``main`` writes
the constructor kwargs it was handed, and it is layered ON TOP of a template by
``AsgiServer``. From the handler down nothing knows which of the two roads was
taken: there is one tree, read through one door.

The shortcut writes every option it receives — the ``site`` identity (``site_name``
and ``site_home``, the folder the site owns), the ``server`` scalars (``debug``
among them), the session ttl, the ``middleware`` and ``plugins`` switches (both
elements have an OPEN signature, so a name registered from outside is an
attribute like any other) and one ``application`` node per declared class — and
POPS each one, so the value is read back from the tree and from nowhere else.
"""

from __future__ import annotations

from typing import Any

from genro_storage import StorageManager

from .builder import AsgiConfigBuilder, BaseConfiguration

__all__ = [
    "CONFIGURATION_TEMPLATES",
    "DEFAULT_TEMPLATE",
    "DefaultConfiguration",
    "ShortcutConfiguration",
]


class DefaultConfiguration(BaseConfiguration):
    """The usual assembly, complete and valid: listener, storage, applications.

    ``BaseConfiguration`` brings the shipped defaults (the bare ``server``
    section and the single ``site:`` mount); this adds the empty ``applications``
    collection, so the section a shortcut or a site recipe fills always exists.
    """

    def main(self, root: Any) -> None:
        """The default document: site, server, storage, applications."""
        cfg = root.configuration()
        self.site_section(cfg)
        self.server_section(cfg)
        self.storage_section(cfg)
        self.applications_section(cfg)

    def applications_section(self, cfg: Any) -> None:
        """The application collection, empty — a site fills it."""
        cfg.applications()


DEFAULT_TEMPLATE = "default"
"""The template a server takes when its caller names none."""

CONFIGURATION_TEMPLATES: dict[str, type] = {DEFAULT_TEMPLATE: DefaultConfiguration}
"""Ready-made configurations by name, for ``config=<name>`` and the CLI."""


class ShortcutConfiguration(AsgiConfigBuilder):
    """The constructor kwargs of a server, written as the top layer of a recipe.

    Built with the kwargs dict itself, which it MUTATES: every option it writes
    into the tree it pops, so the server reads that value back through the
    handler like any other and no option survives at the constructor.

    ``applications`` is a list of CLASSES, or of ``(class, params)`` pairs when
    an application needs its own ``code``/``mount``/kwargs — the shape of the
    ``application`` grammar line, which is what it becomes. The server
    instantiates from the tree, here exactly as for a written configuration.
    """

    server_words = (
        "host",
        "port",
        "external_url",
        "max_threads",
        "shutdown_timeout_seconds",
        "debug",
    )
    """The ``server`` attributes a constructor kwarg spells the same way."""

    def __init__(self, kwargs: dict[str, Any], name: str | None = None) -> None:
        super().__init__(name)
        self.site_name = kwargs.pop("site_name", None)
        self.site_home = kwargs.pop("site_home", None)
        self.server_options = {
            word: kwargs.pop(word) for word in self.server_words if kwargs.get(word) is not None
        }
        self.session_ttl = kwargs.pop("session_ttl", None)
        self.default_code = kwargs.pop("default", None)
        self.middleware_switches = kwargs.pop("middleware", None)
        self.plugin_switches = kwargs.pop("plugins", None)
        self.session_store = kwargs.pop("session_store", None)
        self.session_save_path = kwargs.pop("save_session", None)
        self.websocket_options = kwargs.pop("websocket", None)
        self.storage_mounts_declared = kwargs.pop("storage", None)
        self.storage_key = kwargs.pop("storage_key", None)
        self.user_store = kwargs.pop("users", None)
        self.api_key_store = kwargs.pop("tokens", None)
        self.credentials = kwargs.pop("auth", None)
        self.app_entries = self.application_entries(kwargs.pop("applications", ()))

    def application_entries(self, declared: Any) -> list[tuple[type, dict[str, Any]]]:
        """The declared applications as ``(class, params)``, each with its ``code``.

        A bare class is the same entry with no parameters. The code is resolved
        HERE because it is the collection key the tree files the node under, and
        it follows the rule the application applies to itself: what the caller
        wrote, else the class attribute, else the class name lowercased.
        """
        entries: list[tuple[type, dict[str, Any]]] = []
        for entry in declared:
            app_class, params = self.class_and_params(entry)
            code = params.pop("code", None) or app_class.code or app_class.__name__.lower()
            entries.append((app_class, {"code": code, **params}))
        return entries

    def main(self, root: Any) -> None:
        """Every section the kwargs describe, each in the grammar's own words."""
        cfg = root.configuration()
        self.site_section(cfg)
        self.server_section(cfg)
        self.middleware_section(cfg)
        self.plugins_section(cfg)
        self.storage_section(cfg)
        self.authentication_section(cfg)
        self.applications_section(cfg)

    def server_section(self, cfg: Any) -> None:
        """The listener words, and the two children: the session and the handshake."""
        section = cfg.server(**self.server_options)
        session = self.session_options
        if session:
            section.session(**session)
        if self.websocket_options is not None:
            section.websocket(**self.handshake_options)

    @property
    def handshake_options(self) -> dict[str, Any]:
        """The ``websocket`` attributes: the recipe spells ``origins`` as one string."""
        options = dict(self.websocket_options)
        origins = options.get("origins")
        if origins is not None and not isinstance(origins, str):
            options["origins"] = ",".join(origins)
        return options

    @property
    def session_options(self) -> dict[str, Any]:
        """The ``session`` attributes: the ttl, the snapshot path, the store class."""
        options: dict[str, Any] = {}
        if self.session_ttl is not None:
            options["ttl"] = self.session_ttl
        if self.session_save_path is not None:
            options["save_path"] = str(self.session_save_path)
        if self.session_store is not None:
            store_class, params = self.class_and_params(self.session_store)
            options["store_class"] = store_class
            options.update(params)
        return options

    def middleware_section(self, cfg: Any) -> None:
        """The switches as the attributes of the section — the element is open."""
        if self.middleware_switches is not None:
            cfg.middleware(**self.middleware_switches)

    def plugins_section(self, cfg: Any) -> None:
        """The switches as the attributes of the collection — the short form."""
        if self.plugin_switches is not None:
            cfg.plugins(**self.plugin_switches)

    def storage_section(self, cfg: Any) -> None:
        """The declared mounts in genro-storage's own words — the tag IS the protocol.

        An empty list is written as the bare section, which the composition reads
        as "the default layout"; nothing declared leaves the template's section
        alone — unless a home was declared, whose whole point is to be what
        ``site:`` is anchored on.
        """
        declared = (self.storage_mounts_declared, self.storage_key, self.site_home)
        if all(item is None for item in declared):
            return
        section = cfg.storage(app=StorageManager, storage_key=self.storage_key)
        if self.storage_mounts_declared is None:
            self.storage_mounts(section)
            return
        for mount in self.storage_mounts_declared:
            attrs = dict(mount)
            getattr(section, attrs.pop("protocol"))(**attrs)

    def authentication_section(self, cfg: Any) -> None:
        """The identity stores and the header credentials, each in its own element."""
        declared = (self.user_store, self.api_key_store, self.credentials)
        if all(item is None for item in declared):
            return
        section = cfg.authentication()
        for tag, declared in (("users", self.user_store), ("tokens", self.api_key_store)):
            if declared is not None:
                getattr(section, tag)(**declared)
        if self.credentials:
            self.credentials_section(section.credentials())

    def credentials_section(self, credentials: Any) -> None:
        """One child per header credential: the three tags key differently."""
        for username, entry in (self.credentials.get("basic") or {}).items():
            credentials.basic_user(username=username, **entry)
        for identity, entry in (self.credentials.get("bearer") or {}).items():
            credentials.bearer_token(identity=identity, **entry)
        for entry in self.credentials.get("jwt") or ():
            credentials.jwt(**entry)

    def class_and_params(self, declared: Any) -> tuple[type, dict[str, Any]]:
        """A declared class, or a ``(class, params)`` pair, as the pair it is."""
        if isinstance(declared, tuple):
            return declared[0], dict(declared[1])
        return declared, {}

    def applications_section(self, cfg: Any) -> None:
        """One ``application`` node per declared class: its grammar, its parameters.

        The class carries the grammar, so the subtree an application reads its
        own options from (``applications.<code>.request``, and whatever it
        declares) exists for a server composed in code exactly as it does for
        one written as a recipe.
        """
        apps = cfg.applications(default=self.default_code)
        for app_class, params in self.app_entries:
            apps.application(**params, app_class=app_class)
