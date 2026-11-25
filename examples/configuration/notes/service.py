from genro_routes import route
from kajenn import RoutedApplication


class Notes(RoutedApplication):
    @route(channel_channels="api")
    def add(self, key: str, text: str, _request=None) -> dict:
        session = _request.db()
        session.table("app.note").insert({"id": key, "text": text})
        session.commit()
        return {"saved": key}

    @route(channel_channels="api,mcp")
    async def keys(self, _request=None) -> dict:
        session = _request.async_db()
        keys = await session.table("app.note").query(columns="$id").fetchPkeys()
        return {"keys": sorted(keys)}

    @route(channel_channels="api")
    def export(self, _request=None) -> dict:
        keys = _request.db().table("app.note").query(columns="$id").fetchPkeys()
        target = self.server.storage.node("reports:notes.txt")
        target.write_text("\n".join(sorted(keys)))
        return {"count": len(keys)}
