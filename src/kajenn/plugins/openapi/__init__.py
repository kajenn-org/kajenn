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

"""OpenAPI dialect for kajenn.

genro-routes exposes a dialect-neutral description of each endpoint via
``router.nodes()`` — including a per-entry ``result`` block ``{schema,
media_type}``. This package is the OpenAPI *reader* of that description: it
owns the ``OpenAPIPlugin`` (per-handler OpenAPI config: method/tags/summary/
security) and the ``OpenAPITranslator`` (turning ``nodes()`` output into
OpenAPI paths). It lives here, not in the routing core, because OpenAPI is one
transport dialect among peers (alongside MCP), not a routing concern.

Importing this package has NO side effect on genro-routes: the plugin is
registered only when a server arms a router (``PluginMixin.arm_router``).
"""

from __future__ import annotations

from typing import Any

from .plugin import OpenAPIPlugin
from .translator import OpenAPITranslator

__all__ = ["OpenAPIPlugin", "OpenAPITranslator", "router_openapi"]


def router_openapi(
    router: Any,
    *,
    basepath: str | None = None,
    hierarchical: bool = False,
    lazy: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build the OpenAPI view of a router from its neutral ``nodes()`` output.

    genro-routes ships no OpenAPI dialect; this composes the neutral
    description with the local translator.

    Args:
        router: A router (anything with ``nodes()``).
        basepath: Optional subtree to start from; paths are made absolute.
        hierarchical: If True, use the ``h_openapi`` (tree-preserving) format.
        lazy: If True, child routers stay as references.
        **kwargs: Filters forwarded to ``nodes()`` (auth_tags, env_capabilities,
            channel_channel, forbidden, pattern, ...).

    Returns:
        OpenAPI dict with ``paths`` (and ``$defs`` when nested types exist).
    """
    translate = (
        OpenAPITranslator.translate_h_openapi
        if hierarchical
        else OpenAPITranslator.translate_openapi
    )
    nodes_data = router.nodes(basepath=basepath, lazy=lazy, **kwargs)
    if not nodes_data:
        return {"paths": {}}
    if basepath:
        prefix = "/" + basepath.strip("/")
        return translate(nodes_data, lazy=lazy, path_prefix=prefix)
    return translate(nodes_data, lazy=lazy)
