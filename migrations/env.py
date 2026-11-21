import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ["CTX_DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)


def run():
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run()
