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
- a switched-off application counts its 503 refusal with its code;
- a ``spawner="subprocess"`` application counts in the server with the route
  name resolved in its process, also on its buffered ``WSK`` answer;
- a request refused by the route's own rule keeps the route name.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from genro_routes import route
from prometheus_client.parser import text_string_to_metric_families

from kajenn import AsgiServer, RoutedApplication
from tests.conftest import start_server
from tests.storage_support import MemoryApiKeyStore


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


async def metrics_server(*applications: Any) -> AsgiServer:
    return await start_server(AsgiServer(
        applications=list(applications), tokens={"store_class": MemoryApiKeyStore}))


async def call(server: AsgiServer, method: str, path: str, body: bytes = b"",
               headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    """One request through the server's ASGI entry point: status and body."""
    scope = {
        "type": "http", "method": method, "path": path, "query_string": b"",
        "headers": [(b"host", b"site.example"), *(headers or [])], "scheme": "http",
        "client": ("10.0.0.1", 5555),
    }
    sent: list[dict[str, Any]] = []
    requested = False
    staying = asyncio.Event()

    async def receive() -> dict[str, Any]:
        """The body once, then nothing: the client stays until the answer ends."""
        nonlocal requested
        if requested:
            await staying.wait()
        requested = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await server(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    answer = b"".join(message.get("body", b"") for message in sent
                      if message["type"] == "http.response.body")
    return {"status": start["status"], "body": answer}


async def read_samples(server: AsgiServer) -> list[Any]:
    """Every sample of ``/_server/metrics``, read with an API key holding only SERVER_METRICS."""
    key = server.api_key_store.issue("prometheus", ["SERVER_METRICS"])
    answer = await call(server, "GET", "/_server/metrics",
                        headers=[(b"authorization", f"Bearer {key}".encode())])
    assert answer["status"] == 200
    return [sample for family in text_string_to_metric_families(answer["body"].decode())
            for sample in family.samples]


def value(samples: list[Any], name: str, **labels: str) -> float:
    """The value of the sample ``name`` with exactly ``labels`` (``app="_server"`` added)."""
    wanted = {"app": "_server", **labels}
    [found] = [sample.value for sample in samples
               if sample.name == name and sample.labels == wanted]
    return found


def json_body(data: dict[str, Any]) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    return json.dumps(data).encode(), [(b"content-type", b"application/json")]


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
    assert value(samples, "kajenn_http_requests_total", application="orders", route="",
                 method="GET", status="503") == 1


RECIPE = '''
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryApiKeyStore


class Billing(RoutedApplication):
    @route()
    def invoice(self, number: str):
        return {"number": number}

    @route()
    def boom(self):
        raise LookupError("boom in the process")


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        cfg.applications().application(code="billing", app_class=Billing,
                                       spawner="subprocess", request_timeout=5)
'''


@asynccontextmanager
async def running(config: str):
    server = AsgiServer(config=config)
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        async with asyncio.timeout(30):
            while not all(server.is_enabled(code) for code in server.applications):
                await asyncio.sleep(0.05)
        yield server
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await lifespan


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
