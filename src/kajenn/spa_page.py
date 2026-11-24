# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""A page with a stable identity and stores owned by its application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .spa_application import SpaApplicationBase
    from .spa_registry import PageState


class Page:
    """Override persistent to choose the instance lifetime for this page class."""

    persistent: ClassVar[bool | None] = None

    def __init__(self, application: SpaApplicationBase, page_id: str, connection_id: str) -> None:
        self.application = application
        self.page_id = page_id
        self.connection_id = connection_id

    @property
    def state(self) -> PageState:
        """Resolve the current owner and stores, including after a login."""
        return self.application.registry.page_state(self.page_id, connection_id=self.connection_id)

    @property
    def store(self) -> Any:
        return self.state.page_store

    async def send_message(self, path: str, data: Any = None) -> bool:
        """Send to this page's connected browser. False means no current channel."""
        return await self.application.send_page_message(
            self.page_id, path, data, connection_id=self.connection_id
        )
