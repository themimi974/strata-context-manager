"""which system + tools head each request had, so the tier-0 pin goes to the most used one

Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE request_log ADD COLUMN head_chain text;
ALTER TABLE request_log ADD COLUMN pinned boolean;
CREATE INDEX request_log_head ON request_log (at, head_chain);
""")


def downgrade():
    op.execute("""
DROP INDEX IF EXISTS request_log_head;
ALTER TABLE request_log DROP COLUMN pinned, DROP COLUMN head_chain;
""")
