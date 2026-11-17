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

"""Management sections of the ``_auth`` application.

Each section is a ``RoutingClass`` the ``AuthApplicationBase`` attaches under
its name, so its endpoints live at ``/_auth/<name>/...``. Every route asks
``AUTH_ADMIN``: ``UsersSection`` at ``users``, ``GroupsSection`` at
``groups``, ``TagsSection`` at ``tags``, ``TokensSection`` at ``tokens``.
"""

from __future__ import annotations

from .groups_section import GroupsSection
from .tags_section import TagsSection
from .tokens_section import TokensSection
from .users_section import UsersSection

__all__ = ["GroupsSection", "TagsSection", "TokensSection", "UsersSection"]
