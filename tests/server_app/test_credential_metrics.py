# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The credential check family, counted in the server where the verification runs.

Every test drives a real ``AsgiServer`` and reads the family the way
Prometheus reads it: GET ``/_server/metrics`` with an API key whose identity
holds only ``SERVER_METRICS``. That read verifies its own fresh key: every
read adds one accepted ``api_key`` check, counted before the text is written.

What the suite pins:

- an accepted API key and a refused one grow the counter with their
  ``scheme`` and ``outcome``; a second request with the accepted key is a
  cache hit and does not;
- accepted ``basic`` and ``jwt`` credentials count with their backend; a
  refused credential counts with the scheme its form shows (``gak_`` →
  ``api_key``, three dot-separated parts → ``jwt``, otherwise the header
  word ``bearer``/``basic``);
- a malformed ``Authorization`` value is refused with ``scheme="other"``;
- the credential check of a ``spawner="subprocess"`` application is counted
  in the server;
- the password login counts ``scheme="password"`` accepted and refused;
- the OIDC callback counts ``scheme="oidc"`` accepted and refused.
"""

from __future__ import annotations

import base64
from typing import Any

import jwt
from genro_routes import route

from kajenn import AsgiServer, BaseApplication, RoutedApplication
from kajenn.server_app import ServerApplication
from tests.conftest import call, json_body, read_samples, running, start_server, value
from tests.server_app.test_login_flow import SeededUserStore
from tests.server_app.test_oidc import (  # noqa: F401 — mock_provider is a fixture
    EXTERNAL_URL,
    PROVIDER,
    begin_flow,
    drive,
    mock_provider,
    response_status,
)
from tests.storage_support import MemoryApiKeyStore

CHECKS = "kajenn_credential_checks_total"


class Vault(RoutedApplication):
    """One route reserved to the ``admin`` tag."""

    @route(auth_rule="admin")
    def secret(self) -> dict[str, str]:
        return {"secret": "kept"}


def bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


def checks(samples: list[Any], scheme: str, outcome: str) -> float:
    return value(samples, CHECKS, scheme=scheme, outcome=outcome)


async def vault_server(**kwargs: Any) -> AsgiServer:
    return await start_server(AsgiServer(
        applications=[(Vault, {"code": "vault"})],
        tokens={"store_class": MemoryApiKeyStore}, **kwargs))


async def test_accepted_and_refused_keys_are_counted_and_a_cache_hit_is_not() -> None:
    server = await vault_server()
    key = server.api_key_store.issue("operator", ["admin"])
    assert (await call(server, "GET", "/vault/secret", headers=bearer(key)))["status"] == 200
    assert (await call(server, "GET", "/vault/secret", headers=bearer(key)))["status"] == 200
    assert (await call(server, "GET", "/vault/secret",
                       headers=bearer("gak_unknown")))["status"] == 401
    samples = await read_samples(server)
    # One check for ``key`` (the second request is a cache hit), one for the read.
    assert checks(samples, "api_key", "accepted") == 2
    assert checks(samples, "api_key", "refused") == 1


SECRET = "a-test-secret-of-at-least-thirty-two-bytes"


def basic(user: str, password: str) -> list[tuple[bytes, bytes]]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return [(b"authorization", f"Basic {token}".encode())]


async def test_basic_and_jwt_count_with_their_scheme() -> None:
    server = await vault_server(auth={
        "basic": {"operator": {"password": "pw", "tags": "admin"}},
        "jwt": [{"secret": SECRET, "tags": "admin"}],
    })
    token = jwt.encode({"sub": "operator"}, SECRET, algorithm="HS256")
    forged = jwt.encode({"sub": "operator"}, SECRET + "-other", algorithm="HS256")
    for headers, status in ((basic("operator", "pw"), 200), (bearer(token), 200),
                            (basic("operator", "wrong"), 401), (bearer(forged), 401),
                            (bearer("static-unknown"), 401)):
        assert (await call(server, "GET", "/vault/secret", headers=headers))["status"] == status
    samples = await read_samples(server)
    assert checks(samples, "basic", "accepted") == 1
    assert checks(samples, "jwt", "accepted") == 1
    assert checks(samples, "basic", "refused") == 1
    assert checks(samples, "jwt", "refused") == 1
    assert checks(samples, "bearer", "refused") == 1


async def test_a_malformed_authorization_is_refused_as_other() -> None:
    server = await vault_server()
    headers = [(b"authorization", b"nospace")]
    assert (await call(server, "GET", "/vault/secret", headers=headers))["status"] == 401
    headers = [(b"authorization", b"Digest abc")]
    assert (await call(server, "GET", "/vault/secret", headers=headers))["status"] == 401
    samples = await read_samples(server)
    assert checks(samples, "other", "refused") == 2


RECIPE = '''
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryApiKeyStore


class Vault(RoutedApplication):
    @route(auth_rule="admin")
    def secret(self):
        return {"secret": "kept"}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        cfg.applications().application(code="vault", app_class=Vault,
                                       spawner="subprocess", request_timeout=5)
'''


async def test_the_check_of_a_spawned_application_is_counted_in_the_server(tmp_path) -> None:
    config = tmp_path / "config.py"
    config.write_text(RECIPE)
    async with running(str(config)) as server:
        key = server.api_key_store.issue("operator", ["admin"])
        assert (await call(server, "GET", "/vault/secret", headers=bearer(key)))["status"] == 200
        assert (await call(server, "GET", "/vault/secret",
                           headers=bearer("gak_unknown")))["status"] == 401
        samples = await read_samples(server)
    assert checks(samples, "api_key", "accepted") == 2
    assert checks(samples, "api_key", "refused") == 1


async def test_the_password_login_is_counted() -> None:
    server = await start_server(AsgiServer(
        applications=[ServerApplication, (BaseApplication, {"mount": ""})],
        users={"store_class": SeededUserStore}, tokens={"store_class": MemoryApiKeyStore}))
    for password in ("wonder", "wrong", ""):
        body, headers = json_body({"identity": "alice", "password": password})
        assert (await call(server, "POST", "/_server/login", body, headers))["status"] == 200
    samples = await read_samples(server)
    assert checks(samples, "password", "accepted") == 1
    assert checks(samples, "password", "refused") == 2


async def test_the_oidc_callback_is_counted(mock_provider: None) -> None:  # noqa: F811
    server = await start_server(AsgiServer(
        applications=[(ServerApplication, {"oidc": {"google": PROVIDER}}),
                      (BaseApplication, {"mount": ""})],
        external_url=EXTERNAL_URL, tokens={"store_class": MemoryApiKeyStore}))
    session_id, state = await begin_flow(server)
    _, sent = await drive(server, f"/_server/auth/oidc:google/callback?state={state}&code=c",
                          cookie=f"session_id={session_id}")
    assert response_status(sent) == 302
    session_id, _ = await begin_flow(server)
    _, sent = await drive(server, "/_server/auth/oidc:google/callback?state=forged&code=c",
                          cookie=f"session_id={session_id}")
    assert response_status(sent) == 400
    samples = await read_samples(server)
    assert checks(samples, "oidc", "accepted") == 1
    assert checks(samples, "oidc", "refused") == 1
