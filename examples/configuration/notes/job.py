"""Create the demo schema and list the saved IDs without starting a web server."""
from pathlib import Path

from asqueel import AsqueelDb


def main():
    database = AsqueelDb(str(Path(__file__).with_name("model.py")))
    try:
        with database.session() as session:
            session.migrate()
            session.commit()
            print(sorted(session.table("app.note").query(columns="$id").fetchPkeys()))
    finally:
        database.close()


if __name__ == "__main__":
    main()
