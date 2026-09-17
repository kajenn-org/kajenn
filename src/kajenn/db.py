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

"""Database handler — the core's minimal contract for a mounted database.

A ``database`` declared in the config names a ``db_class`` (the imported class
that builds the real db from the connection parameters) and, optionally, a
``db_handler_class`` (default ``AsgiDbHandlerBase``). At mount time the server
builds ``db_handler_class(db_class(**params))`` and registers the handler.

The handler is what lives in the registry and what ``request.db`` returns. It
proxies every attribute to the wrapped db via ``__getattr__`` (so the db's own
interface — ``execute`` and the rest — stays transparent), while owning the one
method the core itself calls: ``closeConnection`` (registered as a request
cleanup). Concrete db classes and custom handlers live outside the core; the
core only defines this contract.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AsgiDbHandlerBase"]


class AsgiDbHandlerBase:
    """Wraps a database object: owns ``closeConnection``, proxies the rest.

    Subclass to customise lifecycle (e.g. a legacy backend); the default
    proxies every non-underscore attribute to the wrapped db.
    """

    __slots__ = ("_db",)

    def __init__(self, db: Any) -> None:
        self._db = db

    def closeConnection(self) -> None:
        """Close the wrapped db's connection if it exposes ``closeConnection``."""
        close = getattr(self._db, "closeConnection", None)
        if callable(close):
            close()

    def __getattr__(self, name: str) -> Any:
        # __getattr__ runs only for attributes not found normally; ``_db`` is a
        # real slot, so it never recurses here. Underscore names are never
        # proxied: this both guards against recursion (when ``_db`` is not yet
        # set) and keeps the db's internals out of the public proxy surface.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._db, name)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({type(self._db).__name__})"
