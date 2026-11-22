#!/usr/bin/env python3
"""Which file-read outputs came back as IDENTICAL text? (only those can be loaded from a position-independent cache)
Usage: exact_repeats.py RECORDING.jsonl"""
import collections, hashlib, json, re, sys

recs = [json.loads(l) for l in open(sys.argv[1])]
main = [r for r in recs if r.get("tools")]
FILE = r"[\w./~$@+-]+\.(?:rs|toml|md|ron|wgsl|py|json|lock|txt|sh|yml|yaml|cu|cpp|hpp|glb|gltf)"


def kind_of(name, args):
    if name == "read_file":
        return "read_file"
    if name != "terminal":
        return None
    cmd = re.sub(r"\s+", " ", str(args.get("command", "")))
    if re.search(r"cat\s*>|<<\s*['\"]?EOF|tee ", cmd):
        return None
    if re.search(r"\bgrep\b|\brg\b|\bfind\b|cargo |\bgit\b|\bls\b", cmd):
        return None
    if re.search(r"sed -n", cmd): return "sed -n slice"
    if re.search(r"\bhead\b|\btail\b", cmd) and re.search(FILE, cmd): return "head / tail"
    if re.search(r"(^|&& |; )cat (" + FILE + ")", cmd): return "cat"
    return None


calls, content, first_req = {}, {}, {}
for ri, r in enumerate(main):
    for m in r["messages"]:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                if tc["id"] not in calls:
                    try: a = json.loads(tc["function"].get("arguments") or "{}")
                    except ValueError: a = {}
                    calls[tc["id"]] = (ri, tc["function"]["name"], a)
        elif m.get("role") == "tool" and m.get("tool_call_id") not in content:
            c = m.get("content"); content[m["tool_call_id"]] = c if isinstance(c, str) else json.dumps(c)

def text_of(c):
    try:
        d = json.loads(c)
        if isinstance(d, dict):
            return d.get("output") if isinstance(d.get("output"), str) else (d.get("content") if isinstance(d.get("content"), str) else c)
    except ValueError:
        pass
    return c

TOK = 3.0
seen = {}                                    # hash -> request index of the first occurrence
stats = collections.defaultdict(lambda: collections.Counter())
rows = []
for cid, (ri, name, a) in sorted(calls.items(), key=lambda kv: kv[1][0]):
    k = kind_of(name, a)
    if not k or cid not in content:
        continue
    t = text_of(content[cid]); n = len(t); h = hashlib.md5(t.encode()).hexdigest()
    stats[k]["calls"] += 1; stats[k]["chars"] += n
    if h in seen:
        # was the first copy still in the context of the previous request?
        prev_ids = {tc["id"] for m in main[ri - 1]["messages"] if m.get("role") == "assistant" for tc in (m.get("tool_calls") or [])} if ri else set()
        still = any(content.get(i) is not None and hashlib.md5(text_of(content[i]).encode()).hexdigest() == h for i in prev_ids)
        stats[k]["repeat_calls"] += 1; stats[k]["repeat_chars"] += n
        stats[k]["repeat_dropped_chars" if not still else "repeat_inctx_chars"] += n
        rows.append((k, n, still))
    else:
        seen[h] = ri

print(f"{'how it was read':14}{'calls':>6}{'chars K':>9}{'exact repeats':>15}{'repeat chars K':>16}{'  of which re-read after drop K':>32}")
tot = collections.Counter()
for k, s in sorted(stats.items(), key=lambda kv: -kv[1]["chars"]):
    print(f"{k:14}{s['calls']:6d}{s['chars']/1000:9.0f}{s['repeat_calls']:15d}{s['repeat_chars']/1000:16.0f}{s['repeat_dropped_chars']/1000:32.0f}")
    tot.update(s)
print(f"{'all':14}{tot['calls']:6d}{tot['chars']/1000:9.0f}{tot['repeat_calls']:15d}{tot['repeat_chars']/1000:16.0f}{tot['repeat_dropped_chars']/1000:32.0f}")
print(f"\nexact-repeat share of all read output: {100*tot['repeat_chars']/tot['chars']:.0f}%  (~{tot['repeat_chars']/TOK/1000:.0f}K tokens, ~{tot['repeat_chars']/TOK/719:.0f} s of prompt reading at 719 tok/s)")
print("\nexact repeats by size (what a cache could load; the stored image costs ~70 MB + 18 KB/token, a load ~0.35 s):")
for lo, hi in ((0, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 10**9)):
    sel = [(n, s) for _, n, s in rows if lo <= n < hi]
    ch = sum(n for n, _ in sel); dr = sum(n for n, s in sel if not s)
    save = sum(max(0, n / TOK / 719 - 0.35) for n, s in sel if not s)
    print(f"  {lo:>5}-{'' if hi>10**8 else hi:<6} chars: {len(sel):3d} repeats, {ch/1000:5.0f}K chars; {dr/1000:4.0f}K of them after a drop; est. saving if loaded: {save:4.0f} s")
