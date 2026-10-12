# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Avatar data survives bus calls and external placement through public requests."""

import json

import pytest
from genro_bag import Bag

from kajenn import Avatar

from .test_external_application import running
from .test_external_parity import PLACEMENTS, bearer, call

RECIPE = '''
import os

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


class Idp(RoutedApplication):
    @route()
    def check(self, credential: str = "", channel: str = ""):
        assert credential == "Bearer tok"
        assert channel == "rest"
        return {"identity": "idp-user", "tags": ["admin"],
                "data": {"allowed_repository": ["r1"]}}


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
@pytest.mark.parametrize("data", [
    {"allowed_repository": ["r1"]},
    {"permissions": {"allowed_repository": ["r1"], "enabled": True}, "limit": None},
], ids=["repositories", "nested"])
async def test_kbus_call_preserves_avatar_data(tmp_path, placement, data):
    avatar = Avatar("svc", ["admin"])
    avatar.data.update(Bag(data))
    async with running(write_recipe(tmp_path, placement)) as server:
        assert await server.kbus_call("/billing/avatar_data", {}, auth=avatar) == data


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
