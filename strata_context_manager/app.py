"""strata-context-manager: the Context Manager service.

CTX_MODE=passthrough  describe every request, never touch the engine
CTX_MODE=snapshot     also save long conversations to the engine's NVMe slot files and restore them when the engine
                      lost them (restart, GPU reset, eviction). The request itself is never changed.

POST /v1/observe   pre-call hook: blocks, chain, longest already-seen prefix; in snapshot mode may restore first
POST /v1/result    success hook: tokens and timings; in snapshot mode may save a snapshot (in the background)
GET  /v1/stats     summary          GET /health
"""
from __future__ import annotations

import asyncio
import collections
import json
import time
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import policy
from .blocks import Validity, chain, request_blocks
from .canon import move_volatile_lines
from .engine import StrataAdapter

MODE = os.environ.get("CTX_MODE", "passthrough")
VALIDITY = Validity(
    model_id=os.environ.get("CTX_MODEL_ID", "qwen3.8-flash-next"),
    weights_hash=os.environ.get("CTX_WEIGHTS_HASH", "unknown"),
    quant=os.environ.get("CTX_QUANT", "IQ2_XS"),
    engine_version=os.environ.get("CTX_ENGINE_VERSION", "unknown"),
    template_version=os.environ.get("CTX_TEMPLATE_VERSION", "unknown"),
)
ENGINE_URL = os.environ.get("CTX_ENGINE_URL", "")               # Strata server, e.g. http://strata-host:8080
if MODE == "snapshot" and not ENGINE_URL:
    raise SystemExit("CTX_MODE=snapshot needs CTX_ENGINE_URL (the Strata server, e.g. http://strata-host:8080)")
