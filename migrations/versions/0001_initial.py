"""initial schema: blocks, profiles, chain nodes, snapshots, request log

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE context_block (
    block_hash      text PRIMARY KEY,                 -- sha256(kind \\0 exact bytes)
    kind            text NOT NULL,                    -- system | tools | user | assistant | tool
    source_path     text,
    stability_tier  smallint NOT NULL,                -- 0 system .. 4 conversation
    version         integer NOT NULL DEFAULT 1,       -- per source_path, increments when its content changes
    content         text NOT NULL,
    token_estimate  integer NOT NULL,
    volatile        text[] NOT NULL DEFAULT '{}',
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    seen_count      bigint NOT NULL DEFAULT 1
);
CREATE INDEX context_block_source ON context_block (source_path) WHERE source_path IS NOT NULL;

CREATE TABLE context_profile (
    profile_id    bigserial PRIMARY KEY,
    name          text UNIQUE NOT NULL,
    block_hashes  text[] NOT NULL,                    -- ordered
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- every prefix ever observed: chain_hash covers the validity tuple and all blocks up to this one
CREATE TABLE chain_node (
    chain_hash     text PRIMARY KEY,
    parent_hash    text,                              -- NULL for depth 1 (parent is the validity root)
    block_hash     text NOT NULL REFERENCES context_block (block_hash),
    depth          integer NOT NULL,
    validity_root  text NOT NULL,
    token_offset   integer NOT NULL,                  -- estimated tokens before this block
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now(),
    hits           bigint NOT NULL DEFAULT 0
);
CREATE INDEX chain_node_parent ON chain_node (parent_hash);

-- engine states we actually hold (slot files, pinned prefixes), keyed by the chain they reproduce
CREATE TABLE kv_snapshot (
    chain_hash        text PRIMARY KEY REFERENCES chain_node (chain_hash),
    model_id          text NOT NULL,
    weights_hash      text NOT NULL,
    quant             text NOT NULL,
    engine_version    text NOT NULL,
    template_version  text NOT NULL,
    token_count       integer NOT NULL,
    tier              text NOT NULL CHECK (tier IN ('gpu', 'ram', 'nvme')),
    location          text NOT NULL,
    size_bytes        bigint,
    pinned            boolean NOT NULL DEFAULT false,
    stale             boolean NOT NULL DEFAULT false,
    hits              bigint NOT NULL DEFAULT 0,
    recompute_ms      double precision,               -- what a miss would cost (for value-based eviction)
    last_used         timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE request_log (
    id                bigserial PRIMARY KEY,
    at                timestamptz NOT NULL DEFAULT now(),
    call_id           text,
    model             text,
    n_blocks          integer,
    n_messages        integer,
    chain_head        text,
    matched_depth     integer,                        -- longest already-seen prefix, in blocks
    matched_tokens    integer,                        -- its estimated size
    total_tokens_est  integer,
    volatile          text[],
    prompt_tokens     integer,                        -- from the engine, after the call
    cached_tokens     integer,
    latency_ms        double precision,
    ttft_ms           double precision
);
CREATE INDEX request_log_call ON request_log (call_id);

-- a file (source_path) got new content: tell the re-warm worker
CREATE FUNCTION ctx_notify_block() RETURNS trigger AS $$
BEGIN
  IF NEW.source_path IS NOT NULL THEN
    PERFORM pg_notify('ctx_block_changed', json_build_object('block_hash', NEW.block_hash,
                      'source_path', NEW.source_path, 'version', NEW.version)::text);
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER context_block_notify AFTER INSERT ON context_block
  FOR EACH ROW EXECUTE FUNCTION ctx_notify_block();
""")


def downgrade():
    op.execute("""
DROP TRIGGER IF EXISTS context_block_notify ON context_block;
DROP FUNCTION IF EXISTS ctx_notify_block();
DROP TABLE IF EXISTS request_log, kv_snapshot, chain_node, context_profile, context_block;
""")
