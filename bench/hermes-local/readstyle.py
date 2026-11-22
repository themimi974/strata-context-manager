"""How does the agent read files? Classifies every tool call in a gateway recording (JSONL of requests)."""
import collections, json, re, sys

recs = [json.loads(l) for l in open(sys.argv[1])]
calls, results = {}, {}
for ri, r in enumerate(recs):
    for m in r["messages"]:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                if tc["id"] not in calls:
                    try: a = json.loads(tc["function"].get("arguments") or "{}")
                    except ValueError: a = {}
                    calls[tc["id"]] = (ri, tc["function"]["name"], a)
        elif m.get("role") == "tool":
            c = m.get("content"); c = c if isinstance(c, str) else json.dumps(c)
            results.setdefault(m.get("tool_call_id"), len(c))

FILE = r"[\w./~$-]+\.(?:rs|toml|md|ron|wgsl|py|json|lock|txt|sh|yml|yaml|cu|cpp|hpp)"
kinds = collections.Counter(); chars = collections.Counter(); whole = collections.defaultdict(list); n_all = len(calls)
for cid, (ri, name, a) in calls.items():
    size = results.get(cid, 0)
    if name in ("read_file", "skill_view"):
        k = name
    elif name == "terminal":
        cmd = re.sub(r"\s+", " ", str(a.get("command", "")))
        # strip leading cd/env/. ~/.cargo/env parts
        body = re.split(r"&&|;", cmd)
        body = [p.strip() for p in body if not re.match(r"^(cd |export |\. |source |timeout \d+ \. )", p.strip())]
        main = " ".join(body)
        m_cat = re.match(r"^cat (" + FILE + r")( \|.*)?$", main)
        if m_cat and not m_cat.group(2): k = "cat whole file"; whole[m_cat.group(1)].append((ri, size))
        elif re.search(r"\bsed -n\b", main) or re.search(r"\b(head|tail) -", main) or re.search(r"\bnl\b.*\|", main): k = "partial (sed -n / head / tail)"
        elif re.search(r"\bgrep\b|\brg\b|\bfind\b", main): k = "search (grep/find)"
        elif re.search(r"cargo (test|build|clippy|check|fmt)", main): k = "cargo"
        elif re.search(r"\bgit\b", main): k = "git"
        elif re.search(r"\bcat\b.*" + FILE, main): k = "cat of several files / piped"
        else: k = "other terminal"
    elif name == "tool_call": k = "process poll"
    else: k = name
    kinds[k] += 1; chars[k] += size
print(f"tool calls in the recording: {n_all} (requests: {len(recs)})\n")
print(f"{'kind':34}{'calls':>6}{'share':>7}{'output chars':>14}")
for k, n in kinds.most_common(): print(f"{k:34}{n:6d}{100*n/n_all:6.0f}%{chars[k]:14,d}")
big = {f: v for f, v in whole.items()}
print("\nwhole-file `cat` reads, per file (call index in recording -> output chars):")
for f, v in sorted(big.items(), key=lambda x: -sum(s for _, s in x[1])):
    print(f"  {len(v)}x {f}: {[s for _, s in v]}")
cands = [(f, s) for f, v in whole.items() for _, s in v if s >= 4000]
print(f"\nwhole-file cats with output >= 4000 chars (what our cache would take): {len(cands)}, {sum(s for _, s in cands):,} chars (~{sum(s for _, s in cands)//3:,} tokens)")
rep = {f: [s for _, s in v if s >= 4000] for f, v in whole.items() if len([1 for _, s in v if s >= 4000]) >= 2}
print("files cat'ed whole 2+ times:", {f: len(v) for f, v in rep.items()})
