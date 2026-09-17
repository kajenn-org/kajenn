"""Probe application readiness over the actual frame protocol."""

import asyncio

from kajenn.channel.frame import Frame
from kajenn.remote_connection import RemoteConnection


class ReadinessCheck:
    async def run(self) -> None:
        connection = RemoteConnection("tcp:127.0.0.1:8765", timeout=2)
        try:
            reply = await connection.call(Frame(method="CALL", path="/_ready"))
            if reply.info.get("ready") is not True:
                raise RuntimeError("remote application is not ready")
        finally:
            await connection.close()


if __name__ == "__main__":
    asyncio.run(ReadinessCheck().run())
