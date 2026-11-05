from alembic import context
from app.database import Base, engine as app_engine
from app import models  # noqa: F401

if context.is_offline_mode():
    raise RuntimeError("Use online migrations: alembic upgrade head (the initial revision inspects existing tables).")

# Reuse configured SSL arguments; never put %-encoded passwords in ConfigParser.
engine = app_engine.execution_options()
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
