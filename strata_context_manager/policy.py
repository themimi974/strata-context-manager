"""Snapshot decisions, kept pure so they can be tested without an engine or a database."""
from __future__ import annotations

import os

SAVE_MIN_TOKENS = int(os.environ.get("CTX_SAVE_MIN_TOKENS", "8192"))    # shorter conversations are cheap to re-read
SAVE_STEP_TOKENS = int(os.environ.get("CTX_SAVE_STEP_TOKENS", "8192"))  # save again after this much growth
RESTORE_MIN_GAIN = int(os.environ.get("CTX_RESTORE_MIN_GAIN", "2048"))  # restore only if it saves this much reading


def lcp(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def engine_depth(request_chain: list[str], served: list[list[str]]) -> int:
    """Blocks of this request the engine most likely still holds: the longest common prefix with what it served
    since its last restart (it keeps recent conversations parked or checkpointed)."""
    return max((lcp(request_chain, c) for c in served), default=0)


def choose_restore(request_chain: list[str], snapshots: dict[str, int], served: list[list[str]],
                   offsets: list[int]) -> str | None:
    """The chain hash of the snapshot to restore before this request, or None.

    snapshots: chain_hash -> tokens it holds; offsets[i]: estimated tokens before block i (offsets[len] = total)."""
    best = None
    for i in range(len(request_chain) - 1, -1, -1):
        if request_chain[i] in snapshots:
            best = i
            break
    if best is None:
        return None
    have = engine_depth(request_chain, served)
    if have >= best + 1:
        return None
    gain = offsets[best + 1] - offsets[have]
    return request_chain[best] if gain >= RESTORE_MIN_GAIN else None


def should_save(prompt_tokens: int | None, request_chain: list[str], snapshots: dict[str, int]) -> bool:
    if not prompt_tokens or prompt_tokens < SAVE_MIN_TOKENS:
        return False
    if request_chain and request_chain[-1] in snapshots:
        return False
    last = max((snapshots[h] for h in request_chain if h in snapshots), default=0)
    return prompt_tokens - last >= SAVE_STEP_TOKENS


def snapshot_value(hits: int, age_hours: float, recompute_ms: float, size_bytes: int, pinned: bool) -> float:
    """Eviction value: P(reuse) x recompute cost / size. Higher = keep. Pinned snapshots are never evicted.

    P(reuse) grows with past hits (Laplace estimate) and decays with a one-day half-life since last use."""
    if pinned:
        return float("inf")
    p_reuse = (hits + 1) / (hits + 2) * 0.5 ** (max(age_hours, 0.0) / 24.0)
    return p_reuse * max(recompute_ms, 1.0) / max(size_bytes, 1)


def save_matches(n_saved: int | None, prompt_tokens: int, completion_tokens: int | None, slack: int = 64) -> bool:
    """The engine saved the conversation we think it did (another request may have run in between)."""
    if n_saved is None:
        return False
    expected = prompt_tokens + (completion_tokens or 0)
    return prompt_tokens <= n_saved <= expected + slack


def pin_allowed(head: str | None, head_counts: dict[str, int]) -> bool:
    """The engine keeps one pinned prefix and the last request that asks wins it, so only the most requested
    system + tools head (over a recent window, this request included) may ask. Ties may all ask."""
    if not head:
        return False
    top = max(head_counts.values(), default=0)
    return head_counts.get(head, 0) >= top > 0
