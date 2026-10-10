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

"""The ``_server/monitor`` section: the live view of the running server.

``MonitorSection`` is a ``RoutingClass`` the ``ServerApplication`` attaches
under ``monitor``, so the whole monitor lives at one address:

    ``/_server/monitor/snapshot``   the polled data
    ``/_server/monitor/panels``     the panel descriptors, fetched once
    ``/_server/monitor/panel``      one contributor's own panel module

Every route is gated ``auth_rule="SERVER_ADMIN"``: the monitor exposes the
whole server, so it is closed to anyone the operator has not admitted.

The page that composes ONE panel per mounted application is a front-end's,
not this section's: what is served here is the data it reads. What an app
IS at this instant comes from its ``app_snapshot`` (polled, aggregated here under
``apps``); WHO draws it comes from its ``app_panel`` (a class constant, so it
is fetched once at load). Both are inherited from ``BaseApplication``, so an
app that declares nothing still shows up — rendered by the generic panel, its
raw snapshot as key/value rows and tables.

An app whose panel the page does not know SHIPS it: the optional
``panel_source`` hands over the ES module as text, and ``panels`` fills the
descriptor's ``src`` with this section's ``panel`` route. So a panel travels
with the app that needs it — an application installed from another
distribution publishes no route and writes nothing into the core.

Two kinds of contributor are aggregated:

- the mounted applications, keyed by their mount (the ``_server`` app itself
  is left out: this section IS its monitor face);
- the system sections that declare the same two names, keyed ``_server/<name>``
  — a section is not an application, so it opts in by declaring them rather
  than by inheritance. This section never lists itself.

The server's own facts (listener address, pid, what is mounted) sit alongside
the apps under ``server``.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from genro_routes import RoutingClass, route

from kajenn.exceptions import HTTPNotFound

if TYPE_CHECKING:
    from ..server_app import ServerApplication

__all__ = ["MonitorSection"]

MONITOR_RULE = "SERVER_ADMIN"


class MonitorSection(RoutingClass):
    """The ``_server/monitor`` mount: the snapshot, the panels, the panel modules.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``. The server is ``self.application.server``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application

    @property
    def monitored_apps(self) -> dict[str, Any]:
        """The mounted applications the monitor shows, keyed by mount.

        The ``_server`` application is left out: this section is its monitor
        face, and a tab of its own would show the observer observing itself.
        """
        applications = self.application.server.applications
        return {
            app.mount: app for app in applications.values() if app is not self.application
        }

    @property
    def monitored_sections(self) -> dict[str, Any]:
        """Sibling sections that declare the panel contract, keyed ``_server/<name>``.

        A section is a ``RoutingClass``, not an application: it inherits
        nothing, so it takes part by declaring ``app_snapshot`` and
        ``app_panel`` itself. This section is never in the result.
        """
        return {
            f"_server/{name}": section
            for name, section in self.application.sections.items()
            if section is not self
            and hasattr(section, "app_snapshot")
            and hasattr(section, "app_panel")
        }

    @property
    def server_facts(self) -> dict[str, Any]:
        """What the server is, as the header of the page reads it.

        ``host``/``port`` are the CONFIGURED listener (the address ``serve``
        defaults to), not the bound socket: a server booted with ``port=0``
        shows the configuration, and the reader is looking at it through the
        real one anyway.
        """
        server = self.application.server
        return {
            "host": server.config_host,
            "port": server.config_port,
            "pid": os.getpid(),
            "applications": sorted(self.monitored_apps),
            "sections": sorted(self.application.sections),
        }

    @property
    def monitor_contributors(self) -> dict[str, Any]:
        """Everything the monitor shows, keyed as the shell keys its tabs.

        The mounted applications by mount, then the monitorable sections by
        ``_server/<name>``. One map, so the snapshot, the descriptors and the
        panel sources can never disagree on who is in the picture.
        """
        return {**self.monitored_apps, **self.monitored_sections}

    def panel_url(self, key: str) -> str:
        """The address serving ``key``'s own panel module.

        Absolute, not page-relative: the shell imports it with a dynamic
        ``import()``, which resolves against the document — and the document
        answers at two addresses, with and without the trailing slash.
        """
        return f"/{self.application.mount}/monitor/panel?app={quote(key, safe='')}"

    @route(media_type="application/json", auth_rule=MONITOR_RULE)
    def snapshot(self) -> dict[str, Any]:
        """The whole server at this instant: its own facts plus every app's.

        What the shell polls. Each mounted application and each monitorable
        section contributes its ``app_snapshot``.

        Note:
            Route: GET /_server/monitor/snapshot
        """
        apps = {key: item.app_snapshot for key, item in self.monitor_contributors.items()}
        return {"server": self.server_facts, "apps": apps}

    @route(media_type="application/json", auth_rule=MONITOR_RULE)
    def panels(self) -> dict[str, Any]:
        """One panel descriptor per contributor: who draws what.

        The static complement of ``snapshot`` — descriptors are class
        constants, so the shell fetches this once at load and polls the other.

        A contributor that also ships a ``panel_source`` gets its ``src``
        filled in here, pointing at ``panel``: declaring the module is enough,
        the app never publishes a route of its own for it. An explicit ``src``
        in the descriptor wins — an app is free to serve the module itself.

        Note:
            Route: GET /_server/monitor/panels
        """
        descriptors: dict[str, Any] = {}
        for key, item in self.monitor_contributors.items():
            descriptor = dict(item.app_panel)
            if hasattr(item, "panel_source") and "src" not in descriptor:
                descriptor["src"] = self.panel_url(key)
            descriptors[key] = descriptor
        return descriptors

    @route(media_type="text/javascript", auth_rule=MONITOR_RULE)
    def panel(self, app: str = "") -> str:
        """The panel module one contributor ships, as an ES module.

        The shell imports this when a descriptor names a panel it does not
        know. ``app`` is the contributor's key — the mount, ``""`` for the site
        root, or ``_server/<name>`` for a section. An unknown key, or one that
        ships no module, is a 404: the app renders generically.

        Note:
            Route: GET /_server/monitor/panel?app=<key>
        """
        item = self.monitor_contributors.get(app)
        source = getattr(item, "panel_source", None)
        if source is None:
            raise HTTPNotFound(f"no panel module for '{app}'")
        return source
