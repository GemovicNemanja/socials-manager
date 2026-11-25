from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from socialmgr.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


connectable = config.attributes.get("connection")
if connectable is not None:
    _run(connectable)
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as conn:
        _run(conn)
