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

"""The HTTP request families of one server, counted in ``BaseServer.__call__``.

Private to ``kajenn``: the server reaches it through ``server.http_metrics``.

``HttpMetrics`` is held by the server (``server.http_metrics``) and owns five
``prometheus_client`` metric objects created with ``registry=None``: they
belong to the server instance, never to the module-level ``REGISTRY``.
``BaseServer._serve_http`` wraps the ASGI pair of every request it demuxes in
a ``CountedExchange`` and hands the outcome to ``HttpMetrics.record``;
``ServerApplication.prometheus_metrics`` writes ``collect()`` into its text.

The labels: ``application`` is the code of the application the demux chose
(``""`` when it chose none), ``route`` the route name the application wrote
into its scope under ``ROUTE_SCOPE_KEY`` (``""`` when none resolved), never a
path with parameters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import Counter, Histogram
from prometheus_client.metrics_core import Metric

if TYPE_CHECKING:
    from .types import Message, Receive, Send

ROUTE_SCOPE_KEY = "kajenn.route"
"""The scope key where an application writes the name of the route it resolved."""

__all__: list[str] = []


class CountedExchange:
    """The ASGI ``receive``/``send`` of one request, with what passed through them.

    ``status`` is the status of ``http.response.start`` as text (``""`` until
    it is sent); ``received`` sums the bodies of the ``http.request``
    messages, ``sent`` the bodies of the ``http.response.body`` messages.
    """

    def __init__(self, receive: Receive, send: Send) -> None:
        self._receive = receive
        self._send = send
        self.status = ""
        self.received = 0
        self.sent = 0

    async def receive(self) -> Message:
        """The next message of the client, its body size added to ``received``."""
        message = await self._receive()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
        return message

    async def send(self, message: Message) -> None:
        """Send ``message``, reading its status or adding its body size to ``sent``."""
        if message["type"] == "http.response.start":
            self.status = str(message["status"])
        elif message["type"] == "http.response.body":
            self.sent += len(message.get("body", b""))
        await self._send(message)


class HttpMetrics:
    """The HTTP request counters and the duration histogram of one server."""

    def __init__(self) -> None:
        self._requests = Counter(
            "kajenn_http_requests",
            "HTTP requests served, per application, route, method and status.",
            ["application", "route", "method", "status"], registry=None)
        self._duration = Histogram(
            "kajenn_http_request_duration_seconds",
            "Time from the demux until the application returns, in seconds.",
            ["application", "route", "method"], registry=None)
        self._received = Counter(
            "kajenn_http_request_body_bytes",
            "Bytes of HTTP request bodies received.",
            ["application", "route"], registry=None)
        self._sent = Counter(
            "kajenn_http_response_body_bytes",
            "Bytes of HTTP response bodies sent.",
            ["application", "route"], registry=None)
        self._exceptions = Counter(
            "kajenn_http_exceptions",
            "Exceptions an application raised that are not an HTTPException.",
            ["application", "exception"], registry=None)

    def record(self, application: str, route: str, method: str,
               exchange: CountedExchange, seconds: float) -> None:
        """Count one request served by ``application`` on ``route``."""
        self._requests.labels(application, route, method, exchange.status).inc()
        self._duration.labels(application, route, method).observe(seconds)
        self._received.labels(application, route).inc(exchange.received)
        self._sent.labels(application, route).inc(exchange.sent)

    def record_exception(self, application: str, exception: Exception) -> None:
        """Count one exception of ``application`` by the name of its class."""
        self._exceptions.labels(application, type(exception).__name__).inc()

    def collect(self) -> list[Metric]:
        """The families of the five metrics, with their current values."""
        return [family
                for metric in (self._requests, self._duration, self._received,
                               self._sent, self._exceptions)
                for family in metric.collect()]
