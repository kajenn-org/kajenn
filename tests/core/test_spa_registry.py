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

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from kajenn.spa_registry import SpaRegistry


def visit(registry, cid, page):
    registry.new_connection(cid)
    registry.new_page(page, connection_id=cid)
    return registry.page_state(page, connection_id=cid)


def test_guest_login_preserves_stores_and_all_tabs():
    registry = SpaRegistry()
    guest = visit(registry, "first", "a")
    registry.new_page("b", connection_id="first")
    guest.user_store["draft"] = "saved"
    assert guest.user == "guest_first"

    registry.change_connection_user("first", "mario")
    registry.change_connection_user("first", "mario")
    first = registry.page_state("a", connection_id="first")
    second = registry.page_state("b", connection_id="first")
    assert first.user == second.user == "mario"
    assert first.page_store is guest.page_store
    assert second.page_store is not first.page_store
    assert first.connection_store is second.connection_store is guest.connection_store
    assert first.user_store is second.user_store is guest.user_store
    assert second.user_store["draft"] == "saved"


def test_second_browser_joins_resident_then_switches_identity():
    registry = SpaRegistry()
    first = visit(registry, "first", "a")
    registry.change_connection_user("first", "mario")
    guest = visit(registry, "second", "b")
    registry.change_connection_user("second", "mario")
    joined = registry.page_state("b", connection_id="second")
    assert joined.user_store is first.user_store
    assert joined.user_store is not guest.user_store
    assert joined.page_store is guest.page_store
    assert joined.connection_store is guest.connection_store

    registry.change_connection_user("second", "anna")
    switched = registry.page_state("b", connection_id="second")
    assert switched.user == "anna"
    assert switched.user_store is not first.user_store
    assert switched.page_store is joined.page_store
    assert switched.connection_store is joined.connection_store
    assert registry.page_state("a", connection_id="first").user == "mario"


def test_page_access_and_close_are_scoped_to_connection():
    registry = SpaRegistry()
    first = visit(registry, "first", "a")
    visit(registry, "second", "b")
    registry.change_connection_user("first", "mario")
    registry.change_connection_user("second", "mario")
    with pytest.raises(KeyError):
        registry.page_state("a", connection_id="second")
    with pytest.raises(KeyError):
        registry.drop_page("a", connection_id="second")
    with pytest.raises(ValueError):
        registry.new_page("a", connection_id="second")
    registry.drop_page("a", connection_id="first")
    with pytest.raises(KeyError):
        registry.page_state("a", connection_id="first")
    registry.new_page("c", connection_id="first")
    assert (
        registry.page_state("c", connection_id="first").connection_store is first.connection_store
    )
    registry.drop_connection("first")
    assert registry.page_state("b", connection_id="second").user_store is first.user_store
    registry.new_page("c", connection_id="second")


def test_guest_identity_cannot_be_claimed_by_another_visit():
    registry = SpaRegistry()
    first = visit(registry, "first", "a")
    visit(registry, "second", "b")
    with pytest.raises(ValueError):
        registry.change_connection_user("second", "guest_first")
    assert registry.page_state("b", connection_id="second").user_store is not first.user_store


def test_concurrent_first_login_selects_one_guest_store():
    registry = SpaRegistry()
    first = visit(registry, "first", "a")
    second = visit(registry, "second", "b")
    ready = Barrier(2)

    def login(cid):
        ready.wait(timeout=5)
        registry.change_connection_user(cid, "mario")

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(login, ("first", "second")))
    store = registry.page_state("a", connection_id="first").user_store
    assert registry.page_state("b", connection_id="second").user_store is store
    assert store is first.user_store or store is second.user_store


def test_store_hooks_follow_the_logical_page_and_idle_expiry(monkeypatch):
    events = []

    class Registry(SpaRegistry):
        def new_store(self):
            return {}

        def page_attached(self, state):
            events.append(("attach", state.user_store))

        def page_user_changed(self, previous, current):
            events.append(("rebind", previous.user_store, current.user_store))

        def page_detached(self, state):
            events.append(("detach", state.page_store))

    now = 0
    monkeypatch.setattr("kajenn.spa_registry.monotonic", lambda: now)
    registry = Registry()
    registry.ensure_connection("c", None, login=True, ttl=10)
    registry.new_page("p", connection_id="c")
    state = registry.page_state("p", connection_id="c")
    registry.ensure_connection("c", "mario", login=True, ttl=10)
    assert events == [("attach", state.user_store), ("rebind", state.user_store, state.user_store)]
    now = 70
    registry.ensure_connection("next", None, login=True, ttl=10)
    assert events[-1] == ("detach", state.page_store)
    with pytest.raises(KeyError):
        registry.page_state("p", connection_id="c")


def test_failed_adapter_rebind_closes_only_that_page():
    rebound = []

    class Registry(SpaRegistry):
        def page_user_changed(self, previous, current):
            if current.page_id == "broken":
                raise RuntimeError("cannot attach the new store")
            rebound.append(current.page_id)

        def page_detached(self, state):
            if state.page_id == "broken":
                raise RuntimeError("cannot remove a broken binding")

    registry = Registry()
    visit(registry, "c", "broken")
    registry.new_page("healthy", connection_id="c")
    with pytest.raises(ExceptionGroup) as error:
        registry.change_connection_user("c", "mario")
    assert len(error.value.exceptions) == 2
    assert rebound == ["healthy"]
    assert registry.page_state("healthy", connection_id="c").user == "mario"
    with pytest.raises(KeyError):
        registry.page_state("broken", connection_id="c")
