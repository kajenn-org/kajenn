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

"""Storage capability: genro-storage as a mixin over the base server (§4, D6).

``StorageMixin`` is a capability composed over ``BaseServer`` (``class S(...,
StorageMixin, BaseServer)``). Unlike ``AuthMixin``/``SessionMixin`` it arms NO
middleware — storage is survival infrastructure, not a request-chain concern
(§4: PUBLIC = base + storage). Its cooperative ``__init__`` peels ``storage=``
and ``storage_key=`` and forwards everything else down the D16 chain.

``storage=`` shapes the ``genro_storage.StorageManager`` the server owns:

- ``None`` → a manager with the two default mounts, both on the deployment
  directory (the process cwd): ``site:`` and ``home:``;
- a ``StorageManager`` instance → adopted as-is;
- a ``list[dict]`` → genro-storage's own mount configuration, handed to
  ``configure()`` verbatim (``{"name": ..., "protocol": ..., ...}``); an EMPTY
  list reads like ``None`` — a recipe that declared a ``storage`` section for
  its key material alone asked for the default layout, not for no storage.

``storage_key=`` is the at-rest key material: comma-separated Fernet keys, each
optionally ``<domain>:`` prefixed (the first key of a domain encrypts, all of
them decrypt). It reaches ``configure(storage_key=...)``; key material that
resolves empty is genro-storage's own boot error, never a silent
no-encryption fallback. Omitted, encryption stays dormant and any
``encrypted=True`` write raises at the write site.

Encryption is declared per WRITE, not per mount: the stores that hold
credentials pass ``encrypted=True``, everything else writes plain, and both
share one directory tree — what lands on disk is self-describing (an envelope
whose first line is ``#GNRE1:``), so reads declare nothing.

genro-storage nodes are ``smartasync``: under a running event loop they would
hand back a coroutine instead of a value. This server's storage API is
SYNCHRONOUS by ratification (D22, core 1b) — the stores and the spool are plain
sync objects and async dispatch paths wrap them — so the mixin pins the sync
dispatch (``set_sync()``) for the context the server is built in, which every
task it later spawns inherits.

A composition WITHOUT the mixin has NO ``storage`` attribute at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genro_storage import StorageManager
from genro_toolbox.smartasync import set_sync

__all__ = ["DEFAULT_HOME_MOUNT", "DEFAULT_SITE_MOUNT", "StorageMixin"]

DEFAULT_SITE_MOUNT = {"name": "site", "protocol": "local"}
"""The site's own folder as the configuration declares it: its code, its resources."""

DEFAULT_HOME_MOUNT = {"name": "home", "protocol": "local"}
"""The space the site keeps its own things in: statics, deposits, sockets, logs.

Both are the default layout minus their anchor. ``site:`` is anchored on the cwd
read when the manager is built, which is boot; ``home:`` on the folder the card
names, and with no home declared on the folder of ``site:``.
``AsgiConfigBuilder.storage_mounts`` writes the same two mounts as recipe lines,
so this is the one place the two roads agree on.
"""


class StorageMixin:
    """Storage capability mixin, composed over the base server. Arms no middleware.

    Constructor kwargs peeled here: ``storage`` — ``None`` (a manager with the
    ``site:`` and ``home:`` mounts on the deployment directory), a ``StorageManager``
    instance (adopted), or a ``list[dict]`` of genro-storage mount configs (empty
    reads like ``None``); ``storage_key`` — the at-rest key material.
    """

    def __init__(self, **kwargs: Any) -> None:
        storage: list[dict[str, Any]] | None = kwargs.pop("storage", None)
        storage_key: str | None = kwargs.pop("storage_key", None)
        set_sync()
        super().__init__(**kwargs)
        self._storage = self._build_storage(storage, storage_key)

    def _build_storage(
        self,
        storage: list[dict[str, Any]] | None,
        storage_key: str | None,
    ) -> StorageManager:
        """Build the ``StorageManager`` the server owns, from the MOUNTS it was given.

        The mounts come from the ``storage`` section of the configuration — a
        built manager is not something a configuration can carry, so none is
        accepted here. An empty list means "the default layout".
        """
        mounts = storage or self._default_mounts()
        built = StorageManager()
        built.configure(mounts, storage_key=storage_key)
        return built

    def _default_mounts(self) -> list[dict[str, Any]]:
        """The default layout: ``site:`` and ``home:``, both on the deployment directory.

        A composition reaching here declared no home, so the two coincide — the
        rule ``AsgiConfigBuilder.storage_mounts`` applies on the recipe road.
        """
        anchor = str(Path.cwd())
        return [
            {**DEFAULT_SITE_MOUNT, "base_path": anchor},
            {**DEFAULT_HOME_MOUNT, "base_path": anchor},
        ]

    @property
    def storage(self) -> StorageManager:
        """The storage this server owns (mounts + at-rest encryption key material)."""
        return self._storage
