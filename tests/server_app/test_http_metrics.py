# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The HTTP request families, counted in the server for every demuxed request.

Every test drives a real ``AsgiServer`` and reads the families the way
Prometheus reads them: GET ``/_server/metrics`` with an API key whose identity
holds only ``SERVER_METRICS``, parsed with ``prometheus_client``'s parser.

What the suite pins:

- N requests to an in-process route grow the requests counter by N with
  ``application``, ``route`` (the route name, not the path with its
  parameters), ``method`` and ``status``; the duration histogram count by N;
  the bytes received and sent by the body sizes;
- a route that raises counts with status 500 and in the exceptions counter
  with the class name;
- a path no application serves counts with ``application=""`` and
  ``route=""``;
- a switched-off application's 503 refusal counts with ``application=""``:
  ``demux`` returns the refusal answer, not the application;
- a ``spawner="subprocess"`` application counts in the server with the route
  name resolved in its process, also on its buffered ``WSK`` answer;
- a request refused by the route's own rule keeps the route name.
"""

from __future__ import annotations

from typing import Any

import pytest
from genro_routes import route
from prometheus_client import CollectorRegistry, Counter, generate_latest

from kajenn import AsgiServer, RoutedApplication
from kajenn.application import BaseApplication
from kajenn.exceptions import Redirect
from kajenn.streaming import StreamingResponse
from tests.conftest import call, exchange, json_body, read_samples, running, start_server, value
from tests.storage_support import auth_application


class Orders(RoutedApplication):
    """Routes answering, echoing a body, taking a path parameter, raising."""

    @route()
    def echo(self, name: str) -> dict[str, str]:
        return {"name": name}

    @route()
    def item(self, ident: str) -> dict[str, str]:
        return {"ident": ident}

    @route()
    def boom(self) -> None:
        raise LookupError("boom")

    @route()
    def moved(self) -> None:
        raise Redirect("/orders/item/1", 303)

    @route()
    def broken_stream(self) -> StreamingResponse:
        async def chunks():
            yield b"first"
            raise LookupError("broken after the start")
        return StreamingResponse(chunks(), media_type="text/plain")

    def prometheus_metrics(self) -> str:
        registry = CollectorRegistry()
        Counter("orders_placed", "Orders placed.", registry=registry).inc()
        return generate_latest(registry).decode()


class Silent(BaseApplication):
    """Returns without starting an answer."""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        return None


async def metrics_server(*applications: Any) -> AsgiServer:
    return await start_server(AsgiServer(
        applications=[auth_application(), *applications]))


async def test_requests_to_a_route_are_counted_with_sizes_and_duration() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    body, headers = json_body({"name": "abc"})
    answers = [await call(server, "POST", "/orders/echo", body, headers) for _ in range(3)]
    assert {answer["status"] for answer in answers} == {200}
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders", route="echo",
                 method="POST", status="200") == 3
    assert value(samples, "kajenn_http_request_duration_seconds_count",
                 application="orders", route="echo", method="POST") == 3
    assert value(samples, "kajenn_http_request_body_bytes_total",
                 application="orders", route="echo") == 3 * len(body)
    assert value(samples, "kajenn_http_response_body_bytes_total",
                 application="orders", route="echo") == sum(
                     len(answer["body"]) for answer in answers)


async def test_the_route_label_is_the_route_name_not_the_path() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    for ident in ("1", "2"):
        assert (await call(server, "GET", f"/orders/item/{ident}"))["status"] == 200
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders", route="item",
                 method="GET", status="200") == 2


async def test_a_route_that_raises_counts_500_and_the_exception() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    assert (await call(server, "GET", "/orders/boom"))["status"] == 500
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders", route="boom",
                 method="GET", status="500") == 1
    assert value(samples, "kajenn_http_exceptions_total",
                 application="orders", exception="LookupError") == 1


async def test_a_path_no_application_serves_counts_without_application_and_route() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    assert (await call(server, "GET", "/nowhere/at/all"))["status"] == 404
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="", route="",
                 method="GET", status="404") == 1


async def test_a_switched_off_application_counts_its_refusal() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    server.disable_application("orders")
    assert (await call(server, "GET", "/orders/item/1"))["status"] == 503
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="", route="",
                 method="GET", status="503") == 1


RECIPE = '''
from genro_routes import route

from kajenn import AuthApplicationBase, RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryAuthStore


class Billing(RoutedApplication):
    @route()
    def invoice(self, number: str):
        return {"number": number}

    @route()
    def boom(self):
        raise LookupError("boom in the process")


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="_auth", app_class=AuthApplicationBase).store(
            store_class=MemoryAuthStore)
        apps.application(code="billing", app_class=Billing,
                         spawner="subprocess", request_timeout=5)
'''


async def test_a_spawned_application_counts_with_the_route_of_its_process(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        for number in ("7", "8"):
            assert (await call(server, "GET", f"/billing/invoice/{number}"))["status"] == 200
        assert (await call(server, "GET", "/billing/boom"))["status"] == 500
        samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="billing",
                 route="invoice", method="GET", status="200") == 2
    assert value(samples, "kajenn_http_requests_total", application="billing",
                 route="boom", method="GET", status="500") == 1
    assert value(samples, "kajenn_http_exceptions_total",
                 application="billing", exception="ExternalFailure") == 1


async def test_a_wsk_request_to_a_spawned_application_counts_its_route(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        body, headers = json_body({})
        answer = await call(server, "WSK", "/billing/invoice/9", body, headers)
        assert answer["status"] == 200
        samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="billing",
                 route="invoice", method="WSK", status="200") == 1


async def test_a_refused_request_keeps_its_route_name() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    assert (await call(server, "GET", "/_server/metrics"))["status"] == 401
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="_server",
                 route="metrics", method="GET", status="401") == 1


async def test_no_created_series_in_the_text() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    assert (await call(server, "GET", "/orders/item/1"))["status"] == 200
    samples = await read_samples(server)
    assert not [sample.name for sample in samples if sample.name.endswith("_created")]


async def test_a_url_of_0_8_0_counts_with_the_route_of_its_new_address() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    assert (await call(server, "GET", "/orders/_meta/schema_json"))["status"] == 200
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders",
                 route="_meta/api/schema_json", method="GET", status="200") == 1


async def test_an_exception_after_the_start_keeps_the_status_sent() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    with pytest.raises(LookupError):
        await exchange(server, "GET", "/orders/broken_stream")
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders",
                 route="broken_stream", method="GET", status="200") == 1
    assert value(samples, "kajenn_http_exceptions_total",
                 application="orders", exception="LookupError") == 1


async def test_a_redirect_counts_with_its_own_status() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    assert (await call(server, "GET", "/orders/moved"))["status"] == 303
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders",
                 route="moved", method="GET", status="303") == 1
    assert not [sample for sample in samples if sample.name == "kajenn_http_exceptions_total"]


async def test_a_request_that_ends_without_an_answer_counts_with_an_empty_status() -> None:
    server = await metrics_server((Silent, {"code": "silent"}))
    sent = await exchange(server, "GET", "/silent/anything")
    assert not [message for message in sent if message["type"] == "http.response.start"]
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="silent", route="",
                 method="GET", status="") == 1


async def test_no_created_series_from_the_text_of_another_application() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    samples = await read_samples(server)
    placed = [sample for sample in samples if sample.name.startswith("orders_placed")]
    assert [(sample.name, sample.labels, sample.value) for sample in placed] == [
        ("orders_placed_total", {"app": "orders"}, 1.0)]


async def test_an_mcp_call_counts_with_the_mcp_route() -> None:
    server = await metrics_server((Orders, {"code": "orders"}))
    body, headers = json_body({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert (await call(server, "POST", "/orders/_meta/mcp", body, headers))["status"] == 200
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="orders",
                 route="_meta/mcp", method="POST", status="200") == 1
