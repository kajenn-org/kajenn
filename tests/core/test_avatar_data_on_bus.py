# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Avatar data survives bus calls and external placement through public requests."""

import json
from datetime import date
from decimal import Decimal

import pytest
from genro_tytx import from_tytx

from kajenn import Avatar

from .test_external_application import running
from .test_external_parity import PLACEMENTS, bearer, call

RECIPE = '''
import os

from datetime import date
from decimal import Decimal

from genro_bag import Bag
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Billing(RoutedApplication):
    @route()
    def prepared_state(self):
        return {"pid": os.getpid()}

    @route()
    def avatar_data(self, _request=None):
        data = _request.avatar().data
        assert isinstance(data, Bag)
        return data.as_dict(recursive=True)

    @route()
    def amount(self):
        return Decimal("3.30")

    @route()
    def typed(self, amount=None, day=None, _request=None):
        return {"amount": amount, "day": day,
                "types": [type(amount).__name__, type(day).__name__],
                "data": _request.avatar().data.as_dict(recursive=True)}

    @route()
    async def relay_avatar_data(self, _request=None):
        return await self.server.kbus_call(
            "/billing/avatar_data", {}, auth=_request.avatar())

    @route()
    async def relay_typed(self, _request=None):
        return await self.server.kbus_call(
            "/billing/typed", {"amount": Decimal("1.10"), "day": date(2026, 10, 13)},
            auth=_request.avatar())


class Idp(RoutedApplication):
    @route()
    def check(self, credential: str = "", channel: str = ""):
        assert channel == "rest"
        return {"identity": "idp-user", "tags": ["admin"], "data": IDP_DATA[credential]}


IDP_DATA = {
    "Bearer tok": {"allowed_repository": ["r1"]},
    "Bearer nested": {"permissions": {"allowed_repository": ["r1"], "enabled": True},
                      "limit": None},
    "Bearer typed": {"budget": Decimal("12.50"), "since": date(2026, 1, 2)},
}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, DECLARATION)
        apps.application(code="idp", app_class=Idp)
        cfg.channels().channel(name="rest", authentication_route="/idp/check")
'''


def write_recipe(tmp_path, placement):
    path = tmp_path / f"{placement}.py"
    path.write_text(RECIPE.replace("DECLARATION", PLACEMENTS[placement]))
    return str(path)


@pytest.mark.parametrize("placement", PLACEMENTS)
@pytest.mark.parametrize("token, data", [
    ("tok", {"allowed_repository": ["r1"]}),
    ("nested", {"permissions": {"allowed_repository": ["r1"], "enabled": True}, "limit": None}),
], ids=["repositories", "nested"])
async def test_kbus_call_preserves_avatar_data(tmp_path, placement, token, data):
    async with running(write_recipe(tmp_path, placement)) as server:
        status, _, body = await call(
            server, "/billing/relay_avatar_data", headers=bearer(token))
    assert (status, json.loads(body)) == (200, data)


@pytest.mark.parametrize("placement", PLACEMENTS)
async def test_kbus_call_preserves_empty_avatar_bag(tmp_path, placement):
    async with running(write_recipe(tmp_path, placement)) as server:
        assert await server.kbus_call(
            "/billing/avatar_data", {}, auth=Avatar("svc")) == {}


@pytest.mark.parametrize("placement", PLACEMENTS)
async def test_bearer_preserves_avatar_data(tmp_path, placement):
    async with running(write_recipe(tmp_path, placement)) as server:
        status, _, body = await call(
            server, "/billing/avatar_data", headers=bearer("tok"))
        assert (status, json.loads(body)) == (200, {"allowed_repository": ["r1"]})


@pytest.mark.parametrize("placement", PLACEMENTS)
async def test_session_avatar_data_reaches_handler(tmp_path, placement):
    avatar = Avatar("svc", ["admin"])
    avatar.data["allowed_repository"] = ["r1"]
    async with running(write_recipe(tmp_path, placement)) as server:
        session = server.session_store.create(avatar)
        status, _, body = await call(
            server, "/billing/avatar_data",
            headers=[(b"cookie", f"session_id={session.id}".encode())])
        assert (status, json.loads(body)) == (200, {"allowed_repository": ["r1"]})


@pytest.mark.parametrize("placement", PLACEMENTS)
async def test_kbus_call_keeps_typed_values(tmp_path, placement):
    async with running(write_recipe(tmp_path, placement)) as server:
        status, _, body = await call(
            server, "/billing/relay_typed",
            headers=[*bearer("typed"), (b"x-tytx-transport", b"json")])
    assert status == 200
    assert from_tytx(body.decode(), "json") == {"amount": Decimal("1.10"), "day": date(2026, 10, 13),
                      "types": ["Decimal", "date"],
                      "data": {"budget": Decimal("12.50"), "since": date(2026, 1, 2)}}


@pytest.mark.parametrize("placement", PLACEMENTS)
async def test_kbus_call_keeps_a_typed_scalar_answer(tmp_path, placement):
    async with running(write_recipe(tmp_path, placement)) as server:
        answer = await server.kbus_call("/billing/amount", {})
    assert answer == Decimal("3.30") and type(answer) is Decimal
