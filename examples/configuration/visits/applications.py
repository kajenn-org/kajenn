"""A typed service and a SPA whose tabs have separate drafts."""
from genro_routes import route
from kajenn import Page, RoutedApplication, SpaApplicationBase


class Catalog(RoutedApplication):
    @route(channel_channels="api")
    def index(self) -> dict:
        return self.label()

    @route(channel_channels="api,mcp")
    def label(self) -> dict:
        return {"label": self.config("parameters.label")}


class Draft(Page):
    def summary(self):
        return {"page": self.page_id, "text": self.store["text"]}


class Desk(SpaApplicationBase):
    page_class = Draft

    @route(channel_channels="api")
    def new_tab(self, _request=None) -> dict:
        page = self.open_page(_request)
        page.store["text"] = ""
        return page.summary()

    @route(channel_channels="api")
    def save(self, page_id: str, text: str, _request=None) -> dict:
        page = self.get_page(_request, page_id)
        page.store["text"] = text
        return page.summary()

    @route(channel_channels="api")
    def read(self, page_id: str, _request=None) -> dict:
        return self.get_page(_request, page_id).summary()
