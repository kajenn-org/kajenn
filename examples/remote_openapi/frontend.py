"""Frontend composition with one remote mount and one unrelated local mount."""

from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.remote_application import RemoteApplication


class LocalApplication(RoutedApplication):
    """A local endpoint that remains available when the remote peer is down."""

    @route()
    def health(self) -> dict[str, bool]:
        return {"local": True}


def create_server(address: str, *, own_process: bool = True) -> AsgiServer:
    """Build a frontend; ``own_process=False`` connects to an external runner."""
    factory = "examples.remote_openapi.app:create_application" if own_process else None
    return AsgiServer(
        applications=[
            LocalApplication(code="local", mount=""),
            RemoteApplication(address=address, factory=factory, code="demo", mount="demo"),
        ]
    )
