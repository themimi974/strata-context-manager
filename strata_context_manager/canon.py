"""Canonicalisation: make the stable part of a prompt byte-identical across sessions and days.

Rule 4 of the plan: volatile content goes last. Hermes writes a line such as
  "Conversation started: Saturday, November 21, 2026 (CET, UTC+01:00)"
into its system prompt, before ~8.5K tokens of tool definitions, so every new day re-reads them. We move such lines
out of the system message into the start of the first user message: the model still sees them, the system + tools
prefix stays identical, and the move is deterministic, so every turn of a conversation renders the same bytes.
"""
from __future__ import annotations

import copy
import re

DEFAULT_VOLATILE_LINES = [r"^Conversation started: .*$"]
MOVED_HEADER = ""           # moved lines are inserted as-is, followed by one blank line


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.M) for p in patterns]


def move_volatile_lines(messages: list[dict], patterns: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Return (new messages, moved lines). The input is never modified; no match -> the same list back."""
    if not messages or messages[0].get("role") != "system" or not isinstance(messages[0].get("content"), str):
        return messages, []
    first_user = next((i for i, m in enumerate(messages) if m.get("role") == "user"), None)
    if first_user is None:
        return messages, []
    pats = _compile(patterns or DEFAULT_VOLATILE_LINES)
    sys_text = messages[0]["content"]
    moved = []
    for p in pats:
        moved += [m.group(0) for m in p.finditer(sys_text)]
    if not moved:
        return messages, []
    new_sys = sys_text
    for line in moved:
        new_sys = new_sys.replace(line + "\n", "", 1) if (line + "\n") in new_sys else new_sys.replace(line, "", 1)
    out = copy.deepcopy(messages)
    out[0]["content"] = new_sys
    prefix = "\n".join(moved) + "\n\n"
    content = out[first_user].get("content")
    if isinstance(content, str) or content is None:
        out[first_user]["content"] = prefix + (content or "")
    else:                                   # list of parts: prepend a text part
        out[first_user]["content"] = [{"type": "text", "text": prefix}] + list(content)
    return out, moved
