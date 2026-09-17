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

"""The storage a store test runs on: one ``site:`` mount over a temporary directory.

``site_mounts`` is exactly the mount list ``StorageMixin`` builds when ``storage=``
is omitted, only rooted at a ``tmp_path`` instead of the deployment directory, so a
store test exercises the real production shape. ``site_storage`` builds the manager
itself, for a store exercised without a server: a server never receives one, it
builds its own from the mounts its configuration declares.
"""

from __future__ import annotations

from typing import Any

from genro_storage import StorageManager


def site_mounts(base_dir: object) -> list[dict[str, Any]]:
    """The single ``site:`` mount rooted at ``base_dir``, in genro-storage's own shape."""
    return [{"name": "site", "protocol": "local", "base_path": str(base_dir)}]


def site_storage(base_dir: object, storage_key: str | None = None) -> StorageManager:
    """A built ``StorageManager`` over ``site_mounts`` — for a store tested WITHOUT a server.

    A server never receives one: it builds its own from the mounts its
    configuration declares.
    """
    storage = StorageManager()
    storage.configure(site_mounts(base_dir), storage_key=storage_key)
    return storage
