from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.models import Check


def test_check_status_column_is_named_and_bounded():
    column = Check.__table__.columns.get("status")

    assert column is not None
    assert column.name == "status"
    assert column.type.length == 10


def test_migrations_rename_the_legacy_check_status_column(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))

    command.upgrade(config, "20261116_01")
    engine = create_engine(database_url)
    assert {column["name"] for column in inspect(engine).get_columns("checks")} >= {
        "10"
    }

    command.upgrade(config, "head")
    assert {column["name"] for column in inspect(engine).get_columns("checks")} >= {
        "status"
    }
    assert "10" not in {
        column["name"] for column in inspect(engine).get_columns("checks")
    }
