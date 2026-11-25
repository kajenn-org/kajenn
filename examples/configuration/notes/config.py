from pathlib import Path

from genro_bag.resolvers import EnvResolver
from genro_storage import StorageManager
from kajenn.config.templates import DefaultConfiguration
from kajenn_db.asqueel import AsqueelDbHandler

from service import Notes


class Site(DefaultConfiguration):
    def storage_section(self, cfg):
        """Keep the site mounts and add a writable directory for exported reports."""
        mounts = cfg.storage(app=StorageManager)
        self.storage_mounts(mounts)
        mounts.local(name="reports", base_path=EnvResolver("NOTES_REPORTS"))

    def applications_section(self, cfg):
        """Give the notes service its own default database."""
        notes = cfg.applications().application(code="notes", app_class=Notes)
        notes.databases(handler_class=AsqueelDbHandler).database(
            code="default", source=str(Path(__file__).with_name("model.py"))
        )
