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

"""OpenApiApplication: a RoutedApplication that exposes REST + OpenAPI + docs.

``OpenApiApplication`` wraps an API surface — either the app's own
``@route`` methods (direct mode) or an external ``RoutingClass`` attached
under ``api_name`` (mounted mode) — and adds a ``_meta`` sub-tree with three
introspection endpoints:

- ``_meta/schema_json`` — the OpenAPI 3.1 document of the API, every operation
  declaring the code this application answers a rejected value with
  (``declare_validation_error``);
- ``_meta/docs`` — a Swagger-UI page pointing at ``_meta/schema_json``;
- ``_meta/index`` — an HTML splash linking to the docs.

The schema is built STANDALONE from the app's own router
(``router_openapi(app.route)``): it depends on no other application of the
site. The mounted routing class is linked as an eager ``instance`` branch, so
it inherits the app router's plugins — pydantic among them — and its handler
signatures are captured into the neutral ``params``/``result`` blocks the
``OpenAPITranslator`` reads; in direct mode the same plugins reach the app's
own router through the server's plugin arming (``PluginMixin`` config).

The docs and splash HTML live in dedicated resource files next to this module
and are read at USE time (never at import): a swap of the template file takes
effect without re-importing the package.

Kwargs peeled by the cooperative ``__init__``: ``routing_class`` (a
``RoutingClass`` instance to mount), ``module`` (``"pkg.mod:ClassName"``
import path, an alternative to ``routing_class``), ``docs`` (documentation
style — ``"swagger"`` or ``"off"``) and ``api_name`` (the segment the mounted
class is attached under, default ``"api"``). The rest flows down the chain
(``db_name`` to ``RoutedApplication``, ``code``/``mount`` to
``BaseApplication``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from genro_routes import RoutingClass, route

from ..exceptions import HTTPNotFound
from ..plugins.openapi import router_openapi
from ..routed_application import RoutedApplication

__all__ = ["OpenApiApplication"]

RESOURCES_DIR = Path(__file__).parent / "resources"


class OpenApiApplication(RoutedApplication):
    """Expose an API surface as REST + OpenAPI 3.1 with a Swagger docs page.

    Two ways to supply the API:

    - direct mode — subclass and write ``@route`` methods on the app itself;
      endpoints sit at the app root (``/{app}/endpoint``);
    - mounted mode — pass ``routing_class=`` (or ``module=``); the class is
      attached under ``api_name`` (``/{app}/{api_name}/endpoint``).

    Meta endpoints are attached under ``_meta`` in both modes.
    """

    openapi_info: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        self._docs_style: str = kwargs.pop("docs", "swagger")
        self._api_name: str = kwargs.pop("api_name", "api")
        routing_class: RoutingClass | None = kwargs.pop("routing_class", None)
        module: str | None = kwargs.pop("module", None)
        self._mounted_info: dict[str, Any] = {}
        super().__init__(**kwargs)
        self.route.add_branches({"name": "_meta", "instance": OpenApiMeta(self)})
        if routing_class is None and module:
            routing_class = self._import_routing_class(module)
        if routing_class is not None:
            self._mount_routing_class(routing_class)

    @property
    def docs_style(self) -> str:
        """Documentation style — ``"swagger"`` or ``"off"``."""
        return self._docs_style

    @property
    def api_name(self) -> str:
        """Path segment the mounted routing class is attached under."""
        return self._api_name

    @property
    def api_info(self) -> dict[str, Any]:
        """OpenAPI info dict: the mounted class's, else the app's, else empty."""
        return self._mounted_info or self.openapi_info or {}

    def declare_validation_error(self, paths: dict[str, Any]) -> None:
        """Write the code this application answers a rejected value with.

        Every operation of the document gains one response entry on
        ``validation_error_status`` — 400 under the strict reading, 422 for an
        application declaring the FastAPI convention — so a client reads the
        code it will actually receive.
        """
        status = str(self.validation_error_status)
        for path_item in paths.values():
            for operation in path_item.values():
                operation["responses"][status] = {"description": "Invalid argument values"}

    def schema_filters(self) -> dict[str, Any]:
        """Node filters forwarded to ``router_openapi`` when building the schema.

        Empty by default (the whole router). A subclass whose router carries the
        ``channel`` plugin overrides this to select the REST-facing channel so
        the schema stays visible (the ``McpOpenApiApplication`` bridge).
        """
        return {}

    def _mount_routing_class(self, routing_class: RoutingClass) -> None:
        """Mount the routing class under ``api_name`` as an eager branch.

        The instance is linked immediately (the ``instance`` branch form), so it
        inherits the app router's plugins — pydantic among them (fixed on every
        router), which captures the handler signatures into the neutral
        ``params``/``result`` blocks the OpenAPI translator reads.
        """
        self.route.add_branches({"name": self.api_name, "instance": routing_class})
        info = getattr(routing_class, "openapi_info", None)
        if info:
            self._mounted_info = info


class OpenApiMeta(RoutingClass):
    """Meta endpoints of an ``OpenApiApplication``, attached under ``_meta``."""

    def __init__(self, application: OpenApiApplication) -> None:
        self.application = application

    @route()
    def schema_json(self) -> dict[str, Any]:
        """Return the OpenAPI 3.1 document for the app's routes (standalone)."""
        app = self.application
        info = app.api_info
        paths_data = router_openapi(app.route, **app.schema_filters())
        app.declare_validation_error(paths_data.get("paths") or {})
        return {
            "openapi": "3.1.0",
            "servers": [{"url": f"/{app.mount}" if app.mount else "/"}],
            "info": {
                "title": info.get("title", type(app).__name__),
                "version": info.get("version", "1.0.0"),
                "description": info.get("description", ""),
            },
            **paths_data,
        }

    @route(media_type="text/html")
    def docs(self) -> str:
        """Serve the Swagger-UI page pointing at the app's own schema endpoint."""
        app = self.application
        if app.docs_style == "off":
            raise HTTPNotFound("documentation disabled")
        mount = app.mount
        schema_url = f"/{mount}/_meta/schema_json" if mount else "/_meta/schema_json"
        title = app.api_info.get("title", "API")
        template = (RESOURCES_DIR / "swagger.html").read_text()
        return template.format(title=title, schema_url=schema_url)

    @route(media_type="text/html")
    def index(self) -> str:
        """Serve the splash page with a link to the docs."""
        app = self.application
        info = app.api_info
        title = info.get("title", type(app).__name__)
        version = info.get("version", "")
        description = info.get("description", "")
        mount = app.mount
        docs_url = f"/{mount}/_meta/docs" if mount else "/_meta/docs"
        version_html = f"<p>Version: {version}</p>" if version else ""
        desc_html = f"<p>{description}</p>" if description else ""
        template = (RESOURCES_DIR / "openapi_index.html").read_text()
        return template.format(
            title=title,
            version_html=version_html,
            desc_html=desc_html,
            docs_url=docs_url,
        )
