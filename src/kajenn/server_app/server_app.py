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

"""ServerApplication: the ``_server`` system application.

``ServerApplication`` carries the system surface a server exposes under
``/_server``. Every server mounts it under the code ``_server`` (the role
process of an external application excepted); the configuration customises it
with ``application(code="_server", ...)`` and never declares it. The demux finds it through the ordinary mount table — there is no
dedicated demux logic.

It extends ``RoutedApplication``, so ``/_server/_meta/`` carries the usual
``api`` and ``mcp`` descriptions and ``/_server/_meta/mcp`` the MCP face, and
adds:

- ``index`` — the ``/_server/`` descriptor: title and the attached section
  names (JSON — no HTML in code);
- ``sections`` / ``attach_section(section, name)`` — the registry of system
  sections: ``attach_section`` links a ``RoutingClass`` under ``name``
  (endpoints at ``/_server/<name>/...``) and records it so the introspection
  surfaces (``index`` and ``MonitorSection``) can enumerate them;
- ``prometheus_metrics`` — the server's own families (build, process,
  ``WorkPool``, requests in flight, spawned processes, HTTP requests), read by the
  ``metrics`` section at ``/_server/metrics`` with the rest.

Authentication is not here: login, users and tokens belong to the application
mounted at ``/_auth`` (``kajenn.auth_app``). Every section asks
``SERVER_ADMIN``, the metrics excepted (``SERVER_METRICS``) and the health
probes (public).

Handlers stay PURE: they return values and never touch cookies or an ambient
request/response. A handler that needs the live request DECLARES an
UNANNOTATED ``_request`` parameter: ``bind_kwargs`` injects the per-dispatch
``Request`` for that name, and leaving it unannotated keeps it out of the
pydantic model (and thus the public OpenAPI schema).

Identity: ``code`` and ``mount`` are both declared ``"_server"`` as class
attributes — the system mount is an invariant.
"""

from __future__ import annotations

import logging
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

import psutil
from genro_builders.builder import element
from genro_routes import RoutingClass, route
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily

import kajenn
from kajenn.application import ApplicationGrammar
from kajenn.exceptions import HTTPNotFound
from kajenn.routed_application import RoutedApplication
from kajenn.well_known import as_list

from ._exposition import families_text
from .server_sections import (
    ApplicationsSection,
    DatabasesSection,
    K8sSection,
    MetricsSection,
    MonitorSection,
    TasksSection,
)

__all__ = ["ServerApplication", "ServerApplicationGrammar"]

SECURITY_TXT_FIELDS = (
    ("contact", "Contact"),
    ("expires", "Expires"),
    ("encryption", "Encryption"),
    ("acknowledgments", "Acknowledgments"),
    ("preferred_languages", "Preferred-Languages"),
    ("canonical", "Canonical"),
    ("policy", "Policy"),
    ("hiring", "Hiring"),
)


class ServerApplicationGrammar(ApplicationGrammar):
    """The words this application adds to a recipe, under its own element.

    The words of two well-known documents the server answers itself,
    ``/.well-known/security.txt`` and ``/robots.txt``::

        server_app = applications.application(app_class=ServerApplication,
                                              code="_server")
        server_app.security_txt(contact="mailto:security@example.com",
                                expires="2027-06-30T00:00:00Z")
        robots = server_app.robots(sitemaps="https://example.com/sitemap.xml")
        robots.agent(user_agent="*", disallow="/admin/")

    A document whose word is not written is not declared.
    """

    @element(sub_tags="", node_label="security_txt")
    def security_txt(
        self,
        contact: str | list = None,
        expires: str = None,
        encryption: str | list = None,
        acknowledgments: str | list = None,
        preferred_languages: str | list = None,
        canonical: str | list = None,
        policy: str | list = None,
        hiring: str | list = None,
    ) -> None:
        """The RFC 9116 fields of ``/.well-known/security.txt``. ``contact`` and
        ``expires`` (ISO 8601) are required; every other field is optional."""

    @element(sub_tags="agent", node_label="robots")
    def robots(self, sitemaps: str | list = None) -> None:
        """``/robots.txt``: one group per ``agent`` child, then the ``sitemaps``
        (absolute http(s) URLs). At least one ``agent`` is required."""

    @element(parent_tags="robots", sub_tags="")
    def agent(
        self, user_agent: str = "*", allow: str | list = None, disallow: str | list = None
    ) -> None:
        """One robots.txt group: ``user_agent`` and its ``allow``/``disallow``
        rules, each starting with ``/`` or ``*``."""


