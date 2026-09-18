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

"""ConfigurationHandler — the server's read door on its configuration.

A contrib ``ConfigHandler`` subclass: it inherits the callable four-layer read
stack (written value → signature default → call-site ``default=`` → noisy
``KeyError``) and adds the section→kwargs mapping helpers ``AsgiServer.__init__``
consumes. It NEVER builds a server: the server builds ITS OWN handler
(``AsgiServer(config=source)``) and asks these helpers for its kwargs, so there
is one direction of dependency and no materializer.

The helpers read the tree by two rules, and the grammar decides which applies:

- a node with a CLOSED signature is read attribute by attribute THROUGH the
  handler itself, so the element's signature defaults and any resolver sitting
  in an attribute are honored (``server``, ``provider``, ``mount``, ...);
- a node with OPEN ``**kwargs`` has no signature to consult, so its attributes
  are read in bulk through ``builder.runtime_values`` — resolvers resolved,
  everything else verbatim (``application``, ``plugin``, ``database``).

Section → constructor kwarg:

- ``site`` → ``site_name``/``site_home`` (the folder the site owns).
- ``server`` → ``host``/``port``/``external_url``/``max_threads``/``shutdown_timeout_seconds``/``debug``, its
  ``session`` child → ``session_ttl``, its ``tasks`` child → ``tasks``.
- ``middleware`` → ``middleware`` ({name: bool | dict} switches).
- ``authentication`` → ``users``/``tokens`` (the store descriptors ``AuthMixin``
  peels) and ``auth`` (the ``AuthCore`` entries folded from ``credentials``).
- ``storage`` → ``storage`` (genro-storage's own ``list[dict]`` of mounts) and
  ``storage_key`` (the section's at-rest key material).
- ``applications`` → ``applications``/``default`` (each entry an
  ``(app_class, kwargs)`` pair the server instantiates).
- ``databases`` → one descriptor per entry, registered by the server after the
  cooperative chain has run.
- ``plugins`` → ``plugins`` ({code: bool | dict} switches).
- ``openapi`` → no consumer in the core; read and skipped.
- ``applications.<code>.orchestration`` → the orchestration subtree an
  application declares in its own grammar: its three words, and under it
  ``commander`` — the vertex's kwargs and one kwargs set per declared group (the
  two installation paths folded in, the child's own keys gathered into its
  ``worker_kwargs``). The words are none of this dialect's; the helpers only
  read them for the application that owns the subtree.
"""

from __future__ import annotations

from typing import Any

from genro_builders.contrib.config import ConfigHandler

__all__ = ["ConfigError", "ConfigurationHandler"]


class ConfigError(Exception):
    """A configuration recipe names something the runtime cannot honor."""


