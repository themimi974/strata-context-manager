"""file reads seen by the gateway (file state cache): one row per exact content id

Revision ID: 0004
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE file_segment (
    seg_id      text PRIMARY KEY,                 -- sha256(validity root | exact tool-result text)[:32]
    tool        text,
    chars       integer,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    seen_count  bigint NOT NULL DEFAULT 1
);
""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS file_segment;")
