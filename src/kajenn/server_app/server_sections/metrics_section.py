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

"""The ``_server/metrics`` section: the Prometheus exposition of the server.

``MetricsSection`` is a ``RoutingClass`` the ``ServerApplication`` attaches
under ``metrics``. Its one route is the section's ``index``, so it answers at
the section's own address:

    ``/_server/metrics``   every application's families, in one text

The route is gated ``auth_rule="SERVER_METRICS"``: Prometheus reads it with an
API key of an identity that holds only that tag.

At every scrape the route calls ``prometheus_metrics`` on every application
through ``server.applications_broadcast``, ``_server`` included. Each answer
is Prometheus exposition text; an empty answer is skipped, and an application
whose hook raises is logged and left out of that scrape. Every sample gets the
label ``app=<code>``. Families with the same name are merged across
applications: their samples are concatenated when type and help agree; a
family whose type or help differs from the one already collected is logged and
that application's family is left out. The text is written with
``generate_latest`` on a ``CollectorRegistry`` created for the answer, never
the module-level ``REGISTRY``.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING

from genro_routes import RoutingClass, route
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.metrics_core import Metric
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.registry import Collector

if TYPE_CHECKING:
    from ..server_app import ServerApplication

__all__ = ["METRICS_RULE", "MetricsSection"]

METRICS_RULE = "SERVER_METRICS"


class _FamiliesCollector(Collector):
    """A collector that yields a fixed list of metric families."""

    def __init__(self, families: Iterable[Metric]) -> None:
        self.families = list(families)

    def collect(self) -> Iterator[Metric]:
        """Yield the families given at construction."""
        return iter(self.families)


def _families_text(families: Iterable[Metric]) -> str:
    """The exposition text of ``families``, written on a registry of its own."""
    registry = CollectorRegistry()
    registry.register(_FamiliesCollector(families))
    return generate_latest(registry).decode()


class MetricsSection(RoutingClass):
    """The ``_server/metrics`` mount: the Prometheus text of every application.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``. The server is ``self.application.server``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application
        self.logger = logging.getLogger(__name__)

    def log_failure(self, code: str, exception: Exception) -> None:
        """The ``on_error`` of the broadcast: log and leave ``code`` out."""
        self.logger.exception(
            "prometheus_metrics of %r failed", code, exc_info=exception)

    def merged_families(self, answers: list[tuple[str, str]]) -> list[Metric]:
        """Parse every answer, label its samples ``app=<code>``, merge by name.

        A family whose type or help differs from the one already collected
        under its name is logged and left out.
        """
        merged: dict[str, Metric] = {}
        for code, text in answers:
            if not text:
                continue
            for family in text_string_to_metric_families(text):
                family.samples = [
                    sample._replace(labels={**sample.labels, "app": code})
                    for sample in family.samples
                ]
                collected = merged.get(family.name)
                if collected is None:
                    merged[family.name] = family
                elif (collected.type, collected.documentation) != (
                        family.type, family.documentation):
                    self.logger.warning(
                        "Family %r of %r (%s, %r) differs from the one collected"
                        " (%s, %r): left out", family.name, code, family.type,
                        family.documentation, collected.type, collected.documentation)
                else:
                    collected.samples.extend(family.samples)
        return list(merged.values())

    @route(media_type=CONTENT_TYPE_LATEST, auth_rule=METRICS_RULE)
    async def index(self) -> str:
        """Every application's families as one Prometheus text.

        Note:
            Route: GET /_server/metrics
        """
        answers = await self.application.server.applications_broadcast(
            "prometheus_metrics", on_error=self.log_failure)
        return _families_text(self.merged_families(answers))