class ConfigurationHandler(ConfigHandler):
    """Read door over an ``asgiconfig`` tree, plus the section→kwargs mapping."""

    def site_kwargs(self) -> dict[str, Any]:
        """The ``site`` section as server kwargs: ``site_name`` and ``site_home``.

        The element spells them ``name`` and ``home`` because it IS the site;
        the server prefixes both, where ``name`` alone would say nothing.
        """
        declared = self.closed_attrs("site", "name", "home")
        return {f"site_{word}": value for word, value in declared.items()}

    def server_kwargs(self) -> dict[str, Any]:
        """The ``server`` section as server kwargs, its children lifted.

        ``session`` becomes ``session_ttl``, ``save_session`` (its ``save_path``)
        and, when it names a ``store_class``, the ``session_store`` the server is
        handed — the class is instantiated HERE, with the section's remaining
        attributes, ``tasks`` becomes the ``tasks``
        tuning dict and ``websocket`` the websocket options: all three are
        server-domain (sessions, the task backbone and the sockets live on the
        server), so their values lift to the kwargs the owning mixins peel
        while the config keeps them under ``server`` where they belong. The
        ``origins`` of a handshake are written as one comma-separated string in
        a recipe and reach the server as the list it reads.
        """
        kwargs = self.closed_attrs(
            "server",
            "host",
            "port",
            "external_url",
            "max_threads",
            "shutdown_timeout_seconds",
            "debug",
        )
        session = self.node("server.session")
        if session is not None:
            options = self.open_attrs(session)
            ttl = options.pop("ttl", None)
            if ttl is not None:
                kwargs["session_ttl"] = ttl
            save_path = options.pop("save_path", None)
            if save_path is not None:
                kwargs["save_session"] = save_path
            store_class = options.pop("store_class", None)
            if store_class is not None:
                kwargs["session_store"] = store_class(**options)
        if self.node("server.websocket") is not None:
            websocket = self.closed_attrs("server.websocket", "origins", "max_concurrent")
            origins = websocket.get("origins")
            if origins is not None:
                websocket["origins"] = [part.strip() for part in str(origins).split(",") if part.strip()]
            kwargs["websocket"] = websocket
        if self.node("server.tasks") is not None:
            kwargs["tasks"] = self.closed_attrs(
                "server.tasks", "enabled", "tick_seconds", "mount"
            )
        return kwargs

    def middleware_config(self) -> dict[str, Any] | None:
        """The ``middleware`` switches, or ``None`` when the section is absent
        (the composition's own defaults then apply).

        The element's signature is OPEN, so the attributes ARE the switches: the
        five the grammar declares and any name a registry added from outside, read
        in bulk with no list to keep in step."""
        node = self.node("middleware")
        if node is None:
            return None
        return self.open_attrs(node)

    def identity_kwargs(self) -> dict[str, Any]:
        """The identity STORE kwargs of ``authentication`` (``AuthMixin`` peels them).

        ``users``/``tokens`` are the store descriptors: every attribute the
        section carries — ``mount``/``prefix``, the optional ``store_class`` and
        its own kwargs. Nothing else: the server creates no user, so there is no
        bootstrap password to read.
        """
        kwargs: dict[str, Any] = {}
        for tag in ("users", "tokens"):
            node = self.node(f"authentication.{tag}")
            if node is not None:
                kwargs[tag] = self.open_attrs(node)
        return kwargs

    def auth_entries(self) -> dict[str, Any] | None:
        """The ``credentials`` children folded into the ``AuthCore`` sections.

        ``basic_user`` entries are keyed by ``username`` and ``bearer_token``
        entries by ``identity`` — the keys ``AuthCore`` reads back as the
        authenticated identity — while ``jwt`` entries stay an ORDERED list (the
        first verifier that verifies wins). ``None`` when nothing is configured:
        the server then arms no header backend.
        """
        node = self.node("authentication.credentials")
        if node is None:
            return None
        basic: dict[str, Any] = {}
        bearer: dict[str, Any] = {}
        jwt: list[dict[str, Any]] = []
        for child in node.value:
            path = f"authentication.credentials.{child.label}"
            if child.node_tag == "basic_user":
                attrs = self.closed_attrs(path, "username", "password", "tags")
                basic[attrs.pop("username")] = attrs
            elif child.node_tag == "bearer_token":
                attrs = self.closed_attrs(path, "identity", "token", "tags")
                bearer[attrs.pop("identity")] = attrs
            else:
                jwt.append(
                    self.closed_attrs(
                        path, "name", "secret", "public_key", "algorithm", "tags"
                    )
                )
        entries = {"basic": basic, "bearer": bearer, "jwt": jwt}
        return {name: value for name, value in entries.items() if value} or None

    def storage_config(self) -> tuple[list[dict[str, Any]], str | None] | None:
        """The ``storage`` section as ``(mounts, storage_key)``, or ``None`` when it
        is absent (the composition builds its default manager).

        The subtree is written in genro-storage's grammar, so it is flattened
        GENERICALLY into that library's ``list[dict]``: the tag IS the protocol
        and every attribute rides through as-is (``name`` among them — the
        envelope is transparent to containment, so the children carry auto
        labels and their key lives in the attribute the foreign grammar
        declares). This dialect knows no storage vocabulary to translate.

        A section carrying only its ``storage_key`` and no mount is legitimate —
        "the default layout, plus this key" — so it yields an EMPTY mount list
        rather than an error; the composition reads that as "use the default
        ``site:``/``home:`` layout". With ``BaseConfiguration`` layered
        underneath the merged tree normally carries those two mounts anyway, so
        this is the shape a handler built without parents produces.
        """
        node = self.node("storage")
        if node is None:
            return None
        mounts: list[dict[str, Any]] = []
        for child in node.value or ():
            mount = self.open_attrs(child)
            mount["protocol"] = child.node_tag
            mounts.append(mount)
        return mounts, self("storage.storage_key", default=None)

    def plugins_config(self) -> dict[str, bool | dict[str, Any]] | None:
        """The ``plugins`` switches as ``{code: bool | dict}``, or ``None`` when
        the section is absent (the composition arms no extra plugin).

        A plugin maps to ``False`` when ``enabled`` is explicitly false, to its
        remaining options when it carries any, else to ``True``. The collection's
        OWN attributes are switches too — the short form, and the only one a
        plugin registered from outside can use.
        """
        node = self.node("plugins")
        if node is None:
            return None
        switches: dict[str, bool | dict[str, Any]] = dict(self.open_attrs(node))
        for child in node.value or ():
            options = self.open_attrs(child)
            options.pop("code", None)
            enabled = options.pop("enabled", True)
            if not enabled:
                switches[child.label] = False
            else:
                switches[child.label] = options or True
        return switches

    def applications(self) -> tuple[list[tuple[type, dict[str, Any]]], str | None]:
        """The declared applications as ``(app_class, kwargs)`` pairs, plus ``default``.

        Every attribute of the envelope except ``app_class`` is a constructor
        kwarg of the application — ``code`` and ``mount`` included, since the app
        owns their resolution. The mounted subtree is NOT passed: an application
        reads its own configuration back through the handler
        (``applications.<code>.<path>``), it never receives a slice of the tree.
        """
        node = self.node("applications")
        if node is None:
            return [], None
        entries: list[tuple[type, dict[str, Any]]] = []
        for child in node.value or ():
            kwargs = self.open_attrs(child)
            entries.append((kwargs.pop("app_class"), kwargs))
        return entries, self("applications.default", default=None)

    def databases(self) -> list[dict[str, Any]]:
        """The ``databases`` descriptors as ``{code, db_class, db_handler_class, params}``.

        ``db_handler_class`` is ``None`` when the recipe omits it (the server
        substitutes its default) and ``params`` are the remaining connection
        kwargs handed to ``db_class(**params)``.
        """
        node = self.node("databases")
        if node is None:
            return []
        descriptors: list[dict[str, Any]] = []
        for child in node.value:
            params = self.open_attrs(child)
            params.pop("code", None)
            descriptors.append(
                {
                    "code": child.label,
                    "db_class": params.pop("db_class"),
                    "db_handler_class": params.pop("db_handler_class", None),
                    "params": params,
                }
            )
        return descriptors

    def orchestration_kwargs(self, code: str) -> dict[str, Any] | None:
        """One application's orchestration node, or ``None`` when it has none.

        Args:
            code: the application whose orchestration this is — the words live
                under ``applications.<code>.orchestration``.

        Returns:
            ``profiles_path``, ``profile_name`` and ``control_enabled``, the
            three the recipe actually wrote, or ``None`` when the node is absent
            — an application that declares no pool at all.
        """
        if self.node(f"applications.{code}.orchestration") is None:
            return None
        return self.closed_attrs(
            f"applications.{code}.orchestration",
            "profiles_path",
            "profile_name",
            "control_enabled",
        )

    def commander_kwargs(self, code: str) -> dict[str, Any] | None:
        """The pool of one application as its vertex's own constructor kwargs.

        Args:
            code: the application whose pool this is — a pool belongs to the
                application that owns it, so the words live under
                ``applications.<code>.orchestration.commander``.

        Returns:
            The vertex's kwargs, or ``None`` when the node is absent — an
            orchestration node with no commander under it, which the owning
            application refuses.

        ``instance_dir`` is NOT among them: the sockets are the workers' business,
        so that path is folded into every group instead (``group_kwargs``). The
        group ELECTED to receive a newcomer is declared one level down, on the
        collection, and is folded in here because the vertex is what reads it.
        What the recipe leaves out is left out, and the vertex's own default
        answers.
        """
        section = f"applications.{code}.orchestration.commander"
        if self.node(section) is None:
            return None
        kwargs = self.closed_attrs(
            section,
            "frozen_users_path",
            "memory_max_percent",
            "machine_memory_alarm_percent",
            "orchestration_log_path",
            "orchestration_log_max_bytes",
            "orchestration_log_backup_count",
            "user_expiry_hours",
            "guest_expiry_hours",
            "cpu_temperature_sample_seconds",
        )
        elected = self(f"{section}.groups.default", default=None)
        if elected is not None:
            kwargs["default_group"] = elected
        return kwargs

    def group_kwargs(self, code: str) -> dict[str, dict[str, Any]]:
        """One application's groups as ``{name: kwargs}``, one ``GroupHandler`` each.

        Args:
            code: the application whose pool these groups belong to.

        The two paths of the installation live on ``commander`` and are folded in
        here, because a group is what builds the workers that need them. The one
        key the CHILD reads travels in its own ``worker_kwargs``: the group's
        name, which stamps every item it writes. So a recipe writes each policy
        once, on the rung it belongs to, and the child is handed what is his.
        """
        section = f"applications.{code}.orchestration.commander"
        node = self.node(f"{section}.groups")
        if node is None:
            return {}
        shared = self.closed_attrs(section, "frozen_users_path", "instance_dir")
        groups: dict[str, dict[str, Any]] = {}
        for child in node.value:
            path = f"{section}.groups.{child.label}"
            kwargs = self.closed_attrs(
                path,
                "memory_max_percent",
                "worker_max_number",
                "worker_memory_max_percent",
                "worker_memory_admission_percent",
                "restart_occupancy_max_percent",
                "cpu_close_percent",
                "cpu_admission_close_percent",
                "cpu_admission_reopen_percent",
                "cpu_offload_percent",
                "cpu_retirement_quiet_seconds",
                "cpu_heating_seconds",
                "cpu_cooling_seconds",
                "worker_admission_interval_seconds",
                "worker_min_life_seconds",
                "worker_max_users",
                "user_idle_freeze_minutes",
                "entry_module",
                "executable",
                "worker_class",
                "main_threadpool_size",
                "aux_threadpool_size",
                "worker_kwargs",
                "engine_factory",
                "engine_kwargs",
            )
            worker_kwargs = dict(kwargs.pop("worker_kwargs", None) or {}, group=child.label)
            groups[child.label] = {**shared, **kwargs, "worker_kwargs": worker_kwargs}
        return groups

    def node(self, path: str) -> Any:
        """The node at ``path`` (relative to the root element), or ``None``."""
        return self.builder.source.get_node(f"{self.root_label}.{path}")

    def closed_attrs(self, path: str, *names: str) -> dict[str, Any]:
        """Read ``names`` at ``path`` through the read stack, skipping the absent.

        One four-layer read per attribute, so a resolver sitting in an attribute
        resolves and the element's signature defaults apply. ``None`` means
        "not configured and no default" and is left out — the consumer's own
        default then applies.
        """
        attrs: dict[str, Any] = {}
        for name in names:
            value = self(f"{path}.{name}", default=None)
            if value is not None:
                attrs[name] = value
        return attrs

    def open_attrs(self, node: Any) -> dict[str, Any]:
        """Every attribute ``node`` carries, resolvers resolved.

        The read for elements whose signature is OPEN (``**kwargs``): there is
        no declared attribute list to walk and no signature default to consult,
        so the node's own attributes are the whole truth.
        """
        return dict(self.builder.runtime_values(node)[1])
