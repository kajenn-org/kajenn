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

"""The public surface of the kajenn server core.

Every name a site composes a server from is imported here and listed in
``__all__``: the base server and the shipped composition, the app-side
contract and its routed base, the configuration grammar and its handler, the
capability mixins (auth, sessions, middleware, plugins, storage,
communication), the request/response pair, the HTTP exceptions, the channel
and MCP surfaces, the tasks grammar and the ASGI type aliases.

``__version__`` is read from the installed distribution.
"""

from importlib.metadata import version as _distribution_version

from .application import ApplicationGrammar, BaseApplication
from .applications import (
    McpApplication,
    McpOpenApiApplication,
    OpenApiApplication,
)
from .asgi_server import AsgiServer
from .auth import (
    ApiKeyStore,
    AuthCore,
    AuthMixin,
    FileApiKeyStore,
    FileUserStore,
    UserStore,
)
from .channel import ChannelClient, Frame, FrameStream
from .communication import CommunicationMixin
from .config import (
    AsgiConfigBuilder,
    AsgiServerGrammar,
    BaseConfiguration,
    ConfigError,
    ConfigurationHandler,
    DefaultConfig,
)
from .db import AsgiDbHandlerBase
from .exceptions import (
    HTTPBadRequest,
    HTTPException,
    HTTPForbidden,
    HTTPNotFound,
    HTTPUnauthorized,
    HTTPUnprocessableContent,
    HTTPUnsupportedMediaType,
    Redirect,
)
from .mcp import McpEngine, McpError
from .middleware import BaseMiddleware, MiddlewareMixin
from .plugin_mixin import PluginMixin
from .plugins import OpenAPIPlugin, OpenAPITranslator, router_openapi
from .request import Request, UploadedFile
from .request_registry import RegisteredRequest, RequestRegistry
from .response import Response
from .routed_application import RoutedApplication
from .server import BaseServer
from .session import (
    Avatar,
    MemorySessionStore,
    Session,
    SessionMixin,
    SessionStore,
)
from .site_home import SiteHome
from .storage_mixin import StorageMixin
from .tasks import TaskGrammar
from .types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "ASGIApp",
    "ApiKeyStore",
    "ApplicationGrammar",
    "AsgiConfigBuilder",
    "AsgiDbHandlerBase",
    "AsgiServer",
    "AsgiServerGrammar",
    "AuthCore",
    "AuthMixin",
    "Avatar",
    "BaseApplication",
    "BaseConfiguration",
    "BaseMiddleware",
    "BaseServer",
    "ChannelClient",
    "CommunicationMixin",
    "ConfigError",
    "ConfigurationHandler",
    "DefaultConfig",
    "FileApiKeyStore",
    "FileUserStore",
    "Frame",
    "FrameStream",
    "HTTPBadRequest",
    "HTTPException",
    "HTTPForbidden",
    "HTTPNotFound",
    "HTTPUnauthorized",
    "HTTPUnprocessableContent",
    "HTTPUnsupportedMediaType",
    "McpApplication",
    "McpEngine",
    "McpError",
    "McpOpenApiApplication",
    "Message",
    "MemorySessionStore",
    "MiddlewareMixin",
    "OpenAPIPlugin",
    "OpenAPITranslator",
    "OpenApiApplication",
    "PluginMixin",
    "Receive",
    "Redirect",
    "RegisteredRequest",
    "Request",
    "RequestRegistry",
    "Response",
    "RoutedApplication",
    "Scope",
    "Send",
    "Session",
    "SessionMixin",
    "SessionStore",
    "SiteHome",
    "StorageMixin",
    "TaskGrammar",
    "UploadedFile",
    "UserStore",
    "__version__",
    "router_openapi",
]

# Derived from the installed distribution: pyproject.toml is the single place a
# release bump touches, so the two cannot drift apart.
__version__ = _distribution_version("kajenn")
