#!/usr/bin/env python3
"""Study of how an agent reads files, from a gateway request recording (JSONL of requests as sent).
For every file read (sed -n A,Bp, head, tail, cat, read_file) it asks:
  * is it new, already in the context window (redundant), or content that was read earlier and had been dropped (re-read)?
  * how many model turns and how many minutes went into reading?
Usage: partial_reads.py RECORDING.jsonl [--lengths-from "cmd that prints 'N path' lines for the files given as args"]"""
import collections
import json
import re
import statistics
import subprocess
import sys

REC = sys.argv[1]
REPO = "/home/hermes/paraworld-remake"
TOK = 3.0                                    # characters per token, rough, for code
PAUSE_S = 600                                # a gap longer than this between two requests is a pause, not work

recs = [json.loads(l) for l in open(REC)]
main = [r for r in recs if r.get("tools")]   # the agent's own requests (auxiliary calls carry no tools)
print(f"requests in the recording: {len(recs)}, main-agent requests: {len(main)}")

FILE = r"[\w./~$@+-]+\.(?:rs|toml|md|ron|wgsl|py|json|lock|txt|sh|yml|yaml|cu|cpp|hpp|glb|gltf)"


def norm(p):
    p = p.strip("'\"")
    if p.startswith("~/"):
        p = "/home/hermes/" + p[2:]
    p = re.sub(r"^\./", "", p)
    if not p.startswith("/"):
        p = REPO + "/" + p
    return p


def reads_in(name, args):
    """-> list of (kind, file, a, b); b None = to the end of the file; kind 'whole' or 'part'"""
    out = []
    if name == "read_file":
        p = args.get("path")
        if p:
            a, b = args.get("offset"), args.get("limit")
            out.append(("whole", norm(p), 1, None) if not (a or b) else ("part", norm(p), int(a or 1), (int(a or 1) + int(b) - 1) if b else None))
        return out
    if name != "terminal":
        return out
    cmd = re.sub(r"\s+", " ", str(args.get("command", "")))
    if re.search(r"cat\s*>|<<\s*['\"]?EOF|tee ", cmd):
        return out                                       # writes a file, not a read
    for m in re.finditer(r"sed -n ['\"]?(\d+)(?:,(\d+|\$))?p['\"]? (" + FILE + ")", cmd):
        a = int(m[1]); b = int(m[2]) if m[2] and m[2] != "$" else (a if not m[2] else None)
        out.append(("part", norm(m[3]), a, b))
    for m in re.finditer(r"(?:nl(?: -\w+)* (" + FILE + r")|cat (" + FILE + r")) \| sed -n ['\"]?(\d+),(\d+)p", cmd):
        out.append(("part", norm(m[1] or m[2]), int(m[3]), int(m[4])))
    for m in re.finditer(r"head -n? ?(\d+) (" + FILE + ")", cmd):
        out.append(("part", norm(m[2]), 1, int(m[1])))
    for m in re.finditer(r"cat (" + FILE + r") \| head -n? ?(\d+)", cmd):
        out.append(("part", norm(m[1]), 1, int(m[2])))
    for m in re.finditer(r"tail -n? ?\+?(\d+) (" + FILE + ")", cmd):
        out.append(("tail", norm(m[2]), int(m[1]), None))
    have = {(k, f) for k, f, _, _ in out}
    for m in re.finditer(r"(?:^|&& |; |\| xargs )cat ((?:" + FILE + r" ?)+)(?= ?(?:&&|;|$|2>))", cmd):
        for f in m[1].split():
            if ("part", norm(f)) not in have:
                out.append(("whole", norm(f), 1, None))
    return out


# ---- pass 1: every distinct call, in order of first appearance, with its result size, request index, edits
calls, order, result_chars = {}, [], {}
for ri, r in enumerate(main):
    for m in r["messages"]:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                if tc["id"] not in calls:
                    try:
                        a = json.loads(tc["function"].get("arguments") or "{}")
                    except ValueError:
                        a = {}
                    calls[tc["id"]] = {"ri": ri, "name": tc["function"]["name"], "args": a}
                    order.append(tc["id"])
        elif m.get("role") == "tool" and m.get("tool_call_id") not in result_chars:
            c = m.get("content")
            result_chars[m["tool_call_id"]] = len(c if isinstance(c, str) else json.dumps(c))

# file lengths from the machine that has the files (current state: approximate for files the agent edited)
files = sorted({f for c in calls.values() for _, f, _, _ in reads_in(c["name"], c["args"])})
lengths = {}
try:
    out = subprocess.run(["pct", "exec", "104", "--", "bash", "-c", "wc -l " + " ".join(f"'{f}'" for f in files) + " 2>/dev/null"],
                         capture_output=True, text=True, timeout=60).stdout
    for line in out.splitlines():
        m = re.match(r"\s*(\d+) (/.+)$", line)
        if m and m[2] != "total":
            lengths[m[2]] = int(m[1])
except Exception as e:
    print("could not read file lengths:", e)

