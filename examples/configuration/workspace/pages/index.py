from gramlot import endpoint
from gramlot_py_server.kajennspa import Page as SpaPage


class Page(SpaPage):
    def main(self, root):
        root.h1("Workspace")
        root.p(self.application.status()["workspace"])

    @endpoint
    def workspace_status(self):
        return self.application.status()
