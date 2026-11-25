from genro_bag.resolvers import EnvResolver
from kajenn.config.templates import DefaultConfiguration

from applications import Catalog, Desk


class Site(DefaultConfiguration):
    def applications_section(self, cfg):
        """Send the site root to the catalogue and give the desk its own visits."""
        apps = cfg.applications(default="catalog")
        catalog = apps.application(code="catalog", app_class=Catalog)
        catalog.parameters(label=EnvResolver("CATALOG_LABEL", default="Books"))
        apps.application(code="desk", app_class=Desk, persistent=False)
