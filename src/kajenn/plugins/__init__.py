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

"""Transport-dialect plugins for the routing core.

genro-routes ships the routing-concern plugins (auth, channel, env, logging,
pydantic); this package holds the transport-dialect plugins that read the
neutral ``router.nodes()`` description — today the OpenAPI dialect, the MCP
face later. None of them register against genro-routes at import time: a
server arms them explicitly through ``PluginMixin.arm_router``.
"""

from __future__ import annotations

from .openapi import OpenAPIPlugin, OpenAPITranslator, router_openapi

__all__ = ["OpenAPIPlugin", "OpenAPITranslator", "router_openapi"]
