"""Runs inside the Hermes container: summarises one task's sessions from Hermes' own state.db (read-only).
Usage: python3 hermes_side.py "<first words of the task prompt>"   -> one JSON object on stdout."""
import collections
import json
import re
import sqlite3
import sys

DB = "file:/home/hermes/.hermes/state.db?mode=ro"
marker = sys.argv[1] if len(sys.argv) > 1 else "You are working on"
c = sqlite3.connect(DB, uri=True)
c.row_factory = sqlite3.Row

first = c.execute(
    "select m.session_id sid from messages m where m.role='user' and m.content like ? order by m.id limit 1",
    (marker + "%",)).fetchone()
if not first:
    print(json.dumps({"error": "task session not found"}))
    sys.exit(0)

ids, todo = [], [first["sid"]]
while todo:                       # the task's session plus the children a compaction creates
    s = todo.pop()
    ids.append(s)
    todo += [r["id"] for r in c.execute("select id from sessions where parent_session_id=?", (s,))]
q = ",".join("?" * len(ids))
sess = c.execute(f"select * from sessions where id in ({q}) order by started_at", ids).fetchall()
out = {
    "sessions": len(ids),
    "compactions": len(ids) - 1,
    "started_at": min(s["started_at"] for s in sess),
    "last_activity": max((s["last_activity_at"] or s["started_at"]) for s in sess),
    "ended": all(s["ended_at"] for s in sess),
    "end_reason": sess[-1]["end_reason"],
    "api_calls": sum(s["api_call_count"] or 0 for s in sess),
    "input_tokens": sum(s["input_tokens"] or 0 for s in sess),
    "output_tokens": sum(s["output_tokens"] or 0 for s in sess),
    "cache_read_tokens": sum(s["cache_read_tokens"] or 0 for s in sess),
    "reasoning_tokens": sum(s["reasoning_tokens"] or 0 for s in sess),
}

rows = c.execute(f"select * from messages where session_id in ({q}) order by id", ids).fetchall()
# Hermes' store can hold a message twice (a block of early rows is written again later): keep the first, order by time
seen, msgs = set(), []
for m in rows:
    k = (m["timestamp"], m["role"], m["tool_call_id"])
    if k not in seen:
        seen.add(k)
        msgs.append(m)
msgs.sort(key=lambda m: (m["timestamp"] or 0, m["id"]))
out["messages"] = len(msgs)
out["rows_in_store"] = len(rows)
out["hermes_counters"] = {"messages": sum(s["message_count"] or 0 for s in sess), "tool_calls": sum(s["tool_call_count"] or 0 for s in sess)}
out["finish_reasons"] = dict(collections.Counter(m["finish_reason"] for m in msgs if m["role"] == "assistant"))

calls, sig_of_call, results = [], {}, {}
for m in msgs:
    if m["role"] == "assistant" and m["tool_calls"]:
        try:
            tcs = json.loads(m["tool_calls"])
        except ValueError:
            continue
        for tc in tcs:
            fn = tc.get("function", {})
            try:
                a = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                a = {"raw": fn.get("arguments")}
            body = a.get("command") if isinstance(a, dict) and "command" in a else json.dumps(a, sort_keys=True)
            sig = fn.get("name", "?") + "|" + re.sub(r"\s+", " ", str(body)).strip()[:300]
            calls.append((m["timestamp"], fn.get("name", "?"), sig))
            sig_of_call[tc.get("id")] = sig
    elif m["role"] == "tool":
        try:
            d = json.loads(m["content"])
        except (ValueError, TypeError):
            d = {}
        bad = False
        if isinstance(d, dict):
            ec = d.get("exit_code")
            bad = (isinstance(ec, int) and ec != 0) or bool(d.get("error")) or "timed out" in str(d.get("output", ""))[:300].lower()
        results[m["tool_call_id"]] = bad

out["tool_calls"] = len(calls)
out["tools"] = dict(collections.Counter(n for _, n, _ in calls).most_common())
failed = [(sig_of_call.get(i), b) for i, b in results.items() if b]
out["tool_failures"] = len(failed)

# loops: the same call back to back, the same call many times, the same failing call again and again
run = best = 1
best_sig = None
for i in range(1, len(calls)):
    run = run + 1 if calls[i][2] == calls[i - 1][2] else 1
    if run > best:
        best, best_sig = run, calls[i][2]
out["max_same_call_in_a_row"] = best if calls else 0
out["max_same_call_in_a_row_what"] = (best_sig or "")[:140]
cnt = collections.Counter(s for _, _, s in calls)
out["calls_repeated_3plus"] = [[n, s[:120]] for s, n in cnt.most_common(8) if n >= 3]
fcnt = collections.Counter(s for s, _ in failed if s)
out["failing_calls_repeated_2plus"] = [[n, s[:120]] for s, n in fcnt.most_common(5) if n >= 2]
files = collections.Counter()
for _, name, sig in calls:
    if name == "terminal" and re.search(r"\b(cat|sed -n|head|tail|nl|less)\b", sig):
        for p in re.findall(r"[\w./-]+\.(?:rs|toml|md|ron|wgsl|py|json)", sig):
            files[p] += 1
out["files_read_5plus_times"] = [[n, p] for p, n in files.most_common(8) if n >= 5]

ts = [m["timestamp"] for m in msgs if m["timestamp"]]
gaps = sorted(((b - a) for a, b in zip(ts, ts[1:])), reverse=True)
out["wall_s"] = round(ts[-1] - ts[0]) if ts else 0
out["gaps_over_120s"] = sum(1 for g in gaps if g > 120)
out["longest_gap_s"] = round(gaps[0]) if gaps else 0
out["empty_assistant_turns"] = sum(1 for m in msgs if m["role"] == "assistant" and not (m["content"] or "").strip() and not m["tool_calls"])
print(json.dumps(out))
