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

"""System sections of the ``_server`` application.

Each section is a ``RoutingClass`` the ``ServerApplication`` attaches through
``attach_section(section, name)``, so its endpoints live at
``/_server/<name>/...``. Shipped: ``TasksSection`` (the task backbone —
schedules and spool — at ``tasks``, gated SERVER_ADMIN), ``MonitorSection``
(the live view of the running server at ``monitor``, gated SERVER_ADMIN), ``DatabasesSection`` (the declared
databases and their migration at ``databases``, gated SERVER_ADMIN) and
``K8sSection`` (the default health probes at ``k8s``, public),
``ApplicationsSection`` (``ready`` and the state of every application at
``applications``, gated SERVER_ADMIN) and ``MetricsSection`` (the Prometheus
text of every application at ``metrics``, gated SERVER_METRICS).
"""

from __future__ import annotations

from .applications_section import ApplicationsSection
from .databases_section import DatabasesSection
from .k8s_section import K8sSection
from .metrics_section import MetricsSection
from .monitor_section import MonitorSection
from .tasks_section import TasksSection

__all__ = [
    "ApplicationsSection",
    "DatabasesSection",
    "K8sSection",
    "MetricsSection",
    "MonitorSection",
    "TasksSection",
]
