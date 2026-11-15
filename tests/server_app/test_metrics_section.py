# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The ``_server/metrics`` section: every application's Prometheus families in one text.

Every test drives a real ``AsgiServer``. The gate is exercised with real API
keys issued on the server's own api-key store and sent as ``Bearer``: an
identity holding only ``SERVER_METRICS`` reads the text, one holding only
``SERVER_ADMIN`` is refused, an anonymous request is refused.

What the suite pins:

- the text parses with ``prometheus_client``'s parser and carries every family
  of the server with ``app="_server"``;
- an in-process application and a ``spawner="subprocess"`` application that
  override ``prometheus_metrics`` appear with their own ``app`` label;
- an application whose hook raises is left out, the others still appear;
- two applications returning the same family give one family with both labels.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from kajenn import AsgiServer, RoutedApplication
from tests.conftest import start_server
from tests.core.test_auth import MemoryApiKeyStore

SERVER_FAMILIES = {
    "kajenn_build_info",
    "kajenn_process_pid",
    "kajenn_process_start_time_seconds",
    "kajenn_process_cpu_seconds",
    "kajenn_process_resident_memory_bytes",
    "kajenn_process_open_fds",
    "kajenn_pool_threads",
    "kajenn_pool_calls_in_flight",
    "kajenn_requests_in_flight",
}


def orders_text(code: str) -> str:
    """One gauge family ``shop_orders`` with a value of its own, as text."""
    registry = CollectorRegistry()
    Gauge("shop_orders", "Open orders.", registry=registry).set(len(code))
    return generate_latest(registry).decode()


class Shop(RoutedApplication):
    """Overrides the hook, async."""

    async def prometheus_metrics(self) -> str:
        return orders_text(self.code)


class Broken(RoutedApplication):
    """Its hook raises: left out of the scrape."""

    def prometheus_metrics(self) -> str:
        raise LookupError("broken metrics")


class Silent(RoutedApplication):
    """Inherits the empty default."""


async def metrics_server(*applications: Any) -> AsgiServer:
    return await start_server(AsgiServer(
        applications=list(applications), tokens={"store_class": MemoryApiKeyStore}))


async def scrape(server: AsgiServer, tags: list[str] | None = None) -> dict[str, Any]:
    """GET /_server/metrics, with an API key holding ``tags`` when given."""
    headers = [(b"host", b"site.example")]
    if tags is not None:
        key = server.api_key_store.issue("prometheus", tags)
        headers.append((b"authorization", f"Bearer {key}".encode()))
    scope = {
        "type": "http", "method": "GET", "path": "/_server/metrics",
        "query_string": b"", "headers": headers, "scheme": "http",
        "client": ("10.0.0.1", 5555),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await server(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent
                    if message["type"] == "http.response.body")
    return {"status": start["status"], "headers": dict(start["headers"]), "body": body}


def samples_by_family(text: str) -> dict[str, list[Any]]:
    """The samples of every family in ``text``, by family name."""
    return {family.name: family.samples for family in text_string_to_metric_families(text)}


async def read_families(server: AsgiServer) -> dict[str, list[Any]]:
    answer = await scrape(server, ["SERVER_METRICS"])
    assert answer["status"] == 200
    return samples_by_family(answer["body"].decode())


async def test_without_credentials_the_scrape_is_refused() -> None:
    server = await metrics_server()
    assert (await scrape(server))["status"] == 401


async def test_an_identity_with_only_server_metrics_reads_the_text() -> None:
    server = await metrics_server()
    answer = await scrape(server, ["SERVER_METRICS"])
    assert answer["status"] == 200
    assert answer["headers"][b"content-type"].decode() == CONTENT_TYPE_LATEST


async def test_an_identity_with_only_server_admin_is_refused() -> None:
    server = await metrics_server()
    assert (await scrape(server, ["SERVER_ADMIN"]))["status"] == 403


async def test_the_text_carries_every_server_family_labelled_server() -> None:
    server = await metrics_server()
    families = await read_families(server)
    assert SERVER_FAMILIES <= set(families)
    for name in SERVER_FAMILIES:
        assert families[name], name
        assert {sample.labels["app"] for sample in families[name]} == {"_server"}
    [build] = families["kajenn_build_info"]
    assert set(build.labels) == {"app", "version", "python_version"}


async def test_an_overriding_application_appears_with_its_label() -> None:
    server = await metrics_server((Shop, {"code": "shop"}), (Silent, {"code": "silent"}))
    families = await read_families(server)
    [orders] = families["shop_orders"]
    assert orders.labels == {"app": "shop"}
    assert orders.value == len("shop")
    for samples in families.values():
        assert all(sample.labels["app"] != "silent" for sample in samples)


async def test_a_hook_that_raises_leaves_only_that_application_out() -> None:
    server = await metrics_server((Broken, {"code": "broken"}), (Shop, {"code": "shop"}))
    families = await read_families(server)
    apps = {sample.labels["app"] for samples in families.values() for sample in samples}
    assert apps == {"_server", "shop"}


async def test_the_same_family_from_two_applications_is_one_family() -> None:
    server = await metrics_server((Shop, {"code": "shop"}), (Shop, {"code": "store"}))
    text = (await scrape(server, ["SERVER_METRICS"]))["body"].decode()
    names = [family.name for family in text_string_to_metric_families(text)]
    assert names.count("shop_orders") == 1
    orders = samples_by_family(text)["shop_orders"]
    assert {(sample.labels["app"], sample.value) for sample in orders} == {
        ("shop", len("shop")), ("store", len("store"))}


class Clashing(RoutedApplication):
    """Returns ``shop_orders`` with another type: left out of the merge."""

    def prometheus_metrics(self) -> str:
        return "# HELP shop_orders Open orders.\n# TYPE shop_orders counter\nshop_orders_total 1\n"


async def test_a_family_with_another_type_is_left_out() -> None:
    server = await metrics_server((Shop, {"code": "shop"}), (Clashing, {"code": "clash"}))
    families = await read_families(server)
    assert [sample.labels["app"] for sample in families["shop_orders"]] == ["shop"]


RECIPE = '''
from prometheus_client import CollectorRegistry, Gauge, generate_latest

from kajenn import ApiKeyStore, RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class MemoryApiKeyStore(ApiKeyStore):
    def __init__(self, storage=None):
        self._records = {}

    def load_all(self):
        return list(self._records.values())

    def get(self, key_id):
        return self._records.get(key_id)

    def save(self, record):
        self._records[record["key_id"]] = record

    def delete(self, key_id):
        return self._records.pop(key_id, None) is not None


class Billing(RoutedApplication):
    def prometheus_metrics(self):
        registry = CollectorRegistry()
        Gauge("billing_invoices", "Invoices.", registry=registry).set(3)
        return generate_latest(registry).decode()


class Local(RoutedApplication):
    def prometheus_metrics(self):
        registry = CollectorRegistry()
        Gauge("local_items", "Items.", registry=registry).set(2)
        return generate_latest(registry).decode()


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess",
                         request_timeout=5)
        apps.application(code="local", app_class=Local)
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


async def test_a_spawned_application_appears_through_the_forwarding(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        families = await read_families(server)
    assert families["billing_invoices"][0].labels == {"app": "billing"}
    assert families["local_items"][0].labels == {"app": "local"}
    assert [(sample.labels, sample.value) for sample in families["kajenn_spawned_processes"]] == [
        ({"app": "_server", "spawner": "subprocess"}, 1.0)]
