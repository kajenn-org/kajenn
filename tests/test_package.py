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

"""Contract: the version is the installed one, the public api is the declared one,
and no module of the core reaches kajenn_server_app."""

import subprocess
import sys
from importlib.metadata import version

import kajenn


def test_version():
    # __version__ IS the installed distribution's version — never a literal
    # that a release bump can leave behind.
    assert kajenn.__version__ == version("kajenn")


def test_root_exports_public_api():
    expected = [
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
    assert kajenn.__all__ == expected
    for name in expected:
        assert hasattr(kajenn, name)


def test_importing_the_core_loads_no_module_of_the_server_app_package():
    # The core imports nothing of kajenn_server_app, in the root package and in
    # every submodule. Asked in a fresh interpreter, because this one has already
    # imported the server app through the tests that exercise it.
    probe = (
        "import pkgutil, sys\n"
        "import kajenn\n"
        "for found in pkgutil.walk_packages(kajenn.__path__, 'kajenn.'):\n"
        "    __import__(found.name)\n"
        "print(sorted(name for name in sys.modules if name.startswith('kajenn_server_app')))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert loaded == "[]"
