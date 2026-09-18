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

"""Contract: runnable public recipes answer real HTTP and WSX requests.

Examples are extracted from the documentation, with only the listening port
changed. Each server runs in its own subprocess and private storage/home.
"""

import base64
from contextlib import contextmanager
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest


class PublicRecipes:
    """Read complete top-level Python fences from the published guide sources."""

    def __init__(self):
        self.root = Path(__file__).resolve().parents[2]

    def get_code(self, page, marker):
        text = (self.root / "docs" / page).read_text(encoding="utf-8")
        blocks = re.findall(r"^```python\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
        matches = [block for block in blocks if marker in block]
        assert len(matches) == 1, f"{page}: expected one recipe containing {marker!r}"
        return matches[0]


class PublicServer:
    """Own one documentation subprocess, its logs and local HTTP address."""

    def __init__(self, code, directory):
        self.directory = directory
        self.process = None
        self.log = None
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.recipe = directory / "recipe.py"
        assert "port=8000" in code, "Runnable recipe must declare its testable port"
        self.recipe.write_text(code.replace("port=8000", f"port={self.port}"), encoding="utf-8")

    def start(self):
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("KAJENN_", "GENRO_", "GNR_"))
        }
        self.environment.update(
            KAJENN_HOME=str(self.directory / "home"), TMPDIR=str(self.directory)
        )
        self.log = (self.directory / "server.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-I", str(self.recipe)],
            cwd=self.directory,
            env=self.environment,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(self.get_log())
            try:
                self.get_response("/")
                return
            except (OSError, URLError):
                time.sleep(0.05)
        raise AssertionError(f"Documentation server did not become ready:\n{self.get_log()}")

    def get_response(self, path, data=None, headers=None):
        request = Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers or {}
        )
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, response.read()
        except HTTPError as response:
            with response:
                return response.code, response.read()

    def get_log(self):
        if self.log is not None:
            self.log.flush()
        return (self.directory / "server.log").read_text(encoding="utf-8")

    def stop(self):
        try:
            if self.process is not None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=4)
                    raise AssertionError(f"Documentation server did not stop:\n{self.get_log()}") from None
        finally:
            if self.log is not None:
                self.log.close()


@pytest.fixture
def recipes():
    return PublicRecipes()


@pytest.fixture
def serve_recipe(tmp_path):
    @contextmanager
    def serve(code):
        server = PublicServer(code, tmp_path)
        try:
            server.start()
            yield server
        finally:
            server.stop()

    return serve


def test_getting_started_http(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("getting-started.md", "# hello.py")) as server:
        for path, name in (("/index", "world"), ("/greet?name=genro", "genro"), ("/greet", "world")):
            status, body = server.get_response(path)
            assert status == 200
            assert json.loads(body) == {"hello": name}
        assert server.get_response("/nowhere")[0] == 404


