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

"""The public reload launch (#39): any launcher names the roots, the payload
travels to ``factory()`` through the environment, and the CLI is one caller."""

from __future__ import annotations

import json
import os

import pytest

from kajenn.reloading import FACTORY_TARGET, LAUNCHER_ENV, serve_reloading


def test_the_caller_names_the_roots_and_the_payload_reaches_the_environment(monkeypatch):
    launched: dict = {}
    monkeypatch.setattr(
        "kajenn.reloading.uvicorn.run",
        lambda target, **kwargs: launched.update(target=target, **kwargs),
    )
    monkeypatch.setenv(LAUNCHER_ENV, "")

    serve_reloading(
        host="127.0.0.1",
        port=9000,
        reload_dirs=["/srv/site", "/srv/packages"],
        reload_excludes=["/srv/site/data/*"],
        config="/srv/site/config.py",
        debug="sql",
    )

    assert launched["target"] == FACTORY_TARGET
    assert launched["reload_dirs"] == ["/srv/site", "/srv/packages"]
    assert launched["reload_excludes"] == ["/srv/site/data/*"]
    assert json.loads(os.environ[LAUNCHER_ENV]) == {
        "host": "127.0.0.1",
        "port": 9000,
        "config": "/srv/site/config.py",
        "debug": "sql",
    }


def test_exactly_one_source_neither_or_both_is_an_error():
    with pytest.raises(ValueError, match="exactly one"):
        serve_reloading(host="h", port=1, reload_dirs=["/x"])
    with pytest.raises(ValueError, match="exactly one"):
        serve_reloading(
            host="h", port=1, reload_dirs=["/x"], config="/c.py", application="m:App"
        )
