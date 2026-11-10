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

"""The ``_meta`` branch every ``RoutedApplication`` carries.

``_meta`` is the one segment the kernel reserves inside an application. It
holds the administrative routes, one branch per channel, never application
routes:

- ``_meta/index`` — the application page, with a link to each channel's
  description;
- ``_meta/api/schema_json`` — the OpenAPI 3.1 document of the routes the
  request may execute on channel ``api``;
- ``_meta/api/docs`` — a Swagger-UI page over that document (404 when the
  application was built with ``docs="off"``);
- ``_meta/mcp/tools`` — the MCP tools the caller sees, readable with a plain
  GET (the branch lives in ``applications.mcp``).

The HTML pages are read from resource files next to this module at USE time,
never at import: a swap of the template file takes effect without re-importing
the package.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from ..exceptions import HTTPNotFound
from ..plugins.openapi import OpenAPITranslator
from .mcp import McpMeta

if TYPE_CHECKING:
    from ..routed_application import RoutedApplication

__all__ = ["ApiMeta", "ApplicationMeta"]

RESOURCES_DIR = Path(__file__).parent / "resources"


class ApplicationMeta(RoutingClass):
    """The ``_meta`` branch: the application page, and one branch per channel."""

    def __init__(self, application: RoutedApplication) -> None:
        self.application = application
        self.route.add_branches([
            {"name": "api", "instance": ApiMeta(application)},
            {"name": "mcp", "instance": McpMeta(application)},
        ])

    @route(media_type="text/html")
    def index(self) -> str:
        """Serve the application page with a link to the OpenAPI docs."""
        app = self.application
        info = app.api_info
        title = info.get("title", type(app).__name__)
        version = info.get("version", "")
        description = info.get("description", "")
        version_html = f"<p>Version: {version}</p>" if version else ""
        desc_html = f"<p>{description}</p>" if description else ""
        template = (RESOURCES_DIR / "openapi_index.html").read_text()
        return template.format(
            title=title,
            version_html=version_html,
            desc_html=desc_html,
            docs_url=app.meta_url("api/docs"),
        )


class ApiMeta(RoutingClass):
    """The ``_meta/api`` branch: the OpenAPI document and its Swagger page."""

    def __init__(self, application: RoutedApplication) -> None:
        self.application = application

    @route()
    async def schema_json(self, _request=None) -> dict[str, Any]:
        """Return the OpenAPI 3.1 document of the routes this request may execute.

        The tree is the application's ``discover`` on the request's scope: the
        channel of the request and the caller's tags filter it as they filter
        execution. ``_request`` is injected by ``bind_kwargs`` and left
        unannotated, out of the schema.
        """
        app = self.application
        info = app.api_info
        paths_data = OpenAPITranslator.translate_openapi(await app.discover(_request.scope))
        self.declare_validation_error(paths_data.get("paths") or {})
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
        """Serve the Swagger-UI page pointing at the application's own document."""
        app = self.application
        if app.docs_style == "off":
            raise HTTPNotFound("documentation disabled")
        title = app.api_info.get("title", "API")
        template = (RESOURCES_DIR / "swagger.html").read_text()
        return template.format(title=title, schema_url=app.meta_url("api/schema_json"))

    def declare_validation_error(self, paths: dict[str, Any]) -> None:
        """Write the code the application answers a rejected value with.

        Every operation of the document gains one response entry on
        ``validation_error_status`` — 400 under the strict reading, 422 for an
        application declaring the FastAPI convention — so a client reads the
        code it will actually receive.
        """
        status = str(self.application.validation_error_status)
        for path_item in paths.values():
            for operation in path_item.values():
                operation["responses"][status] = {"description": "Invalid argument values"}