edited_at = []                                   # (call order index, file)
for cid in order:
    c = calls[cid]
    if c["name"] in ("patch", "write_file"):
        p = c["args"].get("path") or c["args"].get("file") or c["args"].get("file_path")
        if p:
            edited_at.append((c["ri"], norm(p)))
edited_files = {f for _, f in edited_at}


def version(file, ri):                           # how many edits of this file were made before request ri
    return sum(1 for r_, f in edited_at if f == file and r_ < ri)


# ---- pass 2: resolve each read into a set of lines and classify them
segs = {}                                        # call id -> list of (file, version, lines set, kind)
for cid in order:
    c = calls[cid]
    s = []
    for kind, f, a, b in reads_in(c["name"], c["args"]):
        L = lengths.get(f)
        if kind == "tail":
            if not L:
                continue
            a, b = max(1, L - a + 1), L           # tail -n +N / -n N: approximated as the last N lines
        if b is None:
            b = L if L else a + 200
        if b < a:
            continue
        s.append((f, version(f, c["ri"]), set(range(a, b + 1)), kind))
    if s:
        segs[cid] = s

new_ch = in_ctx_ch = reread_ch = 0.0
per_cls = collections.defaultdict(lambda: [0.0, 0.0, 0.0])      # edited / read-only -> new, in context, re-read (chars)
per_file = collections.defaultdict(lambda: {"reads": 0, "lines_read": 0, "distinct": set(), "chars": 0})
seen_ids = []
slice_lines = []
for cid in order:
    c = calls[cid]
    if cid not in segs:
        seen_ids.append(cid)
        continue
    j = c["ri"]
    # context window at the time of the call = the previous main request's calls
    ctx_ids = set()
    if j > 0:
        for m in main[j - 1]["messages"]:
            if m.get("role") == "assistant":
                for tc in (m.get("tool_calls") or []):
                    ctx_ids.add(tc["id"])
    total_lines = sum(len(x[2]) for x in segs[cid])
    ch = result_chars.get(cid, 0)
    for f, v, lines, kind in segs[cid]:
        share = ch * len(lines) / max(total_lines, 1)
        in_ctx = set(); prior = set()
        for pid in seen_ids:
            if pid in segs:
                for pf, pv, pl, _ in segs[pid]:
                    if pf == f and pv == v:
                        (in_ctx if pid in ctx_ids else prior).update(pl)
        n_in = len(lines & in_ctx); n_re = len((lines - in_ctx) & prior); n_new = len(lines) - n_in - n_re
        cls = "edited" if f in edited_files else "read-only"
        for key, n in (("new", n_new), ("ctx", n_in), ("re", n_re)):
            ch_part = share * n / max(len(lines), 1)
            per_cls[cls][("new", "ctx", "re").index(key)] += ch_part
        pf_ = per_file[f]
        pf_["reads"] += 1; pf_["lines_read"] += len(lines); pf_["distinct"] |= lines; pf_["chars"] += share
        if kind == "part":
            slice_lines.append(len(lines))
    seen_ids.append(cid)

print("\n== 1. what is read, and how much of it is repeated (output chars; ~3 chars per token)")
print(f"{'':12}{'new':>10}{'repeat in same context':>26}{'re-read after dropped':>24}{'total':>9}{'~tokens':>9}")
tot = [0, 0, 0]
for cls in ("read-only", "edited"):
    n, x, r = per_cls[cls]
    for i, v in enumerate((n, x, r)):
        tot[i] += v
    t = n + x + r
    print(f"{cls:12}{n/1000:9.0f}K{x/1000:25.0f}K{r/1000:23.0f}K{t/1000:8.0f}K{t/TOK/1000:8.0f}K")
T = sum(tot)
print(f"{'all':12}{tot[0]/1000:9.0f}K{tot[1]/1000:25.0f}K{tot[2]/1000:23.0f}K{T/1000:8.0f}K{T/TOK/1000:8.0f}K   -> new {100*tot[0]/T:.0f}%, repeat in context {100*tot[1]/T:.0f}%, re-read {100*tot[2]/T:.0f}%")
chk = sum(result_chars.get(c, 0) for c in segs)
print(f"check: output chars of all read calls = {chk/1000:.0f}K; classified = {T/1000:.0f}K; read calls = {len(segs)}; whole-file reads = {sum(1 for c in segs.values() if any(k=="whole" for *_, k in c))}")
print("(files the agent edited are shown apart: after an edit the lines may have changed, so a re-read there can be needed)")

print("\n== 2. files read most (reads, lines read vs distinct lines, file length now)")
rows = sorted(per_file.items(), key=lambda kv: -kv[1]["chars"])[:10]
for f, d in rows:
    L = lengths.get(f)
    cover = f"{100*len(d['distinct'])/L:.0f}% of {L} lines" if L else "length ?"
    print(f"  {d['reads']:3d} reads  {d['lines_read']:5d} lines read  {len(d['distinct']):5d} distinct  ({cover})  {d['chars']/1000:5.0f}K chars  {f.replace(REPO + '/', '')}{'  [edited]' if f in edited_files else ''}")

