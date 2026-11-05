"""Apply versioned schema migrations without printing the database credentials."""
from pathlib import Path
from alembic import command
from alembic.config import Config


def migrate():
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    migrate()
    print("Database schema is up to date.")