ENGINE = StrataAdapter(ENGINE_URL or "http://127.0.0.1:8080")
PREFILL_TOK_PER_S = float(os.environ.get("CTX_PREFILL_TOK_PER_S", "500"))   # measured cold read speed, for costs
REORDER = os.environ.get("CTX_REORDER", "0") == "1"           # move volatile system-prompt lines (canon.py)
# regexes (one per line, multiline mode) for system-prompt lines that change between sessions; default: Hermes' date line
def _patterns(raw: str) -> list[str] | None:
    """CTX_VOLATILE_PATTERNS: a JSON list of regexes, or one regex per line. Empty = the default (Hermes' date line)."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("["):
        return [str(p) for p in json.loads(raw)] or None
    return [l for l in raw.splitlines() if l.strip()] or None


VOLATILE_PATTERNS = _patterns(os.environ.get("CTX_VOLATILE_PATTERNS", ""))
PIN_SYSTEM = os.environ.get("CTX_PIN_SYSTEM", "0") == "1"     # pin system + tools in Strata (strata_prefix)
PIN_MIN_TOKENS = int(os.environ.get("CTX_PIN_MIN_TOKENS", "2048"))
RECORD = os.environ.get("CTX_RECORD", "")                       # benchmark: append every request (as sent) here
PIN_WINDOW_H = float(os.environ.get("CTX_PIN_WINDOW_H", "6"))   # pin the most requested head over this window

pool: asyncpg.Pool | None = None
engine_lock = asyncio.Lock()                                   # one save/restore at a time
served: dict[tuple, collections.deque] = {}                    # engine epoch -> chains it served since


@asynccontextmanager
async def lifespan(app):
    global pool
    pool = await asyncpg.create_pool(os.environ["CTX_DATABASE_URL"], min_size=1, max_size=8)
    yield
    await pool.close()


app = FastAPI(title="strata-context-manager", lifespan=lifespan)


class Observe(BaseModel):
    call_id: str
    model: str
    messages: list[dict]
    tools: list | None = None


class Result(BaseModel):
    call_id: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None


def _served_for(epoch) -> collections.deque:
    return served.setdefault(epoch, collections.deque(maxlen=6))


async def _snapshots(c, hashes: list[str]) -> dict[str, int]:
    rows = await c.fetch("""SELECT chain_hash, token_count FROM kv_snapshot
                            WHERE chain_hash = ANY($1::text[]) AND NOT stale""", hashes)
    return {r["chain_hash"]: r["token_count"] for r in rows}


@app.get("/health")
async def health():
    async with pool.acquire() as c:
        await c.fetchval("SELECT 1")
    return {"status": "ok", "mode": MODE, "engine_epoch": await ENGINE.epoch() if MODE == "snapshot" else None}


@app.post("/v1/observe")
async def observe(o: Observe):
    moved = []
    if RECORD:                                                  # the request exactly as the client sent it, for replays
        with open(RECORD, "a") as f:
            f.write(json.dumps({"t": time.time(), "call_id": o.call_id, "model": o.model, "messages": o.messages,
                                "tools": o.tools}) + "\n")
    if REORDER:
        o.messages, moved = move_volatile_lines(o.messages, VOLATILE_PATTERNS)
    blocks = request_blocks(o.messages, o.tools)
    hashes = chain(VALIDITY, blocks)
    offsets, total = [], 0
    for b in blocks:
        offsets.append(total)
        total += b.token_estimate
    offsets.append(total)
    volatile = sorted({x for b in blocks for x in b.volatile})
    # tier 0 head: the system message (Qwen templates render the tools inside it) and the tool definitions
    n_head = sum(1 for b in blocks if b.kind in ("system", "tools"))
    head = hashes[n_head - 1] if n_head else None
    async with pool.acquire() as c, c.transaction():
        known = await c.fetch("SELECT chain_hash, depth FROM chain_node WHERE chain_hash = ANY($1::text[])", hashes)
        matched = max((r["depth"] for r in known), default=0)
        for b in blocks:
            await c.execute(
                """INSERT INTO context_block (block_hash, kind, source_path, stability_tier, version, content,
                                              token_estimate, volatile)
                   VALUES ($1, $2, $3, $4,
                           COALESCE((SELECT max(version) FROM context_block WHERE source_path = $3), 0) + 1,
                           $5, $6, $7)
                   ON CONFLICT (block_hash) DO UPDATE SET last_seen = now(),
                       seen_count = context_block.seen_count + 1""",
                b.hash, b.kind, b.source_path, b.tier, b.content, b.token_estimate, b.volatile)
        root = VALIDITY.root()
        for i, (b, h) in enumerate(zip(blocks, hashes)):
            await c.execute(
                """INSERT INTO chain_node (chain_hash, parent_hash, block_hash, depth, validity_root, token_offset)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (chain_hash) DO UPDATE SET last_seen = now(), hits = chain_node.hits + 1""",
                h, hashes[i - 1] if i else None, b.hash, i + 1, root, offsets[i])
        rid = await c.fetchval(
            """INSERT INTO request_log (call_id, model, n_blocks, n_messages, chain_head, matched_depth,
                                        matched_tokens, total_tokens_est, volatile, head_chain)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id""",
            o.call_id, o.model, len(blocks), len(o.messages), hashes[-1] if hashes else None, matched,
            offsets[matched], total, volatile, head)
        snaps = await _snapshots(c, hashes) if MODE == "snapshot" else {}

    restored = None
    if MODE == "snapshot" and hashes:
        epoch = await ENGINE.epoch()
        target = policy.choose_restore(hashes, snaps, list(_served_for(epoch)), offsets)
        if target:
            async with engine_lock:
                try:
                    r = await ENGINE.restore(f"{target[:40]}.bin")
                    restored = {"chain": target, "ms": (r.get("timings") or {}).get("restore_ms"),
                                "tokens": r.get("n_restored")}
                    async with pool.acquire() as c:
                        await c.execute("""UPDATE kv_snapshot SET hits = hits + 1, last_used = now()
                                           WHERE chain_hash = $1""", target)
                        await c.execute("UPDATE request_log SET restored_chain = $2, restore_ms = $3 WHERE id = $1",
                                        rid, target, restored["ms"])
                except Exception as e:                      # missing file, engine busy: the request just reads more
                    restored = {"error": repr(e)[:200]}
                    async with pool.acquire() as c:
                        await c.execute("UPDATE kv_snapshot SET stale = true WHERE chain_hash = $1", target)
        _served_for(epoch).append(hashes)
        async with pool.acquire() as c:
            await c.execute("UPDATE request_log SET engine_epoch = $2 WHERE id = $1", rid, str(epoch))
    # tier 0 pin, when the head is big enough to matter and is the most requested one (Strata keeps one pin)
    pin = None
    if (PIN_SYSTEM and head and o.messages and o.messages[0].get("role") == "system"
            and offsets[n_head] >= PIN_MIN_TOKENS):
        async with pool.acquire() as c:
            rows = await c.fetch("""SELECT head_chain, count(*) AS n FROM request_log
                                    WHERE at > now() - make_interval(secs => $1) AND head_chain IS NOT NULL
                                    GROUP BY head_chain""", PIN_WINDOW_H * 3600)
            if policy.pin_allowed(head, {r["head_chain"]: r["n"] for r in rows}):
                pin = {"messages": 1}
            await c.execute("UPDATE request_log SET pinned = $2 WHERE id = $1", rid, pin is not None)
    return {"mode": MODE, "n_blocks": len(blocks), "matched_depth": matched, "matched_tokens_est": offsets[matched],
            "pin": pin,
            "total_tokens_est": total, "volatile": volatile, "restored": restored,
            "messages": o.messages if moved else None, "moved_lines": moved}


async def _maybe_save(call_id: str, prompt_tokens: int | None, completion_tokens: int | None):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT id, chain_head FROM request_log WHERE call_id = $1 ORDER BY id DESC LIMIT 1",
                               call_id)
        if not row or not row["chain_head"]:
            return
        path = await c.fetch("""WITH RECURSIVE up AS (
                                  SELECT chain_hash, parent_hash, depth FROM chain_node WHERE chain_hash = $1
                                  UNION ALL
                                  SELECT n.chain_hash, n.parent_hash, n.depth FROM chain_node n
                                  JOIN up ON n.chain_hash = up.parent_hash)
                                SELECT chain_hash FROM up ORDER BY depth""", row["chain_head"])
        hashes = [r["chain_hash"] for r in path]
        snaps = await _snapshots(c, hashes)
    if not policy.should_save(prompt_tokens, hashes, snaps):
        return
    head = row["chain_head"]
    async with engine_lock:
        try:
            r = await ENGINE.save(f"{head[:40]}.bin")
        except Exception:
            return
    n_saved, ms = r.get("n_saved"), (r.get("timings") or {}).get("save_ms")
    ok = policy.save_matches(n_saved, prompt_tokens, completion_tokens)
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO kv_snapshot (chain_hash, model_id, weights_hash, quant, engine_version, template_version,
                                        token_count, saved_tokens, tier, location, size_bytes, stale, recompute_ms,
                                        last_used)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'nvme', $9, $10, $11, $12, now())
               ON CONFLICT (chain_hash) DO UPDATE SET token_count = $7, saved_tokens = $8, size_bytes = $10,
                   stale = $11, last_used = now(), created_at = now()""",
            head, VALIDITY.model_id, VALIDITY.weights_hash, VALIDITY.quant, VALIDITY.engine_version,
            VALIDITY.template_version, prompt_tokens, n_saved, f"{head[:40]}.bin", r.get("n_written"), not ok,
            prompt_tokens / PREFILL_TOK_PER_S * 1000.0)
        await c.execute("UPDATE request_log SET saved_chain = $2, save_ms = $3 WHERE id = $1",
                        row["id"], head if ok else None, ms)


