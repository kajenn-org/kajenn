# Copyright 2026 Softwell S.r.l.
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

"""The server families of credential checks, WSX messages and websocket connections.

Private to ``kajenn``: the server reaches it through ``server.event_metrics``.

``EventMetrics`` is held by the server (``server.event_metrics``) and owns
``prometheus_client`` metric objects created with ``registry=None``: they
belong to the server instance, never to the module-level ``REGISTRY``.
``ServerApplication.prometheus_metrics`` writes ``collect()`` into its text.

Who counts what:

- credential checks: ``AuthSection.authenticate`` (the verification a cache
  miss of ``server.authenticate_credential`` reaches, for in-process and
  spawned applications alike), ``ServerApplication.login`` (``password``) and
  the OIDC ``callback`` (``oidc``). A cache hit is not a check.
- WSX messages: ``WsxConnection`` for every message it hands to the demux; the
  ping it answers inline is not a message of an application.
- websocket connections: ``BaseServer.on_websocket`` for every connection it
  hands to an application's ``serve_websocket`` (``raw``) or to a
  ``WsxConnection`` (``wsx``).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.metrics_core import Metric

__all__: list[str] = []

#: The ``scheme`` label of an accepted check, by the backend ``AuthCore`` found.
BACKEND_SCHEMES = {"basic": "basic", "bearer": "bearer", "api_key": "api_key", "jwt": "jwt"}

#: The ``scheme`` label of a refused check, by the lowercased header scheme.
HEADER_SCHEMES = {"basic": "basic", "bearer": "bearer"}

OTHER_SCHEME = "other"


def accepted_scheme(backend: str) -> str:
    """The ``scheme`` label of a check ``AuthCore`` accepted with ``backend``.

    ``jwt:<name>`` (a named verifier) is ``jwt``; a backend outside the set is
    ``other``.
    """
    return BACKEND_SCHEMES.get(backend.partition(":")[0], OTHER_SCHEME)


def refused_scheme(credential: str) -> str:
    """The ``scheme`` label of a refused ``Authorization`` value.

    The scheme before the first space, lowercased: ``basic`` and ``bearer``
    keep their name, anything else — a value with no space included — is
    ``other``.
    """
    scheme, separator, _ = credential.partition(" ")
    if not separator:
        return OTHER_SCHEME
    return HEADER_SCHEMES.get(scheme.lower(), OTHER_SCHEME)


class EventMetrics:
    """The credential, WSX and websocket families of one server."""

    def __init__(self) -> None:
        self._credential_checks = Counter(
            "kajenn_credential_checks",
            "Credential verifications, per scheme and outcome; a cache hit is not one.",
            ["scheme", "outcome"], registry=None)
        self._wsx_messages = Counter(
            "kajenn_wsx_messages",
            "WSX messages handed to an application, per application, route and status.",
            ["application", "route", "status"], registry=None)
        self._wsx_duration = Histogram(
            "kajenn_wsx_message_duration_seconds",
            "Time from the demux of a WSX message until its answer is read, in seconds.",
            ["application", "route"], registry=None)
        self._websockets_opened = Counter(
            "kajenn_websocket_connections_opened",
            "Websocket connections handed to an application, per application and kind.",
            ["application", "kind"], registry=None)
        self._websockets_live = Gauge(
            "kajenn_websocket_connections_live",
            "Websocket connections an application holds right now, per application and kind.",
            ["application", "kind"], registry=None)

    def record_credential_check(self, scheme: str, outcome: str) -> None:
        """Count one verification of ``scheme`` that ended ``accepted`` or ``refused``."""
        self._credential_checks.labels(scheme, outcome).inc()

    def record_wsx_message(self, application: str, route: str, status: int,
                           seconds: float) -> None:
        """Count one WSX message ``application`` answered on ``route`` with ``status``."""
        self._wsx_messages.labels(application, route, str(status)).inc()
        self._wsx_duration.labels(application, route).observe(seconds)

    def websocket_opened(self, application: str, kind: str) -> None:
        """Count one connection handed to ``application``, and one more live."""
        self._websockets_opened.labels(application, kind).inc()
        self._websockets_live.labels(application, kind).inc()

    def websocket_closed(self, application: str, kind: str) -> None:
        """One live connection of ``application`` less."""
        self._websockets_live.labels(application, kind).dec()

    def collect(self) -> list[Metric]:
        """The families of the five metrics, with their current values."""
        return [family
                for metric in (self._credential_checks, self._wsx_messages,
                               self._wsx_duration, self._websockets_opened,
                               self._websockets_live)
                for family in metric.collect()]
