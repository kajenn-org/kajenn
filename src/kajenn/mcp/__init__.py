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

"""MCP support: the JSON-RPC engine over genro-routes routers.

``engine`` holds :class:`McpEngine` (envelope, resolution) and
:class:`McpDispatcher` (the tree of methods), ``tools`` the ``tools/*`` family,
``jsonrpc`` the error codes and :class:`McpError`. The transport shells
(``McpApplication``, ``McpOpenApiApplication``) live in ``applications`` and
drive :class:`McpEngine` with their own invoke callback.
"""

from __future__ import annotations

from .engine import McpDispatcher, McpEngine
from .jsonrpc import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_NOT_AUTHORIZED,
    McpError,
)
from .tools import McpTools

__all__ = [
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_REQUEST",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_NOT_AUTHORIZED",
    "McpDispatcher",
    "McpEngine",
    "McpError",
    "McpTools",
]
