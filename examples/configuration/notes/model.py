"""Describe the database once for the web service and the standalone job."""
from asqueel import SqlDatabaseConfig
from genro_bag.resolvers import EnvResolver


class NotesModel(SqlDatabaseConfig):
    def main(self, root):
        database = root.db("notes", implementation="sqlite")
        database.connection("main", database=EnvResolver("NOTES_SQLITE"))
        package = database.packages().package("app", sqlschema="main", sqlprefix=False)
        note = package.table("note", pkey="id")
        note.column("id", size=":22")
        note.column("text")
