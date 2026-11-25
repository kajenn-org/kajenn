from pathlib import Path

from genro_routes import route
from gramlot_py_server.kajennspa import Application
from kajenn.config.templates import DefaultConfiguration


class Workspace(Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.add_pages("pages", Path(__file__).with_name("pages"))

    @route(channel_channels="api,mcp")
    def status(self) -> dict:
        return {"workspace": self.config("parameters.label")}


class Site(DefaultConfiguration):
    def applications_section(self, cfg):
        """Publish the workspace and choose the label shown to this site's users."""
        app = cfg.applications().application(code="work", app_class=Workspace)
        app.parameters(label="Editorial")
