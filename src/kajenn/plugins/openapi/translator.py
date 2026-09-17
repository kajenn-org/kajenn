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

"""OpenAPITranslator — turn genro-routes ``nodes()`` output into OpenAPI.

Reads the dialect-neutral description produced by ``router.nodes()`` and NEVER
re-derives anything from the callables. genro-routes' pydantic plugin computes
and caches, once at decoration time, both the input and the output description
of every handler; this translator only reads those neutral blocks:

- input — ``entry_info["params"]``: ``schema`` is the aggregate request JSON
  schema (``request_schema``) and ``fields`` is the per-parameter list
  (``{name, schema, required, default, kind}``). The HTTP method is guessed
  from the per-parameter schemas (all scalar → GET, else POST), and the query
  parameters / request body are built from the cached ``schema`` — no pydantic
  model is ever built here;
- output — ``entry_info["result"]``: ``{schema, media_type}`` (the return-type
  schema the pydantic plugin produced via ``TypeAdapter``);
- ``security`` / ``x-requires`` come from the per-entry ``auth`` / ``env``
  plugin config.

Consequently this module imports NO pydantic (and does not inspect callables):
pydantic is a concern of genro-routes' pydantic plugin, which owns the schema
derivation. The cached ``schema`` blocks keep ``$defs`` inline (self-contained
per entry); this translator lifts them into a document-level pool, as OpenAPI
expects — copying each block before popping ``$defs`` so the plugin's cache is
never mutated.

The translator methods are staticmethods by design (the granted builder-style
exception, coding rule 4): pure data-logic functions over the neutral node
description, holding no instance state.
"""

from __future__ import annotations

from typing import Any

__all__ = ["OpenAPITranslator"]


