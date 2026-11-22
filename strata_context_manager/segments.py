"""File state cache, gateway side (F4): find the file reads in a request and name each one by its exact content.

Strata (research build, f1-file-state) takes `strata_segx: {"files": [{"message": I, "start": c0, "end": c1, "id": ID}]}`:
for each file it loads the stored state when its store holds these exact tokens under ID, else it reads the file normally
and stores it under ID.  So the gateway needs no record of what is stored: a wrong guess only costs a normal read.

The id is a hash of the validity root (model, weights, quantisation, engine, template) and the exact tool-result text, so
a changed file, another model or another engine version never meets an old state.
"""
from __future__ import annotations

import hashlib
import json

DEFAULT_TOOLS = ("read_file",)


def _calls(messages: list[dict]) -> dict[str, str]:
    """tool_call_id -> function name, from the assistant messages' tool_calls."""
    out = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for c in m.get("tool_calls") or []:
            fn = (c.get("function") or {}).get("name") if isinstance(c, dict) else None
            if c.get("id") and fn:
                out[c["id"]] = fn
    return out


def file_segments(messages: list[dict], validity_root: str, tools: tuple[str, ...] = DEFAULT_TOOLS,
                  min_chars: int = 4000) -> list[dict]:
    """The file reads worth caching in `messages`: tool results of a file-reading tool, long enough to pay off.

    Returns [{"message": index, "start": 0, "end": len(content), "id": id, "chars": n, "tool": name}], in order."""
    calls = _calls(messages)
    out = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) < min_chars:
            continue
        name = calls.get(m.get("tool_call_id") or "") or m.get("name")
        if name not in tools:
            continue
        ident = hashlib.sha256(f"{validity_root}|seg|{content}".encode()).hexdigest()[:32]
        out.append({"message": i, "start": 0, "end": len(content), "id": ident, "chars": len(content), "tool": name})
    return out


def engine_spec(segs: list[dict]) -> dict:
    """What the hook sends to Strata as `strata_segx`."""
    return {"files": [{k: s[k] for k in ("message", "start", "end", "id")} for s in segs]}


def tools_from_env(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_TOOLS
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return tuple(str(x) for x in v)
    except ValueError:
        pass
    return tuple(x.strip() for x in raw.split(",") if x.strip())
