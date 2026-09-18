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

"""OpenAPIPlugin — per-handler OpenAPI configuration.

Provides explicit control over OpenAPI schema generation for handlers. Use
this plugin to override automatically guessed HTTP methods or add
OpenAPI-specific metadata like tags, summary, description, and security.

Accepted config keys (router-level or per-handler):
    - ``enabled``: gate the plugin entirely (default True)
    - ``method``: HTTP method override (e.g. "get", "post", "delete")
    - ``tags``: OpenAPI tags (string or list of strings)
    - ``summary``: summary override for the operation
    - ``description``: description override for the operation
    - ``deprecated``: mark the operation as deprecated (default False)
    - ``security_scheme``: security scheme name (default "BearerAuth")
    - ``security``: explicit security override (list, or [] for public)

Cross-plugin integration (read by ``OpenAPITranslator``):
    - When the auth plugin is active, ``security`` is auto-derived from ``auth_rule``.
    - When the env plugin is active, ``x-requires`` is auto-derived from ``env_requires``.

This file does NOT register itself against genro-routes at import time (the
no-global-state / no-import-side-effect rule): registration is an explicit
arming act performed by ``PluginMixin.arm_router``.
"""

from __future__ import annotations

from typing import Any

from genro_routes.plugins._base_plugin import BasePlugin, MethodEntry

__all__ = ["OpenAPIPlugin"]


class OpenAPIPlugin(BasePlugin):
    """OpenAPI plugin for explicit schema control.

    By default HTTP methods are guessed from the signature (GET for scalar
    params, POST for complex types). Use this plugin to override the guessed
    method or add OpenAPI-specific metadata.
    """

    plugin_code = "openapi"
    plugin_description = "Provides explicit control over OpenAPI schema generation"

    def configure(  # type: ignore[override]
        self,
        enabled: bool = True,
        method: str | None = None,
        tags: str | list[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        deprecated: bool = False,
        security_scheme: str = "BearerAuth",
        security: list | None = None,
    ) -> None:
        """Configure OpenAPI plugin options (storage handled by the wrapper)."""

    def entry_metadata(self, router: Any, entry: MethodEntry) -> dict[str, Any]:
        """Provide OpenAPI-specific metadata for a handler."""
        cfg = self.configuration(entry.name)
        metadata: dict[str, Any] = {}

        if cfg.get("method"):
            metadata["method"] = cfg["method"]
        if cfg.get("tags"):
            metadata["tags"] = cfg["tags"]
        if cfg.get("summary"):
            metadata["summary"] = cfg["summary"]
        if cfg.get("description"):
            metadata["description"] = cfg["description"]
        if cfg.get("deprecated"):
            metadata["deprecated"] = cfg["deprecated"]
        if cfg.get("security_scheme") and cfg["security_scheme"] != "BearerAuth":
            metadata["security_scheme"] = cfg["security_scheme"]
        if cfg.get("security") is not None:
            metadata["security"] = cfg["security"]

        return {"openapi": metadata} if metadata else {}
