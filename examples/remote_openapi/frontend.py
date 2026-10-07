"""Frontend configuration: one application in its own process, one local mount."""

from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES

from examples.remote_openapi.app import RemoteDemoApplication


class LocalApplication(RoutedApplication):
    """A local endpoint that remains available when the external process is down."""

    @route()
    def health(self) -> dict[str, bool]:
        return {"local": True}


class RemoteOpenApiConfiguration(CONFIGURATION_TEMPLATES["default"]):
    """``demo`` runs in a process the server spawns; ``local`` runs in the server."""

    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="local", app_class=LocalApplication, mount="")
        apps.application(code="demo", app_class=RemoteDemoApplication, spawner="subprocess")
