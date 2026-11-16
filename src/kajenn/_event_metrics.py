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

"""The server families of events: credentials, WSX, websockets, sessions, processes, boot.

Private to ``kajenn``: the server reaches it through ``server.event_metrics``.

``EventMetrics`` is held by the server (``server.event_metrics``) and owns
``prometheus_client`` metric objects created with ``registry=None``: they
belong to the server instance, never to the module-level ``REGISTRY``.
``ServerApplication.prometheus_metrics`` writes ``collect()`` into its text.

Who counts what:

- credential checks: ``AuthApplicationBase.authenticate`` (the verification a cache
  miss of ``server.authenticate_credential`` reaches, for in-process and
  spawned applications alike), ``AuthApplicationBase.login`` (``password``) and
  the OIDC ``callback`` (``oidc``). A cache hit is not a check.
  The caller (``_auth``) supplies the scheme label.
- WSX messages: ``WsxConnection`` for every message it hands to the demux; the
  ping it answers inline is not a message of an application.
- websocket connections: ``BaseServer.on_websocket`` for every connection it
  hands to an application's ``serve_websocket`` (``raw``) or to a
  ``WsxConnection`` (``wsx``).
- sessions created: ``SessionMiddleware``, where it creates a session for a
  request that carries none the store knows.
- restarts of spawned processes: ``KBusMixin`` where it asks a spawner for a
  relaunch, by cause — ``exited`` (the process ended without being stopped),
  ``lost`` (its kbus member disconnected), ``unresponsive`` (it did not
  confirm ``forget_credentials`` in time). A process that ended before it
  joined is also a start failure.
- kbus timeouts: ``RemoteApplication.remote_call`` (``remote_call``),
  ``RemoteApplication.__call__`` (``http_forward``, answered 503) and
  ``KBusMixin.forget_credentials_everywhere`` (``forget_credentials``).
- fatal boot errors: ``Lifespan.startup``, only when the server stays up
  (``abort_on_fatal_boot_error=False``).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.metrics_core import Metric

__all__: list[str] = []


class EventMetrics:
    """The event families of one server: credentials, WSX, websockets, sessions,
    spawned processes, kbus timeouts, fatal boot errors."""

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

        self._sessions_created = Counter(
            "kajenn_sessions_created", "Sessions created for a request.", registry=None)
        self._restarts = Counter(
            "kajenn_spawned_process_restarts",
            "Relaunches of the process of a spawned application, per application and cause.",
            ["application", "cause"], registry=None)
        self._start_failures = Counter(
            "kajenn_spawned_process_start_failures",
            "Processes of a spawned application that ended before they joined.",
            ["application"], registry=None)
        self._kbus_timeouts = Counter(
            "kajenn_kbus_timeouts",
            "kbus calls to a spawned application without an answer in time, "
            "per application and operation.",
            ["application", "operation"], registry=None)
        self._fatal_boot_errors = Counter(
            "kajenn_fatal_boot_errors",
            "FatalBootError of the startup with the server kept up for management.",
            registry=None)

    def record_credential_check(self, scheme: str, outcome: str) -> None:
        """Count one verification with the caller-supplied ``scheme`` and outcome."""
        self._credential_checks.labels(scheme, outcome).inc()

    def record_wsx_message(self, application: str, route: str, status: int,
                           seconds: float) -> None:
        """Count one WSX message ``application`` answered on ``route`` with ``status``."""
        self._wsx_messages.labels(application, route, str(status)).inc()
        self._wsx_duration.labels(application, route).observe(seconds)

    def record_websocket_opened(self, application: str, kind: str) -> None:
        """Count one connection handed to ``application``, and one more live."""
        self._websockets_opened.labels(application, kind).inc()
        self._websockets_live.labels(application, kind).inc()

    def record_websocket_closed(self, application: str, kind: str) -> None:
        """One live connection of ``application`` less."""
        self._websockets_live.labels(application, kind).dec()

    def record_session_created(self) -> None:
        """Count one session created."""
        self._sessions_created.inc()

    def record_restart(self, application: str, cause: str) -> None:
        """Count one relaunch of ``application``'s process for ``cause``."""
        self._restarts.labels(application, cause).inc()

    def record_start_failure(self, application: str) -> None:
        """Count one process of ``application`` that ended before it joined."""
        self._start_failures.labels(application).inc()

    def record_kbus_timeout(self, application: str, operation: str) -> None:
        """Count one ``operation`` towards ``application`` that timed out."""
        self._kbus_timeouts.labels(application, operation).inc()

    def record_fatal_boot_error(self) -> None:
        """Count one ``FatalBootError`` the server stayed up after."""
        self._fatal_boot_errors.inc()

    def collect(self) -> list[Metric]:
        """The families of the event metrics, with their current values."""
        return [family
                for metric in (self._credential_checks, self._wsx_messages,
                               self._wsx_duration, self._websockets_opened,
                               self._websockets_live, self._sessions_created,
                               self._restarts, self._start_failures,
                               self._kbus_timeouts, self._fatal_boot_errors)
                for family in metric.collect()]
