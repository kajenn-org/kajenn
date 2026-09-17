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

"""Avatar — the ONE authenticated identity type across the package.

An ``Avatar`` is a plain slotted value object: an identity string, its
authorization tags, and an extensible ``Bag`` of per-user data. ``AuthCore``
returns it and sessions carry it — "nobody" is ``None`` uniformly, never an
anonymous Avatar. The constructor normalizes ``tags=None`` to an empty list
(the identity boundary for e.g. a JWT null tags claim). No framework machinery.
"""

from __future__ import annotations

from genro_bag import Bag

__all__ = ["Avatar"]


class Avatar:
    """User identity with authorization tags and extensible Bag data."""

    __slots__ = ("_identity", "_tags", "_data")

    def __init__(self, identity: str, tags: list[str] | None = None) -> None:
        """Build the avatar; ``tags=None`` normalizes to an empty list."""
        self._identity = identity
        self._tags = list(tags) if tags else []
        self._data = Bag()

    @property
    def identity(self) -> str:
        """User identifier (username, email, ...)."""
        return self._identity

    @property
    def tags(self) -> list[str]:
        """Authorization tags (roles/permissions)."""
        return self._tags

    @property
    def data(self) -> Bag:
        """Extensible per-user data as a Bag."""
        return self._data