@app.post("/v1/result")
async def result(r: Result, bg: BackgroundTasks):
    async with pool.acquire() as c:
        await c.execute(
            """UPDATE request_log SET prompt_tokens = $2, cached_tokens = $3, latency_ms = $4, ttft_ms = $5,
                                      completion_tokens = $6
               WHERE id = (SELECT id FROM request_log WHERE call_id = $1 ORDER BY id DESC LIMIT 1)""",
            r.call_id, r.prompt_tokens, r.cached_tokens, r.latency_ms, r.ttft_ms, r.completion_tokens)
    if MODE == "snapshot":
        bg.add_task(_maybe_save, r.call_id, r.prompt_tokens, r.completion_tokens)
    return {"ok": True}


@app.get("/v1/stats")
async def stats():
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """SELECT count(*) AS requests,
                      sum(matched_tokens)::float / NULLIF(sum(total_tokens_est), 0) AS predicted_hit,
                      sum(cached_tokens)::float / NULLIF(sum(prompt_tokens), 0) AS engine_hit,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p50,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p95,
                      count(restored_chain) AS restores, count(saved_chain) AS saves
               FROM request_log""")
        snaps = await c.fetchrow("""SELECT count(*) AS n, coalesce(sum(size_bytes), 0) AS bytes FROM kv_snapshot
                                    WHERE NOT stale""")
        blocks = await c.fetchval("SELECT count(*) FROM context_block")
    return {**dict(row), "blocks": blocks, "snapshots": snaps["n"], "snapshot_bytes": snaps["bytes"], "mode": MODE}


# ---- eviction (called by the NVMe janitor on the engine host) ----------------------------------------------------
ADMIN_TOKEN = os.environ.get("CTX_ADMIN_TOKEN", "")


def _authorized(token: str | None) -> bool:
    return bool(ADMIN_TOKEN) and token == ADMIN_TOKEN


class Evicted(BaseModel):
    locations: list[str]


@app.get("/v1/snapshots/eviction-order")
async def eviction_order(x_ctx_token: str | None = Header(default=None)):
    """Snapshot files, lowest value first (P(reuse) x recompute cost / size); pinned ones are left out."""
    if not _authorized(x_ctx_token):
        raise HTTPException(status_code=403, detail="bad token")
    async with pool.acquire() as c:
        rows = await c.fetch("""SELECT location, size_bytes, hits, pinned, stale, recompute_ms,
                                       extract(epoch FROM now() - coalesce(last_used, created_at)) / 3600 AS age_h
                                FROM kv_snapshot""")
    order = []
    for r in rows:
        v = 0.0 if r["stale"] else policy.snapshot_value(r["hits"], float(r["age_h"] or 0), r["recompute_ms"] or 0,
                                                         r["size_bytes"] or 1, r["pinned"])
        if v != float("inf"):
            order.append({"location": r["location"], "size_bytes": r["size_bytes"], "value": v})
    order.sort(key=lambda x: x["value"])
    return {"order": order}


@app.post("/v1/snapshots/evicted")
async def evicted(e: Evicted, x_ctx_token: str | None = Header(default=None)):
    if not _authorized(x_ctx_token):
        raise HTTPException(status_code=403, detail="bad token")
    async with pool.acquire() as c:
        n = await c.execute("DELETE FROM kv_snapshot WHERE location = ANY($1::text[])", e.locations)
    return {"deleted": n}
