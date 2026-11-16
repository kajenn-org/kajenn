# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The MCP families, counted on the application that answers ``_meta/mcp``.

Every test drives a real ``AsgiServer`` and reads ``/_server/metrics`` with an
API key whose identity holds only ``SERVER_METRICS``. The families come out of
``RoutedApplication.prometheus_metrics``, so every sample carries the label
``app=<code>`` of the application that counted it.

What the suite pins:

- a ``tools/call`` answered with a result, one answered with a tool error
  (bad arguments), one on an unknown tool (a protocol error, ``tool=""``) and
  a ``tools/list`` grow the calls counter with method, tool and outcome; the
  duration histogram counts the ``tools/call`` whose tool resolved;
- a notification (no ``id``) is not counted;
- a ``spawner="subprocess"`` application's families reach the text through
  the forwarded hook, with its ``app`` label;
- an application that overrides the hook with ``super()`` keeps its MCP
  families beside its own.
"""

from __future__ import annotations

import json
from typing import Any

from genro_routes import route
from prometheus_client import CollectorRegistry, Counter, generate_latest

from kajenn import AsgiServer, RoutedApplication
from tests.conftest import call, read_samples, running, start_server, value
from tests.storage_support import MemoryApiKeyStore

CALLS = "kajenn_mcp_calls_total"
DURATION_COUNT = "kajenn_mcp_tool_call_duration_seconds_count"


class Shop(RoutedApplication):
    """One MCP tool with a typed argument, and its own family beside the MCP ones."""

    @route(channel_channels="mcp,api")
    def price(self, item: int) -> dict[str, int]:
        return {"item": item, "price": 10}

    def prometheus_metrics(self) -> str:
        registry = CollectorRegistry()
        Counter("shop_visits", "Visits.", registry=registry).inc()
        return super().prometheus_metrics() + generate_latest(registry).decode()


async def mcp(server: AsgiServer, mount: str, message: dict[str, Any]) -> dict[str, Any]:
    """POST one JSON-RPC message to the MCP face of ``mount``."""
    answer = await call(server, "POST", f"/{mount}/_meta/mcp", json.dumps(message).encode(),
                        [(b"content-type", b"application/json")])
    return answer


def tools_call(ident: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


async def drive_calls(server: AsgiServer, mount: str) -> None:
    """Two results, one tool error, one unknown tool, one tools/list, one notification."""
    for ident in (1, 2):
        answer = json.loads((await mcp(server, mount, tools_call(ident, "price", {"item": 3})))["body"])
        assert answer["result"]["structuredContent"] == {"item": 3, "price": 10}
    answer = json.loads((await mcp(server, mount, tools_call(3, "price", {"item": "x"})))["body"])
    assert answer["result"]["isError"] is True
    answer = json.loads((await mcp(server, mount, tools_call(4, "nothing", {})))["body"])
    assert "error" in answer
    answer = json.loads((await mcp(server, mount, {"jsonrpc": "2.0", "id": 5,
                                                   "method": "tools/list"}))["body"])
    assert "result" in answer
    notified = await mcp(server, mount, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notified["status"] == 202


def assert_counted(samples: list[Any], code: str) -> None:
    assert value(samples, CALLS, app=code, method="tools/call", tool="price",
                 outcome="result") == 2
    assert value(samples, CALLS, app=code, method="tools/call", tool="price",
                 outcome="tool_error") == 1
    assert value(samples, CALLS, app=code, method="tools/call", tool="",
                 outcome="protocol_error") == 1
    assert value(samples, CALLS, app=code, method="tools/list", tool="", outcome="result") == 1
    assert value(samples, DURATION_COUNT, app=code, tool="price") == 3
    assert not [sample for sample in samples
                if sample.name == CALLS and sample.labels["method"] == "other"]


async def test_calls_of_an_in_process_application_are_counted() -> None:
    server = await start_server(AsgiServer(
        applications=[(Shop, {"code": "shop"})], tokens={"store_class": MemoryApiKeyStore}))
    await drive_calls(server, "shop")
    samples = await read_samples(server)
    assert_counted(samples, "shop")


async def test_an_override_with_super_keeps_the_mcp_families() -> None:
    server = await start_server(AsgiServer(
        applications=[(Shop, {"code": "shop"})], tokens={"store_class": MemoryApiKeyStore}))
    assert (json.loads((await mcp(server, "shop", tools_call(1, "price", {"item": 1})))["body"])
            ["result"]["structuredContent"]["price"]) == 10
    samples = await read_samples(server)
    assert value(samples, "shop_visits_total", app="shop") == 1
    assert value(samples, CALLS, app="shop", method="tools/call", tool="price",
                 outcome="result") == 1


RECIPE = '''
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryApiKeyStore


class Shop(RoutedApplication):
    @route(channel_channels="mcp,api")
    def price(self, item: int):
        return {"item": item, "price": 10}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        cfg.applications().application(code="shop", app_class=Shop,
                                       spawner="subprocess", request_timeout=5)
'''


async def test_calls_of_a_spawned_application_reach_the_text(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        await drive_calls(server, "shop")
        samples = await read_samples(server)
    assert_counted(samples, "shop")
