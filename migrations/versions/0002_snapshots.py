"""snapshot bookkeeping on the request log; snapshots keyed by any chain node

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE request_log ADD COLUMN completion_tokens integer;
ALTER TABLE request_log ADD COLUMN restored_chain text;
ALTER TABLE request_log ADD COLUMN restore_ms double precision;
ALTER TABLE request_log ADD COLUMN saved_chain text;
ALTER TABLE request_log ADD COLUMN save_ms double precision;
ALTER TABLE request_log ADD COLUMN engine_epoch text;
ALTER TABLE kv_snapshot ADD COLUMN saved_tokens integer;
CREATE INDEX kv_snapshot_lru ON kv_snapshot (last_used);
""")


def downgrade():
    op.execute("""
DROP INDEX IF EXISTS kv_snapshot_lru;
ALTER TABLE kv_snapshot DROP COLUMN saved_tokens;
ALTER TABLE request_log DROP COLUMN engine_epoch, DROP COLUMN save_ms, DROP COLUMN saved_chain,
  DROP COLUMN restore_ms, DROP COLUMN restored_chain, DROP COLUMN completion_tokens;
""")