def test_request_bodies_and_multipart(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/requests.md", "class Bodies")) as server:
        status, body = server.get_response(
            "/document", b'{"name":"Ada"}', {"Content-Type": "application/json"}
        )
        assert status == 200
        assert json.loads(body) == {"received": {"name": "Ada"}}
        status, body = server.get_response(
            "/raw/count", b"abc", {"Content-Type": "application/octet-stream"}
        )
        assert status == 200
        assert json.loads(body) == {"bytes": 3}
        # The decoded application refuses the content-type the raw one serves.
        assert server.get_response(
            "/document", b"abc", {"Content-Type": "application/octet-stream"}
        )[0] == 415
        multipart = (
            '--docsboundary\r\nContent-Disposition: form-data; name="title"\r\n\r\n'
            'Example\r\n--docsboundary\r\nContent-Disposition: form-data; '
            'name="document"; filename="demo.txt"\r\nContent-Type: text/plain\r\n\r\n'
            'abc\r\n--docsboundary--\r\n'
        ).encode()
        status, body = server.get_response(
            "/upload", multipart, {"Content-Type": "multipart/form-data; boundary=docsboundary"}
        )
        assert status == 200
        assert json.loads(body) == {"title": "Example", "filename": "demo.txt", "bytes": 3}
        assert server.get_response("/raw/count")[0] == 400


def test_authentication_statuses(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/authentication.md", "class App")) as server:
        assert server.get_response("/secret", headers={"Accept": "application/json"})[0] == 401
        credential = base64.b64encode(b"alice:wonderland").decode()
        assert server.get_response("/secret", headers={"Authorization": f"Basic {credential}"})[0] == 200
        assert server.get_response("/secret", headers={"Authorization": "Bearer sk_live_xyz"})[0] == 403


def test_openapi_and_argument_errors(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/openapi.md", "class Shop")) as server:
        status, body = server.get_response("/_meta/schema_json")
        assert status == 200
        assert json.loads(body)["openapi"] == "3.1.0"
        assert server.get_response("/_meta/docs")[0] == 200
        assert server.get_response("/search?max_price=invalid")[0] == 400
        assert server.get_response("/search?extra=1")[0] == 400
        assert "400" in json.loads(body)["paths"]["/search"]["get"]["responses"]


def test_wsx_client_roundtrip(recipes, serve_recipe, tmp_path):
    hello = recipes.get_code("getting-started.md", "# hello.py")
    with serve_recipe(hello) as server:
        client = recipes.get_code("guides/websockets.md", "async def main")
        client = client.replace("127.0.0.1:8000", f"127.0.0.1:{server.port}")
        result = subprocess.run(
            [sys.executable, "-I", "-c", client], cwd=tmp_path, env=server.environment,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_mcp_tools(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/mcp.md", "class Calc")) as server:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        status, body = server.get_response("/mcp", payload, {"Content-Type": "application/json"})
        assert status == 200
        assert {"add", "mul"} <= {item["name"] for item in json.loads(body)["result"]["tools"]}
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 2, "b": 3}},
        }).encode()
        status, body = server.get_response("/mcp", payload, {"Content-Type": "application/json"})
        assert status == 200
        reply = json.loads(body)
        assert "error" not in reply
        assert json.loads(reply["result"]["content"][0]["text"]) == {"result": 5}


def test_complete_configuration_readback(recipes, tmp_path):
    from cryptography.fernet import Fernet

    code = recipes.get_code("guides/configuration.md", "from kajenn import AsgiServer")
    site = tmp_path / "site"
    site.mkdir()
    code = code.replace("/srv/shop", str(site))
    code += '''
server = AsgiServer(config=ServerConfiguration)
assert server.config("server.host") == "127.0.0.1"
assert server.config("server.port") == 8123
assert server.config("server.session.ttl") == 3600
assert server.config("middleware.cors") is True
assert server.config("applications.shop.catalog.page_size") == 20
shop = server.applications["shop"]
assert shop.config("parameters.currency") == "EUR"
assert shop.config("catalog.title") == "Outlet"
assert shop.config("catalog.locale", default="it") == "it"
'''
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("KAJENN_", "GENRO_", "GNR_", "SHOP_"))
    }
    environment.update(
        KAJENN_HOME=str(tmp_path / "home"), TMPDIR=str(tmp_path),
        SHOP_PORT="8123", SHOP_STORAGE_KEY=Fernet.generate_key().decode(),
        SHOP_ADMIN_PASSWORD="temporary-documentation-check",
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Contract tests: new learning-path recipes must work as published.
def test_intro_recipe_reuses_hello(recipes, serve_recipe, tmp_path):
    (tmp_path / "hello.py").write_text(recipes.get_code("getting-started.md", "# hello.py"))
    code = recipes.get_code("configuration.md", "from hello import Hello")
    # -I deliberately removes the script directory; restore only this example's sibling import.
    code = f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n" + code
    with serve_recipe(code) as server:
        assert json.loads(server.get_response("/greet?name=Ada")[1]) == {"hello": "Ada"}


def test_request_context_reconnects_session(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/requests.md", "class ContextApp")) as server:
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        values = []
        for _ in range(2):
            with client.open(f"http://127.0.0.1:{server.port}/visit", timeout=4) as response:
                value = json.load(response)
                assert response.headers["X-Visit-Count"] == str(value["visits"])
                values.append(value)
        assert [item["visits"] for item in values] == [1, 2]
        assert values[0]["session_id"] == values[1]["session_id"]


def test_storage_roundtrip(recipes, serve_recipe, tmp_path):
    with serve_recipe(recipes.get_code("guides/storage.md", "class Files")) as server:
        assert json.loads(server.get_response("/save?text=hello", b"")[1]) == {"text": "hello"}
        assert json.loads(server.get_response("/read")[1]) == {"text": "hello"}
        assert (tmp_path / "data" / "note.txt").read_text() == "hello"


def test_database_selection_and_cleanup(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/databases.md", "class ExampleDatabase")) as server:
        for path, expected in (("database", 0), ("database", 1), ("lookup", 2), ("lookup", 2)):
            assert json.loads(server.get_response("/" + path)[1]) == {
                "label": "catalog", "previous_cleanups": expected,
            }


def test_task_submission_reaches_result(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/tasks.md", "class Jobs")) as server:
        task_id = json.loads(server.get_response("/submit?a=2&b=3", b"")[1])["task_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = json.loads(server.get_response(f"/result?task_id={task_id}")[1])
            if result["task"]["status"] in ("terminated", "aborted"):
                break
            time.sleep(0.05)
        assert result["task"]["status"] == "terminated", result
        assert result["result"] == {"result": 5}


def test_streaming_and_sse_routes(recipes, serve_recipe):
    with serve_recipe(recipes.get_code("guides/streaming.md", "class Streams")) as server:
        assert server.get_response("/download") == (200, b"onetwo")
        status, body = server.get_response("/updates")
        assert status == 200
        assert b"data: a\n\n" in body and b"data: b\n\n" in body
        assert b"retry: 5000" in body


def test_management_login_tokens_and_permissions(recipes, serve_recipe, monkeypatch):
    # A fixed, disposable Fernet key for this isolated test directory only.
    monkeypatch.setenv("DEMO_STORAGE_KEY", base64.urlsafe_b64encode(bytes(range(32))).decode())
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "local-test-password")
    operator = {"Authorization": "Basic " + base64.b64encode(b"operator:local-test-password").decode()}
    with serve_recipe(recipes.get_code("guides/management.md", "class Members")) as server:
        assert server.get_response("/_server/monitor/snapshot")[0] == 401
        assert server.get_response("/_server/monitor/snapshot", headers=operator)[0] == 200
        payload = json.dumps({"password": "demo-password", "password_confirm": "demo-password", "tags": ["member"]}).encode()
        status, body = server.get_response("/_server/users/create_user?identity=alice", payload,
                                          {**operator, "Content-Type": "application/json"})
        assert status == 200 and json.loads(body)["identity"] == "alice", body
        assert "password_hash" not in json.loads(body)
        assert len(json.loads(server.get_response("/_server/users/list", headers=operator)[1])["users"]) == 1
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        url = f"http://127.0.0.1:{server.port}"
        request = Request(url + "/_server/login", json.dumps({"identity": "alice", "password": "demo-password"}).encode(),
                          {"Content-Type": "application/json"})
        with client.open(request, timeout=4) as response:
            login = json.load(response)
        assert login["identity"] == "alice"
        with client.open(url + "/private", timeout=4) as response:
            assert json.load(response) == {"identity": "alice"}
        with pytest.raises(HTTPError) as denied:
            client.open(url + "/_server/monitor/snapshot", timeout=4)
        assert denied.value.code == 403
        denied.value.close()
        request = Request(url + "/_server/logout?session_id=" + login["session_id"], b"")
        with client.open(request, timeout=4) as response:
            assert json.load(response)["status"] == "ok"
        with pytest.raises(HTTPError) as denied:
            client.open(url + "/private", timeout=4)
        assert denied.value.code == 401
        denied.value.close()
        status, body = server.get_response("/_server/tokens/issue", json.dumps({"label": "local-client", "tags": ["member"]}).encode(),
                                          {**operator, "Content-Type": "application/json"})
        assert status == 200
        key = json.loads(body)["key"]
        bearer = {"Authorization": "Bearer " + key}
        assert server.get_response("/private", headers=bearer)[0] == 200
        records = json.loads(server.get_response("/_server/tokens/list", headers=operator)[1])["tokens"]
        assert len(records) == 1 and "secret_hash" not in records[0]
        key_id = records[0]["key_id"]
        status, body = server.get_response("/_server/tokens/revoke?key_id=" + key_id, b"", operator)
        assert status == 200 and json.loads(body)["revoked"] is True
        assert server.get_response("/private", headers=bearer)[0] == 401


@pytest.mark.parametrize("page,marker", [
    ("guides/sessions.md", 'middleware={"session"'),
    ("guides/authentication.md", "PROVIDER ="),
])
def test_secondary_application_declarations(recipes, page, marker, tmp_path):
    code = "from kajenn import AsgiServer, RoutedApplication, MemorySessionStore\nclass App(RoutedApplication):\n    pass\n" + recipes.get_code(page, marker)
    code += "\nassert isinstance(server, AsgiServer)\n"
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("KAJENN_", "GENRO_", "GNR_"))}
    environment["KAJENN_HOME"] = str(tmp_path / "home")
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_storage_encryption_recipe(recipes, tmp_path):
    code = recipes.get_code("guides/storage.md", "import os")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("KAJENN_", "GENRO_", "GNR_"))}
    environment.update(KAJENN_HOME=str(tmp_path / "home"),
                       DEMO_STORAGE_KEY=base64.urlsafe_b64encode(bytes(range(32))).decode())
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert b"private demo" not in (tmp_path / "encrypted-demo.txt").read_bytes()