class OpenAPITranslator:
    """Translate router ``nodes()`` output to OpenAPI format.

    Modes:
        - ``openapi``: flat format, all paths merged into a single paths dict.
        - ``h_openapi``: hierarchical format preserving the router tree.
    """

    # JSON-schema primitive types serializable as query-string parameters.
    SCALAR_JSON_TYPES: set[str] = {"string", "integer", "number", "boolean", "null"}

    @staticmethod
    def translate_openapi(
        nodes_data: dict[str, Any],
        lazy: bool = False,
        path_prefix: str = "",
    ) -> dict[str, Any]:
        """Translate ``nodes()`` output to flat OpenAPI format."""
        paths: dict[str, Any] = {}
        all_defs: dict[str, Any] = {}

        entries = nodes_data.get("entries", {})
        for entry_name, entry_info in entries.items():
            path = f"{path_prefix}/{entry_name}" if path_prefix else f"/{entry_name}"
            path_item, defs = OpenAPITranslator.entry_info_to_openapi(entry_name, entry_info)
            paths[path] = path_item
            all_defs.update(defs)

        routers_data = nodes_data.get("routers", {})
        routers: dict[str, Any]
        if lazy:
            routers = dict(routers_data)
        else:
            for child_name, child_data in routers_data.items():
                child_prefix = f"{path_prefix}/{child_name}" if path_prefix else f"/{child_name}"
                child_openapi = OpenAPITranslator.translate_openapi(
                    child_data, lazy=False, path_prefix=child_prefix
                )
                paths.update(child_openapi.get("paths", {}))
                if "$defs" in child_openapi:
                    all_defs.update(child_openapi["$defs"])
            routers = {}

        result: dict[str, Any] = {"paths": paths}
        if all_defs:
            result["$defs"] = all_defs
        if routers:
            result["routers"] = routers
        return result

    @staticmethod
    def translate_h_openapi(
        nodes_data: dict[str, Any],
        lazy: bool = False,
        path_prefix: str = "",
    ) -> dict[str, Any]:
        """Translate ``nodes()`` output to hierarchical OpenAPI format."""
        paths: dict[str, Any] = {}
        all_defs: dict[str, Any] = {}

        entries = nodes_data.get("entries", {})
        for entry_name, entry_info in entries.items():
            path = f"{path_prefix}/{entry_name}" if path_prefix else f"/{entry_name}"
            path_item, defs = OpenAPITranslator.entry_info_to_openapi(entry_name, entry_info)
            paths[path] = path_item
            all_defs.update(defs)

        routers_data = nodes_data.get("routers", {})
        routers: dict[str, Any]
        if lazy:
            routers = dict(routers_data)
        else:
            routers = {}
            for child_name, child_data in routers_data.items():
                child_h_openapi = OpenAPITranslator.translate_h_openapi(child_data, lazy=False)
                if child_h_openapi:
                    routers[child_name] = child_h_openapi
                    if "$defs" in child_h_openapi:
                        all_defs.update(child_h_openapi.pop("$defs"))

        result: dict[str, Any] = {
            "description": nodes_data.get("description"),
            "owner_doc": nodes_data.get("owner_doc"),
        }
        if paths:
            result["paths"] = paths
        if all_defs:
            result["$defs"] = all_defs
        if routers:
            result["routers"] = routers
        return result

    @staticmethod
    def entry_info_to_openapi(
        name: str, entry_info: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Convert an entry info dict to an OpenAPI path item.

        HTTP method priority: explicit openapi plugin config, else guessed from
        the per-parameter schemas. Input (query params / request body) is built
        from the cached ``params`` block; the response schema is read from the
        neutral ``result`` block. Nothing is derived from the callable.
        """
        doc = entry_info.get("doc", "")
        summary = doc.split("\n")[0] if doc else name
        metadata = entry_info.get("metadata", {})
        collected_defs: dict[str, Any] = {}

        params_block = entry_info.get("params") or {}
        request_schema = params_block.get("schema")
        fields = params_block.get("fields") or []

        openapi_config = metadata.get("plugin_config", {}).get("openapi", {})
        explicit_method = openapi_config.get("method")
        if explicit_method:
            http_method = explicit_method.lower()
        else:
            http_method = OpenAPITranslator.guess_http_method(fields)

        if openapi_config.get("summary"):
            summary = openapi_config["summary"]

        operation: dict[str, Any] = {
            "operationId": name,
            "summary": summary,
        }
        if openapi_config.get("description"):
            operation["description"] = openapi_config["description"]
        elif doc:
            operation["description"] = doc

        if openapi_config.get("deprecated"):
            operation["deprecated"] = True

        tags = openapi_config.get("tags")
        if tags:
            operation["tags"] = tags if isinstance(tags, list) else [tags]

        # Request body / query params: read the cached input schema (copy before
        # lifting $defs so the plugin's cached schema is never mutated).
        if request_schema is not None:
            schema = dict(request_schema)
            defs = schema.pop("$defs", None)
            if defs:
                collected_defs.update(defs)
            if http_method == "get":
                parameters = OpenAPITranslator.schema_to_parameters(schema)
                if parameters:
                    operation["parameters"] = parameters
            else:
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                }

        # Response schema: read from the neutral result block.
        result_block = entry_info.get("result") or {}
        response_schema = result_block.get("schema")
        media_type = result_block.get("media_type") or "application/json"
        if response_schema is not None:
            response_schema = dict(response_schema)
            if "$defs" in response_schema:
                collected_defs.update(response_schema.pop("$defs"))
            operation["responses"] = {
                "200": {
                    "description": "Successful response",
                    "content": {media_type: {"schema": response_schema}},
                }
            }

        if "responses" not in operation:
            operation["responses"] = {"200": {"description": "Successful response"}}

        # Security: explicit override, else derived from the auth plugin config.
        plugins = entry_info.get("plugins", {})
        explicit_security = openapi_config.get("security")
        if explicit_security is not None:
            operation["security"] = explicit_security
        else:
            auth_plugin = plugins.get("auth")
            if auth_plugin is not None:
                auth_config = auth_plugin.get("config", {})
                auth_rule = auth_config.get("rule", "")
                security_scheme = openapi_config.get("security_scheme", "BearerAuth")
                if auth_rule:
                    operation["security"] = [{security_scheme: []}]
                else:
                    operation["security"] = []

        # x-requires from the env plugin config.
        env_plugin = plugins.get("env")
        if env_plugin is not None:
            env_config = env_plugin.get("config", {})
            env_requires = env_config.get("requires", "")
            if env_requires:
                operation["x-requires"] = env_requires

        return {http_method: operation}, collected_defs

    @staticmethod
    def guess_http_method(fields: list[dict[str, Any]]) -> str:
        """Guess the HTTP method from the neutral parameter fields.

        GET when every typed parameter carries a scalar JSON schema, else POST.
        Reads only the cached ``fields`` (never the callable); an untyped
        parameter — ``schema`` is ``None`` (unannotated, or ``*args``/``**kwargs``)
        — does not force POST, matching "no typed params → GET".
        """
        for field in fields:
            schema = field.get("schema")
            if schema is None:
                continue
            if not OpenAPITranslator._is_scalar_schema(schema):
                return "post"
        return "get"

    @staticmethod
    def _is_scalar_schema(schema: Any) -> bool:
        """Return True if a JSON-schema fragment is a scalar (GET-friendly) type.

        A ``$ref`` or ``allOf`` (nested model) is non-scalar; an ``anyOf`` /
        ``oneOf`` union is scalar when every branch is (e.g. ``int | None``); a
        bare ``enum`` is scalar.
        """
        if not isinstance(schema, dict):
            return False
        if "$ref" in schema or "allOf" in schema:
            return False
        for combiner in ("anyOf", "oneOf"):
            options = schema.get(combiner)
            if options is not None:
                return all(OpenAPITranslator._is_scalar_schema(option) for option in options)
        schema_type = schema.get("type")
        if schema_type in OpenAPITranslator.SCALAR_JSON_TYPES:
            return True
        if schema_type is None and "enum" in schema:
            return True
        return False

    @staticmethod
    def schema_to_parameters(schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert an aggregate request JSON schema to OpenAPI query parameters."""
        properties = schema.get("properties", {})
        required_fields = set(schema.get("required", []))
        parameters: list[dict[str, Any]] = []

        for prop_name, prop_schema in properties.items():
            parameters.append(
                {
                    "name": prop_name,
                    "in": "query",
                    "required": prop_name in required_fields,
                    "schema": prop_schema,
                }
            )
        return parameters
