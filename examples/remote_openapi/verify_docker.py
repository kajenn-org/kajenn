"""Exercise a live host frontend backed by this example's Docker Compose app.

Run after starting Compose and the connect-only frontend. --exercise-restart
stops and restarts only the selected Compose project's app service, proving
failure isolation and reconnection. The app is restored even on assertion failure.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

from kajenn.wsx import WsxEnvelope


class DockerProof:
    def __init__(self, base_url: str, project: str, exercise_restart: bool) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.exercise_restart = exercise_restart
        self.compose_file = Path(__file__).with_name("compose.yaml")

    async def command(self, *args: str) -> str:
        result = await asyncio.to_thread(
            subprocess.run, args, capture_output=True, text=True, timeout=60, check=True,
        )
        return result.stdout.strip()

    async def compose(self, *args: str) -> str:
        return await self.command("docker", "compose", "-p", self.project,
                                  "-f", str(self.compose_file), *args)

    async def wait_ready(self, client: httpx.AsyncClient) -> None:
        async with asyncio.timeout(40):
            while True:
                try:
                    response = await client.get("/demo/hello")
                    if response.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.25)

    async def run(self) -> None:
        container = await self.compose("ps", "-q", "app")
        if not container or "\n" in container:
            raise RuntimeError("expected exactly one running Compose app container")
        state = json.loads(await self.command("docker", "inspect", "--format", "{{json .State}}", container))
        assert state["Running"] and state["Health"]["Status"] == "healthy"
        print("Healthy container:", container, flush=True)
        runtime = await self.command("docker", "exec", container, "python", "-c",
                                     "import os,sys; print(sys.platform, os.getuid())")
        assert runtime == "linux 10001", runtime
        print("Container runtime:", runtime, flush=True)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=5) as client:
            await self.wait_ready(client)
            hello = await client.get("/demo/hello")
            assert hello.json() == {"hello": "remote", "pid": 1}
            assert (await client.get("/health")).json() == {"local": True}
            body = bytes(range(256)) * 4096
            echo = await client.post("/demo/echo", content=body)
            assert echo.status_code == 200 and echo.content == body
            print("1 MiB binary echo SHA256:", hashlib.sha256(echo.content).hexdigest(), flush=True)
            delays = [0.15, 0.01, 0.07]
            replies = await asyncio.gather(*(client.get("/demo/delay", params={"seconds": d}) for d in delays))
            assert [r.json()["waited"] for r in replies] == delays
            for path, status in [("/demo/fail", 400), ("/demo/protected", 401),
                                 ("/demo/_meta/docs", 200), ("/demo/_meta/schema_json", 200)]:
                response = await client.get(path)
                assert response.status_code == status, (path, response.status_code)
                if path.endswith("schema_json"):
                    assert response.json()["servers"] == [{"url": "/demo"}]
            uri = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/demo"
            async with connect(uri) as socket:
                await socket.send(WsxEnvelope(id="docker-hello", method="POST", path="/demo/hello").encode())
                reply = WsxEnvelope(await asyncio.wait_for(socket.recv(), 5))
                assert reply.id == "docker-hello" and reply.status == 200
                assert reply.data == {"hello": "remote", "pid": 1}
            print("HTTP, concurrency, auth refusal, Swagger and WSX passed", flush=True)
            if self.exercise_restart:
                started_at = state["StartedAt"]
                try:
                    await self.compose("stop", "--timeout", "8", "app")
                    assert (await client.get("/health")).status_code == 200
                    assert (await client.get("/demo/hello")).status_code == 503
                    print("Stopped container: local health 200, remote route 503", flush=True)
                finally:
                    await self.compose("start", "app")
                await self.wait_ready(client)
                updated = json.loads(await self.command("docker", "inspect", "--format", "{{json .State}}", container))
                assert updated["StartedAt"] != started_at
                assert (await client.get("/demo/hello")).json()["pid"] == 1
                print("New container process: frontend reconnected successfully", flush=True)
        print("Docker PoC passed; application left running", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18764")
    parser.add_argument("--project", default="genro-opaque-docker-poc")
    parser.add_argument("--exercise-restart", action="store_true")
    options = parser.parse_args()
    asyncio.run(DockerProof(options.base_url, options.project, options.exercise_restart).run())
