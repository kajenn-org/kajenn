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


def families_text(families: Iterable[Metric]) -> str:
    """The exposition text of ``families``, written on a registry of its own."""
    registry = CollectorRegistry()
    registry.register(FamiliesCollector(families))
    return generate_latest(registry).decode()
