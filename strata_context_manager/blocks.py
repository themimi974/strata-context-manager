"""Turn an OpenAI chat request into content-addressed blocks and a chain of prefix hashes.

Rules (from the project plan):
- exact matching only: a block is identified by the sha256 of its kind and its exact bytes;
- content-defined chunking: long texts are cut at paragraph breaks chosen by their own content,
  so an edit in one place does not move the boundaries elsewhere;
- the chain hash of block i covers the validity tuple and every block before it:
  h0 = H(validity tuple), h_i = H(h_{i-1} | block_hash_i).

Passthrough mode never changes the request; it only describes it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

# stability tiers: lower = changes less often = should come first
TIER_SYSTEM, TIER_TOOLS, TIER_DOCS, TIER_FILES, TIER_CONVERSATION = 0, 1, 2, 3, 4

CDC_MIN_CHARS = 1024      # never cut a chunk shorter than this
CDC_MAX_CHARS = 16384     # always cut once a chunk reaches this
CDC_MASK = 0x7            # cut after a paragraph whose hash ends in these bits being 0 (~1 in 8)

VOLATILE_PATTERNS = [
    re.compile(r"\b20\d\d-\d\d-\d\d\b"),                                   # ISO date
    re.compile(r"\b\d{1,2}:\d\d(:\d\d)?\b"),                                # clock time
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),  # UUID
    re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,? \w+ \d{1,2}", re.I),           # weekday dates
]


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Validity:
    """Everything a cached state depends on. A change in any field invalidates every chain."""
    model_id: str
    weights_hash: str
    quant: str
    engine_version: str
    template_version: str

    def root(self) -> str:
        return sha256("ctxcache-v1|" + "|".join(
            [self.model_id, self.weights_hash, self.quant, self.engine_version, self.template_version]))


@dataclass
class Block:
    kind: str                 # system | tools | user | assistant | tool
    tier: int
    content: str              # exact text this block contributes (or canonical JSON for tools)
    msg_index: int            # which message it came from (-1 for tools)
    part: int = 0             # chunk number inside that message
    source_path: str | None = None
    volatile: list[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        return sha256(self.kind + "\0" + self.content)

    @property
    def token_estimate(self) -> int:
        return max(1, round(len(self.content) / 3.5))


def cdc_split(text: str) -> list[str]:
    """Deterministic content-defined chunking on paragraph boundaries.

    The concatenation of the returned chunks is exactly `text`."""
    if len(text) <= CDC_MIN_CHARS:
        return [text]
    paras = re.split(r"(?<=\n\n)", text)          # keep the separator with the paragraph before it
    chunks, cur = [], ""
    for p in paras:
        cur += p
        cut_here = len(cur) >= CDC_MIN_CHARS and (int(sha256(p)[:8], 16) & CDC_MASK) == 0
        while len(cur) >= CDC_MAX_CHARS:           # a huge paragraph: hard cuts at the max size
            chunks.append(cur[:CDC_MAX_CHARS])
            cur = cur[CDC_MAX_CHARS:]
            cut_here = False
        if cut_here:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def _text_of(content) -> str:
    """OpenAI content is a string or a list of parts; keep text parts, mark images by their URL hash."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    out = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            out.append(part.get("text", ""))
        elif isinstance(part, dict) and part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            out.append(f"<image sha256={sha256(url)}>")
        else:
            out.append(json.dumps(part, sort_keys=True))
    return "\n".join(out)


_PATH_HINT = re.compile(r"(?:^|\s)((?:/|\./|~/)[\w./-]+\.\w{1,8})")


def _tool_source_path(text: str) -> str | None:
    m = _PATH_HINT.search(text[:400])
    return m.group(1) if m else None


def _volatile(text: str) -> list[str]:
    hits = []
    for pat in VOLATILE_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    return hits


def request_blocks(messages: list[dict], tools: list | None = None) -> list[Block]:
    """Blocks in the order the request renders them (Qwen templates put tools in the system turn)."""
    blocks: list[Block] = []
    first_system = 0 if messages and messages[0].get("role") == "system" else None

    def add_message(i: int, m: dict):
        role = m.get("role", "user")
        text = _text_of(m.get("content"))
        extra = {}
        if m.get("tool_calls"):
            extra["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            extra["tool_call_id"] = m["tool_call_id"]
        if m.get("name"):
            extra["name"] = m["name"]
        if extra:                                   # tool-call metadata is rendered too, so it is part of the bytes
            text = text + "\n\x1e" + json.dumps(extra, sort_keys=True, ensure_ascii=False)   # 0x1E: Postgres text cannot hold NUL
        tier = {"system": TIER_SYSTEM, "tool": TIER_CONVERSATION}.get(role, TIER_CONVERSATION)
        src = _tool_source_path(text) if role == "tool" else None
        if role == "tool" and src:
            tier = TIER_FILES
        for k, chunk in enumerate(cdc_split(text)):
            blocks.append(Block(kind=role, tier=tier, content=chunk, msg_index=i, part=k,
                                source_path=src, volatile=_volatile(chunk) if role == "system" else []))

    if first_system is not None:
        add_message(0, messages[0])
    if tools:
        blocks.append(Block(kind="tools", tier=TIER_TOOLS,
                            content=json.dumps(tools, sort_keys=True, ensure_ascii=False), msg_index=-1))
    for i, m in enumerate(messages):
        if i == first_system:
            continue
        add_message(i, m)
    return blocks


def chain(validity: Validity, blocks: list[Block]) -> list[str]:
    """chain[i] identifies the prefix made of blocks[0..i] under this validity tuple."""
    h = validity.root()
    out = []
    for b in blocks:
        h = sha256(h + "|" + b.hash)
        out.append(h)
    return out
