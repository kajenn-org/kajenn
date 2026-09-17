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

"""Plugin capability: router plugins as a mixin over the base server (D16).

The base server knows nothing about router plugins. This mixin adds them as a
capability, composed BEFORE the server class (``class MyServer(PluginMixin,
BaseServer)``), mirroring ``MiddlewareMixin``: its cooperative ``__init__``
peels ``plugins=`` (the ``{name: bool | dict}`` switches — a dict value enables
the plugin and becomes its plug options) and ``plugin_registry=`` (extra
``{name: class}`` entries merged over ``default_plugin_registry()``), and
exposes ``arm_router(router)``.

Unlike the middleware chain — assembled once around the base dispatch —
plugins are armed onto the ROUTER of each routed application: ``arm_router``
is called by ``RoutedApplication`` on first ``route`` access. Per enabled
plugin it ensures the plugin class is registered with genro-routes (idempotent),
then batch-plugs the enabled set with a single ``router.plug([...])`` call
(genro-routes 0.28.0 accepts a list of plugin dicts); bundled genro-routes
plugins (auth/channel/env/logging/pydantic) carry no class in the registry and
are plugged by name alone. A composition WITHOUT this mixin exposes no
``arm_router`` and arms nothing — ``RoutedApplication`` degrades silently.

``default_plugin_registry()`` returns a FRESH dict per call (``{"openapi":
OpenAPIPlugin}`` as of Phase 5) — a function, so no module-level mutable
registry exists; and importing this module never registers a plugin against
genro-routes (no import side effect).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import Router

from .plugins.openapi import OpenAPIPlugin

if TYPE_CHECKING:
    from genro_routes.plugins._base_plugin import BasePlugin

__all__ = ["PluginMixin", "default_plugin_registry"]


#: The plugins every server arms unconditionally — the fixed structure of a
#: routed core, not a config choice. ``pydantic`` captures handler signatures
#: into the neutral ``params``/``result`` blocks; ``openapi`` carries the
#: per-entry schema controls (``openapi_method`` and friends). The ``plugins``
#: config section only ADDS extras over this base; disabling a fixed plugin is
#: a config error, not an opt-out.
FIXED_PLUGINS: tuple[str, ...] = ("pydantic", "openapi")


def default_plugin_registry() -> dict[str, type[BasePlugin]]:
    """A fresh ``{name: class}`` mapping of the plugins this core arms itself.

    Only the transport-dialect plugins that live in this package need a class
    here (so ``arm_router`` can register them). genro-routes' own bundled
    plugins are plugged by name without a class.
    """
    return {"openapi": OpenAPIPlugin}


class PluginMixin:
    """Router-plugin capability mixin, composed BEFORE a server class.

    Every server arms the ``FIXED_PLUGINS`` (``pydantic``/``openapi``)
    unconditionally — the fixed structure of a routed core. Constructor kwargs
    peeled here: ``plugins`` — the ``{name: bool | dict}`` switches tuning the
    base and adding extras (a dict value becomes the plug options; ``False``
    drops an EXTRA but is a config error on a fixed plugin);
    ``plugin_registry`` — extra ``{name: class}`` entries merged over
    ``default_plugin_registry()``.
    """

    def __init__(self, **kwargs: Any) -> None:
        plugins: dict[str, bool | dict[str, Any]] | None = kwargs.pop("plugins", None)
        extra: dict[str, type[BasePlugin]] | None = kwargs.pop("plugin_registry", None)
        super().__init__(**kwargs)
        registry = default_plugin_registry()
        if extra:
            registry.update(extra)
        self._plugin_registry = registry
        self._plugins_config = self._resolve_plugins(plugins or {})

    def _resolve_plugins(
        self, config: dict[str, bool | dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Merge the config switches over the fixed base into ``{name: options}``.

        The result always carries the ``FIXED_PLUGINS`` (``pydantic``/``openapi``)
        — the fixed structure of a routed core. The ``{name: bool | dict}``
        config only tunes the base and adds extras: a dict value is the plug
        options; a truthy scalar enables an extra with no options;
        ``False``/``None`` drops an EXTRA. Disabling a fixed plugin
        (``{"openapi": False}``) is a config error, not an opt-out.
        """
        resolved: dict[str, dict[str, Any]] = {name: {} for name in FIXED_PLUGINS}
        for name, value in config.items():
            if not value and name in FIXED_PLUGINS:
                raise ValueError(f"Plugin '{name}' is fixed structure and cannot be disabled")
            if isinstance(value, dict):
                resolved[name] = value
            elif value:
                resolved[name] = {}
            else:
                resolved.pop(name, None)
        return resolved

    @property
    def plugins(self) -> dict[str, dict[str, Any]]:
        """The materialized ``{name: options}`` config of the enabled plugins."""
        return dict(self._plugins_config)

    @property
    def plugin_registry(self) -> dict[str, type[BasePlugin]]:
        """The effective ``{name: class}`` registry (defaults + extras)."""
        return dict(self._plugin_registry)

    def arm_router(self, router: Router) -> None:
        """Register and plug every enabled plugin onto ``router`` (idempotent).

        A plugin carrying a class in the registry is registered with
        genro-routes first (guarded — never re-registered); the not-yet-attached
        plugins are then armed with a single batch ``router.plug([...])`` call, so
        arming twice is safe (already-attached plugins are skipped). Unknown
        plugin names surface as ``router.plug`` errors, not silent no-ops.
        """
        plugged = {plugin.name for plugin in router.iter_plugins()}
        specs: list[dict[str, Any]] = []
        for name, options in self._plugins_config.items():
            cls = self._plugin_registry.get(name)
            if cls is not None and name not in Router.available_plugins():
                Router.register_plugin(cls)
            if name not in plugged:
                specs.append({"name": name, **options})
        if specs:
            router.plug(specs)