def test_custom_middleware_recipe(recipes, serve_recipe):
    setup = recipes.get_code("guides/middleware.md", "class App")
    declarations = setup.split("server = AsgiServer")[0]
    custom = recipes.get_code("guides/middleware.md", "class StampMiddleware")
    with serve_recipe(declarations + custom + '\nserver.serve(host="127.0.0.1", port=8000)') as server:
        status, body = server.get_response("/index")
        assert status == 200 and json.loads(body) == {"ok": True}


def test_interval_task_recipe(recipes, serve_recipe, tmp_path):
    code = recipes.get_code("guides/tasks.md", "class Jobs")
    method = recipes.get_code("guides/tasks.md", '@route(task="cleanup"')
    method = "\n".join("    " + line for line in method.splitlines())
    code = code.replace('if __name__ == "__main__":', method + '\n\nif __name__ == "__main__":')
    with serve_recipe(code):
        log = tmp_path / "tasks" / "logs" / "cleanup.jsonl"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if log.exists() and log.read_text().strip():
                break
            time.sleep(0.05)
        assert log.exists(), "The documented interval did not execute"
        assert json.loads(log.read_text().splitlines()[0])["outcome"] == "ok"


def test_mounted_openapi_recipe(recipes, serve_recipe):
    code = recipes.get_code("guides/openapi.md", "class Shop").split("server = AsgiServer")[0]
    code += "\nSubApi = Shop\n" + recipes.get_code("guides/openapi.md", '"routing_class": SubApi()')
    code += '\nserver.serve(host="127.0.0.1", port=8000)'
    with serve_recipe(code) as server:
        status, body = server.get_response("/mount/api/search?q=coffee")
        assert status == 200 and json.loads(body)["query"] == "coffee"
        assert server.get_response("/mount/_meta/schema_json")[0] == 200
        assert server.get_response("/mount/_meta/docs")[0] == 200


def test_hosted_application_recipe(recipes, serve_recipe):
    code = "from kajenn import AsgiServer\n"
    code += recipes.get_code("guides/applications.md", "class HostedApplication")
    code += '''
async def existing_asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": scope["path"].encode()})
'''
    code += recipes.get_code("guides/applications.md", '"asgi_app": existing_asgi_app')
    code += '\nserver.serve(host="127.0.0.1", port=8000)'
    with serve_recipe(code) as server:
        assert server.get_response("/external/hello") == (200, b"/hello")
