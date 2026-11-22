"""File state cache, gateway side (F4): find the file reads in a request and name each one by its exact content.

Strata (research build, f1-file-state) takes `strata_segx: {"files": [{"message": I, "start": c0, "end": c1, "id": ID}]}`:
for each file it loads the stored state when its store holds these exact tokens under ID, else it reads the file normally
and stores it under ID.  So the gateway needs no record of what is stored: a wrong guess only costs a normal read.

The id is a hash of the validity root (model, weights, quantisation, engine, template) and the exact tool-result text, so
a changed file, another model or another engine version never meets an old state.

Besides file tools (`read_file`) it can recognise a read made through a shell tool (`terminal`, `bash`): a command that does
nothing but print a file (`cat`, or a slice with `sed -n` / `head` / `tail`).  This is opt-in (`terminal` modes) and strict: a
command that writes, pipes elsewhere, builds or runs anything else is never taken for a read.
"""
from __future__ import annotations

import hashlib
import json
import re

DEFAULT_TOOLS = ("read_file",)
TERMINAL_MODES = ("cat", "slice")

_FILE = r"[\w./~$@+-]+\.[A-Za-z0-9]{1,6}"
_RANGE = r"['\"]?\d+(?:,(?:\d+|\$))?p['\"]?"
_COUNT = r"(?:-n ?\+?\d+|-\d+)"
_PART_NEUTRAL = re.compile(r"^(?:cd [\w./~$@+-]+|echo (?:\"[^\"$`\\]*\"|'[^']*'|[\w=.:/ -]+))$")
_PART_CAT = re.compile(rf"^cat {_FILE}(?: {_FILE}){{0,2}}$")
_PART_SLICE = (
    re.compile(rf"^sed -n {_RANGE} {_FILE}$"),
    re.compile(rf"^head {_COUNT} {_FILE}$"),
    re.compile(rf"^tail {_COUNT} {_FILE}$"),
    re.compile(rf"^(?:nl(?: -\w+)* {_FILE}|cat {_FILE}) \| sed -n ['\"]?\d+,\d+p['\"]?$"),
    re.compile(rf"^cat {_FILE} \| (?:head|tail) {_COUNT}$"),
)


def terminal_read_kind(command: str) -> str | None:
    """'cat' or 'slice' when `command` only prints files (optionally after cd / echo separators); None for anything else.

    Strict on purpose: every `&&` / `;` part must be one of the known read shapes, so a write, a build, a search, a pipe
    into another tool or a redirection makes the whole command not a read."""
    if not isinstance(command, str) or not command.strip() or len(command) > 2000:
        return None
    kind = None
    for part in re.split(r"\s*(?:&&|;)\s*", re.sub(r"\s+", " ", command.strip())):
        if not part or _PART_NEUTRAL.match(part):
            continue
        if _PART_CAT.match(part):
            kind = kind or "cat"
        elif any(rx.match(part) for rx in _PART_SLICE):
            kind = "slice"
        else:
            return None
    return kind


def _calls(messages: list[dict]) -> dict[str, tuple[str, str]]:
    """tool_call_id -> (function name, arguments as sent), from the assistant messages' tool_calls."""
    out = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for c in m.get("tool_calls") or []:
            f = (c.get("function") or {}) if isinstance(c, dict) else {}
            if c.get("id") and f.get("name"):
                out[c["id"]] = (f["name"], f.get("arguments") or "")
    return out


def _terminal_kind(arguments: str, content: str) -> str | None:
    """Kind of read ('cat' / 'slice') when this shell call was a pure read that succeeded, else None."""
    try:
        cmd = json.loads(arguments).get("command")
        res = json.loads(content)
    except (ValueError, AttributeError):
        return None
    if not isinstance(res, dict) or res.get("exit_code") != 0 or res.get("error"):
        return None
    return terminal_read_kind(cmd)


def file_segments(messages: list[dict], validity_root: str, tools: tuple[str, ...] = DEFAULT_TOOLS,
                  min_chars: int = 4000, terminal_modes: tuple[str, ...] = (), terminal_tools: tuple[str, ...] = ("terminal",),
                  slice_min_chars: int = 3000) -> list[dict]:
    """The file reads worth caching in `messages`: tool results of a file-reading tool, long enough to pay off.

    `terminal_modes` ('cat', 'slice'): also take results of a shell tool in `terminal_tools` whose command was a pure read of
    that kind (a slice must reach `slice_min_chars`, a whole file `min_chars`).  A text that appears twice in one request is
    named once: the second copy is read normally.

    Returns [{"message": index, "start": 0, "end": len(content), "id": id, "chars": n, "tool": name}], in order."""
    calls = _calls(messages)
    out, seen = [], set()
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        name, args = calls.get(m.get("tool_call_id") or "") or (m.get("name"), "")
        label = name
        if name in tools:
            if len(content) < min_chars:
                continue
        elif terminal_modes and name in terminal_tools:
            kind = _terminal_kind(args, content)
            if kind not in terminal_modes or len(content) < (min_chars if kind == "cat" else slice_min_chars):
                continue
            label = f"{name}:{kind}"
        else:
            continue
        ident = hashlib.sha256(f"{validity_root}|seg|{content}".encode()).hexdigest()[:32]
        if ident in seen:
            continue
        seen.add(ident)
        out.append({"message": i, "start": 0, "end": len(content), "id": ident, "chars": len(content), "tool": label})
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


def terminal_modes_from_env(raw: str | None) -> tuple[str, ...]:
    """CTX_SEG_TERMINAL: 'cat', 'slice' or 'cat,slice' (empty = off)."""
    return tuple(x for x in (t.strip() for t in (raw or "").split(",")) if x in TERMINAL_MODES)
