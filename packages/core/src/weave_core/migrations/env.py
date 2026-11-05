from alembic import context
from sqlalchemy import create_engine
from weave_core.schema import metadata

engine = create_engine(context.config.attributes["database_url"])
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
