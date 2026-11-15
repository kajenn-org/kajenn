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
is Prometheus exposition text, parsed in full before it is merged. An empty
answer contributes nothing. An application is left out of that scrape, with a
log line, when its hook raises, its answer is not a ``str`` or its text does
not parse. Every sample gets the label ``app=<code>``, which overwrites an
``app`` label the application set itself. Families with the same name are
merged across applications when their type is equal, keeping the help of the
first; a family whose type differs from the one already collected is logged
and that application's family is left out. The text is written on a
``CollectorRegistry`` created for the answer, never the module-level
``REGISTRY``.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from genro_routes import RoutingClass, route
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.metrics_core import Metric
from prometheus_client.parser import text_string_to_metric_families

from .._exposition import families_text

if TYPE_CHECKING:
    from ..server_app import ServerApplication

__all__ = ["MetricsSection"]

METRICS_RULE = "SERVER_METRICS"


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

    def _log_failure(self, code: str, exception: Exception) -> None:
        """Log why ``code`` is left out of the scrape; the ``on_error`` of the broadcast."""
        self.logger.exception(
            "prometheus_metrics of %r left out", code, exc_info=exception)

    def _answer_families(self, code: str, text: object) -> list[Metric]:
        """The families of one answer, labelled ``app=<code>``.

        Raises:
            TypeError: the answer is not a ``str``.
            ValueError: the text does not parse.
        """
        if not isinstance(text, str):
            raise TypeError(f"prometheus_metrics answered {type(text).__name__}, not str")
        families = list(text_string_to_metric_families(text))
        for family in families:
            family.samples = [
                sample._replace(labels={**sample.labels, "app": code})
                for sample in family.samples
            ]
        return families

    @route(media_type=CONTENT_TYPE_LATEST, auth_rule=METRICS_RULE)
    async def index(self) -> str:
        """Every application's families as one Prometheus text.

        Note:
            Route: GET /_server/metrics
        """
        answers = await self.application.server.applications_broadcast(
            "prometheus_metrics", on_error=self._log_failure)
        merged: dict[str, Metric] = {}
        for code, text in answers:
            if text == "":
                continue
            try:
                families = self._answer_families(code, text)
            except (TypeError, ValueError) as exception:
                self._log_failure(code, exception)
                continue
            for family in families:
                collected = merged.setdefault(family.name, family)
                if collected is family:
                    continue
                if collected.type != family.type:
                    self.logger.warning(
                        "Family %r of %r is %s, the one collected is %s: left out",
                        family.name, code, family.type, collected.type)
                    continue
                collected.samples.extend(family.samples)
        return families_text(merged.values())