if slice_lines:
    print(f"\n== 3. slice size: {len(slice_lines)} partial reads, median {statistics.median(slice_lines):.0f} lines, mean {statistics.mean(slice_lines):.0f}, "
          f"{100*sum(1 for x in slice_lines if x <= 60)/len(slice_lines):.0f}% are 60 lines or fewer")

# ---- pass 3: where does the wall-clock time of the turns go?
print("\n== 4. wall-clock time per kind of turn (a turn = one model answer with its tool calls; pauses over 10 min excluded)")


def turn_class(tcs):
    names = [calls[i]["name"] for i in tcs]
    cmds = " ".join(str(calls[i]["args"].get("command", "")) for i in tcs if calls[i]["name"] == "terminal")
    if any(n in ("patch", "write_file") for n in names): return "edit / write"
    if re.search(r"cargo (test|build|clippy|check|fmt|run)", cmds): return "build / test"
    if any(i in segs for i in tcs): return "read (sed / head / cat / read_file)"
    if any(n in ("search_files",) for n in names) or re.search(r"\bgrep\b|\brg\b|\bfind\b|\bls\b", cmds): return "search / list"
    if re.search(r"\bgit\b", cmds): return "git"
    if all(n == "tool_call" for n in names): return "poll"
    return "other"


cls_t = collections.defaultdict(lambda: [0, 0.0])
first_in_req = collections.defaultdict(list)
for cid in order:
    first_in_req[calls[cid]["ri"]].append(cid)
for ri in range(len(main) - 1):
    new_here = first_in_req.get(ri + 1)
    gap = main[ri + 1]["t"] - main[ri]["t"]
    if not new_here or gap > PAUSE_S or gap < 0:
        continue
    k = turn_class(new_here)
    cls_t[k][0] += 1; cls_t[k][1] += gap
tt = sum(v[1] for v in cls_t.values())
print(f"{'kind of turn':38}{'turns':>7}{'minutes':>9}{'share':>7}{'s per turn':>12}")
for k, (n, s) in sorted(cls_t.items(), key=lambda kv: -kv[1][1]):
    print(f"{k:38}{n:7d}{s/60:9.1f}{100*s/tt:6.0f}%{s/n:12.0f}")
print(f"{'all turns':38}{sum(v[0] for v in cls_t.values()):7d}{tt/60:9.1f}")

# tokens of read output vs time of read turns
rd = cls_t.get("read (sed / head / cat / read_file)", [0, 0.0])
read_tokens = (tot[0] + tot[1] + tot[2]) / TOK
print(f"\nreading output the model had to take in: ~{read_tokens/1000:.0f}K tokens = about {read_tokens/719/60:.1f} min of prompt reading at 719 tok/s;"
      f" the {rd[0]} turns that issued those reads took {rd[1]/60:.0f} min of wall clock (the model's thinking before each call, not the read itself)")

all_gaps = [main[i + 1]["t"] - main[i]["t"] for i in range(len(main) - 1)]
active = sum(g for g in all_gaps if 0 <= g <= PAUSE_S)
print(f"turns above cover {tt/60:.0f} of {active/60:.0f} active minutes between consecutive requests "
      f"({(active - tt)/60:.0f} min are requests with no new tool call: compaction summaries, final answers); pauses over 10 min: "
      f"{[round(g/60) for g in all_gaps if g > PAUSE_S]} min")

print("\n== 5. avoidable round trips: consecutive read turns on the same file (one whole read would have replaced them)")
turn_files = []
for ri in range(len(main) - 1):
    new_here = first_in_req.get(ri + 1)
    gap = main[ri + 1]["t"] - main[ri]["t"]
    if not new_here or gap > PAUSE_S or gap < 0:
        continue
    k = turn_class(new_here)
    fs = {f for i in new_here if i in segs for f, *_ in segs[i]}
    turn_files.append((k, fs, gap))
avoid = 0; avoid_s = 0.0; run_files = set(); in_run = False
for k, fs, gap in turn_files:
    if k.startswith("read") and fs:
        if in_run and fs & run_files:
            avoid += 1; avoid_s += gap
        run_files = fs if not in_run else (run_files | fs); in_run = True
    else:
        in_run = False; run_files = set()
print(f"{avoid} of the {rd[0]} read turns repeat a file the previous read turn already touched; they took {avoid_s/60:.1f} min of wall clock")

top = rows[0][0] if rows else None
if top:
    print(f"\nexample, every read of {top.replace(REPO + '/', '')} in order:")
    seen_before = []
    for cid in order:
        if cid in segs:
            for f, v, lines, kind in segs[cid]:
                if f == top:
                    print(f"   request {calls[cid]['ri']:3d}  {kind:5s} lines {min(lines)}-{max(lines)} ({len(lines)} lines), file version {v}")
