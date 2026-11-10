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

"""The ``_server/k8s`` section: the default answers of the health probes.

``K8sSection`` is a ``RoutingClass`` the ``ServerApplication`` attaches under
``k8s``; the server application gives its three routes as the defaults of the
well-known paths ``/healthz``, ``/livez`` and ``/readyz`` (#105):

    ``/_server/k8s/healthz``   the process is listening
    ``/_server/k8s/livez``     the process is alive
    ``/_server/k8s/readyz``    the server takes requests

Each answers ``ok`` whenever it is reached. While the server is not RUNNING the
entry point refuses every HTTP request with 503 before the demux, so the three
probes answer 503 during shutdown and a balancer stops sending traffic before
the server closes. The routes declare no rule: a probe carries no credentials.
An application that knows better — its own database, its own warm-up —
declares the path in its ``well_known`` and answers it instead.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from genro_routes import RoutingClass, route

from kajenn.exceptions import HTTPException

if TYPE_CHECKING:
    from ..server_app import ServerApplication

__all__ = ["K8sSection"]

PROBE_ANSWER = "ok"


class K8sSection(RoutingClass):
    """The ``_server/k8s`` mount: the default health probes.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application

    @route(media_type="text/plain")
    def healthz(self) -> str:
        """The process is listening. Route: GET /healthz"""
        return PROBE_ANSWER

    @route(media_type="text/plain")
    def livez(self) -> str:
        """The process is alive. Route: GET /livez"""
        return PROBE_ANSWER

    @route(media_type="text/plain")
    def readyz(self) -> str:
        """The server takes requests. Route: GET /readyz

        503 while the public part is closed: a fatal boot kept the server up
        for ``_server`` only.
        """
        if self.application.server.public_closed:
            raise HTTPException(503, "not ready: the public part is closed")
        return PROBE_ANSWER
