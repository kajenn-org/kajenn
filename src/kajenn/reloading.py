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

"""The reload launch, reachable by any launcher.

``serve_reloading`` boots a server under uvicorn's reload supervisor: the
caller names WHAT to rebuild (a ``config`` path or an ``application`` target —
the very keys ``factory()`` reads back) and WHAT IS WATCHED (``reload_dirs``,
plural, with ``reload_excludes`` when a watched tree is written into at
runtime). Nothing is derived here: whoever ships a recipe inside a package
knows that watching the recipe's own directory is useless — what its
developers edit is the site, and only the caller knows where that lives.

The supervisor accepts only an import string, so the description crosses the
process boundary as one JSON object in ``LAUNCHER_ENV`` and
``kajenn.__main__:factory`` rebuilds the very same server on every
restart — with ``shutdown_mode = QUITTING``, so every reload exit takes the
soft quit and the population survives the restart.

This package's own CLI is one caller among others: ``kajenn serve
--reload`` keeps deriving its default watch root from the source file it was
given, and hands the result here.
"""

from __future__ import annotations

import json
import os

import uvicorn

LAUNCHER_ENV = "KAJENN_LAUNCHER"
"""The variable carrying the launcher's state across the reload process boundary."""

FACTORY_TARGET = "kajenn.__main__:factory"
"""The import string the supervisor rebuilds from, in every restarted process."""

__all__ = ["FACTORY_TARGET", "LAUNCHER_ENV", "serve_reloading"]


def serve_reloading(
    *,
    host: str,
    port: int,
    reload_dirs: list[str],
    reload_excludes: list[str] | None = None,
    config: str | None = None,
    application: str | None = None,
    save_session: str | None = None,
    site_name: str | None = None,
    site_home: str | None = None,
    debug: bool | str = False,
) -> None:
    """Boot under the reload supervisor, watching the roots the caller names.

    Args:
        host: the bind host.
        port: the bind port.
        reload_dirs: the directories whose ``*.py`` changes restart the child.
        reload_excludes: patterns the watcher must ignore — a site that writes
            inside its own tree at runtime says so here.
        config: the config.py path ``factory()`` rebuilds from.
        application: the quickstart target, when there is no config; exactly
            one of the two must be given.
        save_session: the session snapshot file, when a named serve armed one.
        site_name: the name the site is filed under, when it has a card.
        site_home: the folder the site owns, when it has one.
        debug: the declared usage mode, carried to the rebuilt server as is.

    Raises:
        ValueError: neither or both of ``config`` and ``application``.

    Blocks until the supervisor ends. Every restarted child is rebuilt by
    ``factory()`` from what this wrote in the environment.
    """
    if (config is None) == (application is None):
        raise ValueError("exactly one of 'config' and 'application' must be given")
    payload: dict[str, object] = {"host": host, "port": port}
    if config is not None:
        payload["config"] = config
    else:
        payload["application"] = application
    if save_session is not None:
        payload["save_session"] = save_session
    for word, value in (("site_name", site_name), ("site_home", site_home)):
        if value is not None:
            payload[word] = value
    if debug is not False:
        payload["debug"] = debug
    os.environ[LAUNCHER_ENV] = json.dumps(payload)
    uvicorn.run(
        FACTORY_TARGET,
        factory=True,
        reload=True,
        reload_dirs=reload_dirs,
        reload_excludes=reload_excludes,
        host=host,
        port=port,
    )
