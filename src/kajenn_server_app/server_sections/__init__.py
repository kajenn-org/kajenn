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

"""System sections of the automatic ``_server`` application.

Each section is a ``RoutingClass`` the ``ServerApplication`` attaches through
``attach_section(section, name)``, so its endpoints live at
``/_server/<name>/...``. Shipped: ``AuthSection`` (the ``auth`` mount carrying
the login methods), ``UsersSection`` (SUPERADMIN-gated user management at
``users``), ``TokensSection`` (issued credentials — api keys and JWTs — at
``tokens``), ``TasksSection`` (the task backbone — schedules and spool — at
``tasks``) and ``MonitorSection`` (the live view of the running server at
``monitor``, gated SERVER_ADMIN).
"""

from __future__ import annotations

from .auth_section import AuthSection
from .monitor_section import MonitorSection
from .tasks_section import TasksSection
from .tokens_section import TokensSection
from .users_section import UsersSection

__all__ = [
    "AuthSection",
    "MonitorSection",
    "TasksSection",
    "TokensSection",
    "UsersSection",
]
