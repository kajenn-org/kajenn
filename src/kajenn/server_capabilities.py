# Copyright 2025-2026 Softwell S.r.l.
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

"""ServerCapabilities: the facts of the server a route can require.

``RoutedApplication`` plugs the genro-routes ``env`` plugin and sets its
``capabilities`` to a ``ServerCapabilities`` bound to the application. A route
declares what it needs with ``@route(env_requires="<capability>")``; a request
reaching it when the capability is off is refused by the router with
``not_available``. Every capability is evaluated when a request resolves the
route, never cached at mount: it reads the server the application is attached
to at that moment.

Capabilities:

- ``external_links`` — the server has an ``external_url``, its public base
  address. A route that hands an absolute link of this server to the outside
  (the OIDC ``redirect_uri`` of the ``_auth`` application) requires it.
"""

from __future__ import annotations

from typing import Any

from genro_routes.plugins.env import CapabilitiesSet, capability

__all__ = ["ServerCapabilities"]


class ServerCapabilities(CapabilitiesSet):
    """The server facts of one application, read at request time."""

    def __init__(self, application: Any) -> None:
        self._application = application

    @property
    def application(self) -> Any:
        """The application whose server these capabilities describe."""
        return self._application

    @capability
    def external_links(self) -> bool:
        """True when the application's server has an ``external_url``.

        False while the application is attached to no server.
        """
        server = self.application.server
        return server is not None and server.external_url is not None
