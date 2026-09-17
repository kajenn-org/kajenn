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

"""JSON-RPC error codes and :class:`McpError`, shared by the engine and the method families.

The codes are the ones the transport renders in the ``error`` object of a
JSON-RPC response. ``McpError`` carries one of them with a message; every
protocol failure in the ``mcp`` package raises it and nothing else.
"""

from __future__ import annotations

__all__ = [
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_REQUEST",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_NOT_AUTHORIZED",
    "McpError",
]

JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_NOT_AUTHORIZED = -32000


class McpError(Exception):
    """Carries a JSON-RPC error code + message for the transport to render."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
