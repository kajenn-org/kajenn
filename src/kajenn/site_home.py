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

"""SiteHome — the folder a site owns, and the named paths inside it.

A site is a folder. Inside it every path the site owns is RELATIVE and NAMED,
so the same site is the same thing in development, in classic production, in a
virtualenv, in Docker and in Kubernetes: only the folder moves::

    <home>/
        config.py            the configuration recipe (the card names the file)
        static/              the site's static files
        data/
            frozen_users/    the deposit of the frozen users
            sessions/        the session snapshots
        sockets/             the worker sockets of an orchestrated site
        logs/                the orchestration log and its decisions journal
        run/                 the pidfile

The layout follows genropy's ``site_path`` where the core has a counterpart.
``pages/``, ``resources/`` and ``root.py`` are genropy's own: a hosted framework
adds its folders to the same home.

The home is NOT the installation root. ``KAJENN_HOME`` (``DefaultConfig``)
names where kajenn keeps the site CARDS and the machine defaults layer;
this class is one SITE's folder, named by the card and written into the
configuration as ``site(home=...)``.

The folder is also the anchor of the ``home:`` storage volume, beside ``site:``
— the site's own folder as the configuration declares it, its code and its
resources. With no home declared the two coincide.

The folder is laid out by ``kajenn configure <name>`` and by nobody else:
``serve`` creates nothing and refuses a home that is not there.

Nothing here reads the environment and nothing here decides: the paths are
properties of the folder, and whoever needs one asks for it by name.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SiteHome"]


class SiteHome:
    """One site's folder: every path inside it relative and named."""

    recipe_name = "config.py"
    """The configuration recipe ``configure`` writes; a card may name another file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def __str__(self) -> str:
        return str(self.path)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SiteHome) and other.path == self.path

    def __hash__(self) -> int:
        return hash(self.path)

    @property
    def recipe(self) -> Path:
        """The conventional configuration recipe of this home."""
        return self.path / self.recipe_name

    @property
    def static(self) -> Path:
        """The site's static files (favicon, robots, sitemap)."""
        return self.path / "static"

    @property
    def data(self) -> Path:
        """What the site keeps across restarts."""
        return self.path / "data"

    @property
    def frozen_users(self) -> Path:
        """The deposit of the frozen users of an orchestrated site."""
        return self.data / "frozen_users"

    @property
    def sessions(self) -> Path:
        """Where a named serve writes its session snapshot."""
        return self.data / "sessions"

    @property
    def sockets(self) -> Path:
        """The worker sockets of an orchestrated site."""
        return self.path / "sockets"

    @property
    def logs(self) -> Path:
        """The orchestration log and the decisions journal beside it."""
        return self.path / "logs"

    @property
    def run(self) -> Path:
        """The pidfile of a running site."""
        return self.path / "run"

    @property
    def folders(self) -> tuple[Path, ...]:
        """Every folder of the layout, parents first — what ``prepare`` creates."""
        return (self.static, self.data, self.frozen_users, self.sessions,
                self.sockets, self.logs, self.run)

    def prepare(self) -> None:
        """Create the home and every folder of the layout that is missing.

        An empty mounted volume becomes a usable home with one call, which is
        what a container's init step needs; a home already laid out is untouched.
        """
        for folder in self.folders:
            folder.mkdir(parents=True, exist_ok=True)