class ServerApplication(RoutedApplication):
    """System endpoints of a server, mounted under ``/_server``.

    Carries the public server's system surface: the sections attached through
    ``attach_section``, listed by the ``index`` descriptor.

    It also answers three well-known documents as defaults, each given only
    when it is configured (``active_default_well_known``):
    ``/.well-known/security.txt`` and ``/robots.txt`` from the ``security_txt``
    and ``robots`` words, validated there so a bad value stops the server at
    construction, and ``/favicon.ico`` from ``static/favicon.ico`` of the site
    home. It answers the health probes ``/healthz``, ``/livez`` and
    ``/readyz`` as defaults too, always, from its ``k8s`` section (#105). An
    application declaring one of these paths in ``well_known`` answers it
    instead.
    """

    openapi_info: ClassVar[dict[str, Any]] = {
        "title": "kajenn server endpoints",
        "version": "1.0.0",
    }
    code = "_server"
    mount = "_server"
    grammar: type = ServerApplicationGrammar
    default_well_known = {
        "/.well-known/security.txt": "security_txt",
        "/robots.txt": "robots_txt",
        "/favicon.ico": "favicon_ico",
        "/healthz": "k8s/healthz",
        "/livez": "k8s/livez",
        "/readyz": "k8s/readyz",
    }

    def __init__(self, **kwargs: Any) -> None:
        self._security_txt_document: str | None = None
        self._robots_txt_document: str | None = None
        self._sections: dict[str, RoutingClass] = {}
        super().__init__(**kwargs)
        self.attach_section(TasksSection(self), name="tasks")
        self.attach_section(MonitorSection(self), name="monitor")
        self.attach_section(DatabasesSection(self), name="databases")
        self.attach_section(K8sSection(self), name="k8s")
        self.attach_section(ApplicationsSection(self), name="applications")
        self.attach_section(MetricsSection(self), name="metrics")

    def active_default_well_known(self) -> dict[str, str]:
        """The well-known documents this server has configured, given as defaults.

        Builds ``security.txt`` and ``robots.txt`` from their words — the
        validation raises ``ValueError`` here, at construction — and keeps
        each path only when its word is written; ``/favicon.ico`` only when the
        site home has ``static/favicon.ico``. An application declaring the same
        path in ``well_known`` answers it instead.
        """
        active = super().active_default_well_known()
        handler = getattr(self.server, "config", None)
        section = f"applications.{self.code}"
        if handler is not None and handler.node(f"{section}.security_txt") is not None:
            self._security_txt_document = self.build_security_txt(handler, section)
        else:
            active.pop("/.well-known/security.txt")
        if handler is not None and handler.node(f"{section}.robots") is not None:
            self._robots_txt_document = self.build_robots_txt(handler, section)
        else:
            active.pop("/robots.txt")
        if self.favicon_path() is None:
            active.pop("/favicon.ico")
        return active

    def build_security_txt(self, handler: Any, section: str) -> str:
        """The security.txt body from the ``security_txt`` word, validated."""
        names = [name for name, _ in SECURITY_TXT_FIELDS]
        fields = handler.closed_attrs(f"{section}.security_txt", *names)
        if not as_list(fields.get("contact")):
            raise ValueError("security_txt: contact is required")
        expires = fields.get("expires")
        if not expires:
            raise ValueError("security_txt: expires is required")
        try:
            expires_at = datetime.fromisoformat(expires)
        except (TypeError, ValueError):
            raise ValueError(f"security_txt: expires {expires!r} is not ISO 8601") from None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            logging.getLogger(__name__).warning("security_txt: expires %s is past", expires)
        lines = []
        for name, field in SECURITY_TXT_FIELDS:
            values = as_list(fields.get(name))
            if name == "preferred_languages" and values:
                values = [", ".join(values)]
            lines.extend(f"{field}: {value}" for value in values)
        return "\n".join(lines) + "\n"

    def build_robots_txt(self, handler: Any, section: str) -> str:
        """The robots.txt body from the ``robots`` word and its agents, validated."""
        path = f"{section}.robots"
        groups = []
        for child in handler.node(path).value or ():
            agent = handler.closed_attrs(f"{path}.{child.label}", "user_agent", "allow", "disallow")
            if not agent.get("user_agent"):
                raise ValueError("robots: an agent has an empty user_agent")
            lines = [f"User-agent: {agent['user_agent']}"]
            for field in ("allow", "disallow"):
                for rule in as_list(agent.get(field)):
                    if not rule.startswith(("/", "*")):
                        raise ValueError(f"robots: rule {rule!r} must start with '/' or '*'")
                    lines.append(f"{field.capitalize()}: {rule}")
            groups.append("\n".join(lines) + "\n")
        if not groups:
            raise ValueError("robots: at least one agent is required")
        body = "\n".join(groups)
        sitemaps = as_list(handler.closed_attrs(path, "sitemaps").get("sitemaps"))
        for sitemap in sitemaps:
            parsed = urlparse(sitemap)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"robots: sitemap {sitemap!r} is not an absolute http(s) URL")
        if sitemaps:
            body += "\n" + "".join(f"Sitemap: {sitemap}\n" for sitemap in sitemaps)
        return body

    def favicon_path(self) -> Path | None:
        """``static/favicon.ico`` of the site home, ``None`` when there is no such file."""
        home = getattr(self.server, "site_home", None)
        if home is None:
            return None
        path = home.static / "favicon.ico"
        return path if path.is_file() else None

    @property
    def sections(self) -> dict[str, RoutingClass]:
        """Attached system sections keyed by their mount segment (may be empty)."""
        return self._sections

    def attach_section(self, section: RoutingClass, name: str) -> None:
        """Attach ``section`` under ``name`` and record it in ``sections``.

        Links the section's router into this app (endpoints at
        ``/_server/<name>/...``) and keeps it enumerable for the
        introspection surfaces (the ``index`` descriptor, ``MonitorSection``).
        """
        self.route.add_branches({"name": name, "instance": section})
        self.sections[name] = section

    def prometheus_metrics(self) -> str:
        """The server's own families, read at call time, as exposition text.

        Build (kajenn and Python versions), the process (pid, start time, CPU
        seconds, resident memory, open file descriptors, from psutil), the
        ``WorkPool`` gauges (``pool.metrics``), the requests in flight
        (``requests.in_flight``), for every started spawner backend the
        processes still running, and the HTTP request families
        (``server.http_metrics``).
        """
        server = self.server
        process = psutil.Process()
        cpu = process.cpu_times()
        pool = server.pool.metrics
        spawned = GaugeMetricFamily(
            "kajenn_spawned_processes",
            "Processes of external applications still running, per spawner backend.",
            labels=["spawner"])
        for backend, spawner in server.kbus_spawners.items():
            running = sum(1 for item in spawner.processes.values() if item.returncode is None)
            spawned.add_metric([backend], running)
        return families_text([
            InfoMetricFamily(
                "kajenn_build", "kajenn and Python versions of the server process.",
                value={"version": kajenn.__version__,
                       "python_version": platform.python_version()}),
            GaugeMetricFamily(
                "kajenn_process_pid", "Process id of the server process.",
                value=os.getpid()),
            GaugeMetricFamily(
                "kajenn_process_start_time_seconds",
                "Start time of the server process since the Unix epoch, in seconds.",
                value=process.create_time()),
            CounterMetricFamily(
                "kajenn_process_cpu_seconds",
                "User and system CPU time of the server process, in seconds.",
                value=cpu.user + cpu.system),
            GaugeMetricFamily(
                "kajenn_process_resident_memory_bytes",
                "Resident memory size of the server process, in bytes.",
                value=process.memory_info().rss),
            GaugeMetricFamily(
                "kajenn_process_open_fds", "Open file descriptors of the server process.",
                value=process.num_fds()),
            GaugeMetricFamily(
                "kajenn_pool_threads", "Threads of the WorkPool, 0 before the first sync call.",
                value=pool["total"]),
            GaugeMetricFamily(
                "kajenn_pool_calls_in_flight",
                "Calls entered in the WorkPool and not yet exited, queued ones included.",
                value=pool["busy"]),
            GaugeMetricFamily(
                "kajenn_requests_in_flight", "Requests registered on the server right now.",
                value=server.requests.in_flight),
            spawned,
            *server.http_metrics.collect(),
        ])

    @route()
    def index(self) -> dict[str, Any]:
        """The ``/_server/`` descriptor: title and section names."""
        return {
            "title": self.api_info.get("title", type(self).__name__),
            "sections": sorted(self.sections),
        }

    @route(media_type="text/plain")
    def security_txt(self) -> str:
        """The security.txt document. Route: GET /.well-known/security.txt

        Raises ``HTTPNotFound`` when the ``security_txt`` word is not written.
        """
        if self._security_txt_document is None:
            raise HTTPNotFound("security.txt is not configured")
        return self._security_txt_document

    @route(media_type="text/plain")
    def robots_txt(self) -> str:
        """The robots.txt document. Route: GET /robots.txt

        Raises ``HTTPNotFound`` when the ``robots`` word is not written.
        """
        if self._robots_txt_document is None:
            raise HTTPNotFound("robots.txt is not configured")
        return self._robots_txt_document

    @route(media_type="image/x-icon")
    def favicon_ico(self) -> Path:
        """The site's favicon file. Route: GET /favicon.ico

        Raises ``HTTPNotFound`` when the site home has no ``static/favicon.ico``.
        """
        path = self.favicon_path()
        if path is None:
            raise HTTPNotFound("favicon.ico is not configured")
        return path
