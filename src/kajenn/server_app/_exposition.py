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

"""Write metric families as Prometheus exposition text.

Private to ``kajenn.server_app``: ``ServerApplication.prometheus_metrics`` and
the ``metrics`` section write their text with ``families_text``, on a
``CollectorRegistry`` created for each call, never the module-level
``REGISTRY``.

The ``_created`` series ``prometheus_client`` adds to counters, histograms
and summaries are left out of the text: inside a family as samples named
``<family>_created``, and as the gauge family ``<family>_created`` the text
parser makes of them when a text is read back.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.metrics_core import Metric
from prometheus_client.registry import Collector

__all__: list[str] = []


class FamiliesCollector(Collector):
    """A collector that yields a fixed list of metric families."""

    def __init__(self, families: Iterable[Metric]) -> None:
        self.families = list(families)

    def collect(self) -> Iterator[Metric]:
        """Yield the families given at construction."""
        return iter(self.families)


CREATED_SUFFIX = "_created"
"""The suffix of the creation-time series of a counter, histogram or summary."""

CREATED_TYPES = frozenset({"counter", "histogram", "summary"})
"""The family types ``prometheus_client`` writes a ``_created`` series for."""


def without_created(families: Iterable[Metric]) -> list[Metric]:
    """``families`` without their ``_created`` series.

    A sample named ``<family>_created`` is dropped from its family; a gauge
    family named ``<name>_created`` is dropped when a counter, histogram or
    summary family ``<name>`` is in ``families``.
    """
    families = list(families)
    owners = {family.name + CREATED_SUFFIX for family in families
              if family.type in CREATED_TYPES}
    kept: list[Metric] = []
    for family in families:
        if family.type == "gauge" and family.name in owners:
            continue
        copy = Metric(family.name, family.documentation, family.type, family.unit)
        copy.samples = [sample for sample in family.samples
                        if sample.name != family.name + CREATED_SUFFIX]
        kept.append(copy)
    return kept


def families_text(families: Iterable[Metric]) -> str:
    """The exposition text of ``families`` without ``_created`` series, on a registry of its own."""
    registry = CollectorRegistry()
    registry.register(FamiliesCollector(without_created(families)))
    return generate_latest(registry).decode()
