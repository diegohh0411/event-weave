from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine


def make_engine(url: str) -> Engine:
    return create_engine(url)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def migrate(url: str) -> str:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return ScriptDirectory.from_config(config).get_current_head()
