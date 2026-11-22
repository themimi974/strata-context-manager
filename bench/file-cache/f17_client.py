"""F1.7 (partial re-read) quality test of the file state cache on the F1 engine (:8082), run on VM 102 as root with the Strata venv.

Families per case: full (normal read), full2 (normal read, other run id = the engine's own noise), comp (file composed
from its stored state on EVERY question), and on the large file comp_noT (composed without T).  Yardstick:
KL(comp||full) vs KL(full2||full) on the first generated token, plus accuracy on questions with known answers
(quote a line, find the line of a text, rename as a unified diff).  The client starts the F1 server itself and
restarts it after a GPU fault, then redoes the interrupted family.  Results: $OUT/f17.json after every case."""
import glob, json, os, random, re, shutil, subprocess, sys, time, traceback, urllib.request
import numpy as np

URL = "http://127.0.0.1:8082"
CAP, COMP, FIRST = "/nvme/f1/cap", "/nvme/f1/comp", "/nvme/f1/first.f32"
ELOG = "/root/Strata-data/bench/f1-engine.log"
PY = "/root/Strata/terminal/.venv/bin/python"
OUT, SMALL, MEDIUM, LARGE, REPLAY = sys.argv[1:6]
DEEP_SRC = sys.argv[6:]
CTX_X = open("/opt/strata-context-manager/README.md").read()
CTX_Y = open("/opt/strata-context-manager/docs/HOW-IT-WORKS.md").read()
HEAD = "\n\nHere is the result of read_file:\n"
ACK = "I have read the file."
SYS = "You are a careful coding assistant. Answer briefly."
random.seed(11)
REPORT = {"started": time.strftime("%H:%M:%S"), "restarts": []}
nonce = [0]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def save():
    json.dump(REPORT, open(f"{OUT}/f17.json", "w"), indent=1)


# ------------------------------------------------------------------ server control
def resets():
    out = subprocess.run(["dmesg"], capture_output=True, text=True).stdout
    return out.count("GPU reset(")


def server_loaded():
    try:
        with urllib.request.urlopen(URL + "/v1/models", timeout=5) as r:
            return b"loaded" in r.read()
    except Exception:
        return False


def stop_server():
    subprocess.run(["pkill", "-f", r"(^|/)[.]venv/bin/python serve/server[.]py .*--port 8082"])
    for _ in range(60):
        if subprocess.run(["pgrep", "-f", r"^/root/Strata-f1/build-f1/strata --serve"]).returncode != 0:
            break
        time.sleep(1)
    subprocess.run(["pkill", "-9", "-f", r"^/root/Strata-f1/build-f1/strata --serve"])
    time.sleep(3)


def start_server():
    out = open(f"{OUT}/f1-server.out", "a")
    subprocess.Popen([PY, "serve/server.py", "--engine", "strata", "--config", "f1-iq2xs-128k.json", "--port", "8082",
                      "--no-api-key"], cwd="/root/Strata-f1/terminal", stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(150):
        if server_loaded():
            return True
        time.sleep(4)
    return False


def restart(why):
    log("RESTART:", why)
    REPORT["restarts"].append({"t": time.strftime("%H:%M:%S"), "why": why[:200], "gpu_resets": resets()})
    save()
    stop_server()
    time.sleep(10)
    ok = start_server()
    log("server back:", ok)
    return ok


class Retry(Exception):
    pass


# ------------------------------------------------------------------ requests
def post(path, b, timeout=3600):
    req = urllib.request.Request(URL + path, json.dumps(b).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ask(msgs, extra=None, max_tokens=8):
    for f in glob.glob(FIRST + ".*"):
        os.remove(f)
    b = {"model": "x", "messages": msgs, "max_tokens": max_tokens, "temperature": 0,
         "chat_template_kwargs": {"enable_thinking": False}}
    if extra:
        b.update(extra)
    n0, r0 = os.path.getsize(ELOG), resets()
    t0 = time.time()
    try:
        d = post("/v1/chat/completions", b)
    except Exception as ex:
        raise Retry(f"{ex!r} (GPU resets +{resets() - r0})")
    wall = time.time() - t0
    time.sleep(0.3)
    with open(ELOG, "rb") as f:
        f.seek(n0)
        lg = f.read().decode(errors="replace")
    read_ms = reused = None
    parts = []
    for line in lg.splitlines():
        m = re.match(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms", line)
        if m:
            reused, read_ms = int(m.group(2)), float(m.group(4))
        m = re.match(r"strata trace: read (\d+) tokens \((\w+)\) in ([\d.]+) ms", line)
        if m:
            parts.append([int(m.group(1)), m.group(2), float(m.group(3))])
        m = re.match(r"strata serve: segc compose \[(\d+), (\d+)\): (\S+) \(GPU ([\d.]+) ms, wall ([\d.]+) ms\)", line)
        if m:
            parts.append([int(m.group(2)) - int(m.group(1)), "compose-" + m.group(3), float(m.group(5))])
    fl = sorted(glob.glob(FIRST + ".*"))
    logits = np.fromfile(fl[0], dtype=np.float32) if fl else None
    composes = len(re.findall(r"segc compose \[\d+, \d+\): done", lg))
    ignored = "segc=" in lg and "ignored" in lg
    return {"text": d["choices"][0]["message"].get("content") or "", "wall": wall, "read_ms": read_ms,
            "reused": reused, "parts": parts, "logits": logits, "composes": composes, "segc_ignored": ignored}


def softmax(x):
    x = x.astype(np.float64) - x.max()
    p = np.exp(x)
    return p / p.sum()


def kl(a, b):
    if a is None or b is None:
        return None
    p, q = softmax(a), softmax(b)
    return float(np.sum(p * (np.log(p + 1e-30) - np.log(q + 1e-30))))


def with_nonce(msgs):
    nonce[0] += 1
    m = json.loads(json.dumps(msgs))
    m[0]["content"] = f"[run {nonce[0]:06d}] " + m[0]["content"]
    return m


def dry_spans(msgs, segments):
    b = {"model": "x", "messages": with_nonce(msgs), "max_tokens": 1, "strata_dry": True,
         "chat_template_kwargs": {"enable_thinking": False}, "strata_seg": {"segments": segments}}
    d = post("/v1/chat/completions", b, 300)
    return [s.get("tokens") for s in d["strata_seg"]["segments"]], d["prompt_tokens"]


# ------------------------------------------------------------------ files and questions
def render(path):
    text = open(path, encoding="utf-8", errors="replace").read().removesuffix("\n")
    numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(text.split("\n"), 1))
    return json.dumps({"content": numbered, "total_lines": numbered.count("\n") + 1}, ensure_ascii=False)


def file_lines(doc):
    out = {}
    for raw in json.loads(doc)["content"].split("\n"):
        m = re.match(r"^(\d+)\|(.*)$", raw)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def questions(doc, n_quote, n_where, n_diff, label="the file"):
    lines = file_lines(doc)
    good = [n for n, t in lines.items() if len(t.strip()) >= 12 and len(t) <= 160]
    qs = []
    for n in random.sample(good, min(n_quote, len(good))):
        qs.append({"kind": "quote", "line": n, "truth": lines[n], "max": 70,
                   "q": f"Quote line {n} of {label} exactly, without the line number. Reply with the line only."})
    uniq = [n for n in good if sum(1 for t in lines.values() if lines[n].strip() in t) == 1]
    for n in random.sample(uniq, min(n_where, len(uniq))):
        qs.append({"kind": "where", "line": n, "truth": str(n), "max": 8,
                   "q": f"In {label}, on which line number is this exact text: `{lines[n].strip()}` ? Answer with the number only."})
    idents = [(n, m.group(0)) for n in good for m in [re.search(r"\b[a-z_][a-z0-9_]{4,}\b", lines[n])] if m]
    for n, ident in random.sample(idents, min(n_diff, len(idents))):
        qs.append({"kind": "diff", "line": n, "truth": [lines[n], lines[n].replace(ident, ident + "_v2", 1)], "max": 120,
                   "q": f"In {label}, rename `{ident}` to `{ident}_v2` on line {n} only. Reply with only a unified diff of that change."})
    return qs


def score(q, text):
    t = text.strip()
    if q["kind"] == "quote":
        t = t.replace("```", "").strip()
        t = t.split("\n")[0] if t else t
        t = re.sub(r"^\d+\|", "", t).strip().strip("`")
        return t.strip() == q["truth"].strip()
    if q["kind"] == "where":
        m = re.search(r"\d+", t)
        return bool(m) and m.group(0) == q["truth"]
    old, new = q["truth"]
    minus = [l[1:] for l in text.split("\n") if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:] for l in text.split("\n") if l.startswith("+") and not l.startswith("+++")]
    hunk = re.search(r"@@ -(\d+)", text)
    return (any(m.strip() == old.strip() for m in minus) and any(p.strip() == new.strip() for p in plus)
            and bool(hunk) and abs(int(hunk.group(1)) - q["line"]) <= 3)


# ------------------------------------------------------------------ capture, align, families

VARIANTS = {"comp": (0.0, 0.0), "head15": (0.15, 0.0), "tail15": (0.0, 0.15), "edges10": (0.10, 0.10)}


def up4(x):
    return ((x + 3) // 4) * 4


def down4(x):
    return (x // 4) * 4


def capture(msgs_x, msg_i, c0, c1, dst, head, tail):
    """Captures the part of the file [start + 8 + head share, end - 4 - tail share); the rest is read normally at use."""
    for attempt in range(3):
        try:
            toks, _ = dry_spans(msgs_x, [{"message": msg_i, "start": c0, "end": c1}])
            if not toks or not toks[0]:
                raise RuntimeError(f"span not resolved: {toks}")
            f0, f1 = toks[0]
            n = f1 - f0
            a, e = up4(f0 + 8 + round(head * n)), down4(f1 - 4 - round(tail * n))
            os.makedirs(CAP, exist_ok=True)
            for f in os.listdir(CAP):
                os.remove(f"{CAP}/{f}")
            ask(with_nonce(msgs_x), {"strata_seg": {"tokens": [a, e]}}, max_tokens=1)
            got = sorted(os.listdir(CAP))
            if not {"T.f32", "after.f32", "before.f32", "qsa.bin", "ple.f32", "meta.txt"} <= set(got):
                raise Retry(f"capture incomplete: {got}")
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            os.makedirs(dst)
            for f in got:
                shutil.copy(f"{CAP}/{f}", f"{dst}/{f}")
            return (a, e), (f0, f1)
        except Retry as r:
            restart(f"capture: {r}")
    raise RuntimeError("capture failed 3 times")


def capture_all(name, files):
    """files: list of (msgs_x, msg_i, c0, c1).  Returns {variant: [(span_x, file_x, dir), ...]}."""
    out = {}
    for v, (h, t) in VARIANTS.items():
        out[v] = []
        for k, (m, i, c0, c1) in enumerate(files):
            d = f"/nvme/f1/store/{name}/{v}/{k}"
            sx, fx = capture(m, i, c0, c1, d, h, t)
            out[v].append((sx, fx, d))
    return out


def align(build, files_x, n_pads=1):
    """build(pads) -> (msgs, segments).  Each file must start at the same position mod 4 as when captured, same length.
    Returns (msgs, token spans of the files, pads, prompt tokens)."""
    pads = [0] * n_pads
    toks = n = None
    for k in range(n_pads):
        for p in range(16):
            pads[k] = p
            msgs, segs = build(pads)
            toks, n = dry_spans(msgs, segs)
            ks = range(len(toks)) if n_pads == 1 else [k]
            if all(toks[i] and (toks[i][0] - files_x[i][0]) % 4 == 0 and toks[i][1] - toks[i][0] == files_x[i][1] - files_x[i][0]
                   for i in ks):
                break
    msgs, segs = build(pads)
    if not all(t and (t[0] - fx[0]) % 4 == 0 and t[1] - t[0] == fx[1] - fx[0] for t, fx in zip(toks, files_x)):
        return None, None, pads, n
    return msgs, toks, pads, n


def stash(dirs):
    for f in os.listdir(COMP):
        p = f"{COMP}/{f}"
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    if len(dirs) == 1:
        for f in os.listdir(dirs[0]):
            shutil.copy(f"{dirs[0]}/{f}", f"{COMP}/{f}")
    else:
        for k, d in enumerate(dirs):
            shutil.copytree(d, f"{COMP}/{k}")


def run_family(fam, msgs_y, spans, qs, dirs):
    for attempt in range(3):
        try:
            base = with_nonce(msgs_y)
            extra = None
            if dirs is not None:
                stash(dirs)
                extra = {"strata_segc": {"spans": spans}} if len(spans) > 1 else {"strata_segc": {"tokens": spans[0]}}
            out = []
            for q in qs:
                msgs = base + [{"role": "assistant", "content": ACK}, {"role": "user", "content": q["q"]}]
                out.append(ask(msgs, extra, max_tokens=q["max"]))
            return out
        except Retry as r:
            if not restart(f"{fam}: {r}"):
                raise RuntimeError("server did not come back")
    raise RuntimeError(f"family {fam} failed 3 times")


def evaluate(name, msgs_y, toks, caps, qs):
    res = {}
    for fam in ["full", "full2"] + list(VARIANTS):
        t0 = time.time()
        if fam in VARIANTS:
            spans = [[t[0] + (sx[0] - fx[0]), t[0] + (sx[1] - fx[0])] for t, (sx, fx, d) in zip(toks, caps[fam])]
            res[fam] = run_family(fam, msgs_y, spans, qs, [d for _, _, d in caps[fam]])
        else:
            res[fam] = run_family(fam, msgs_y, None, qs, None)
        log(f"{name}/{fam}: {len(qs)} questions in {time.time() - t0:.0f} s")
    fams = list(res)
    rows = []
    for i, q in enumerate(qs):
        row = {"kind": q["kind"], "line": q["line"]}
        for f in fams:
            row["ok_" + f] = score(q, res[f][i]["text"])
            if f != "full":
                row["kl_" + f] = kl(res["full"][i]["logits"], res[f][i]["logits"])
                row["top1_" + f] = (None if res[f][i]["logits"] is None or res["full"][i]["logits"] is None
                                    else int(np.argmax(res[f][i]["logits"]) == np.argmax(res["full"][i]["logits"])))
        rows.append(row)

    def mean(key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return None if not v else float(np.mean(v))
    summ = {"n": len(qs),
            "accuracy": {f: mean("ok_" + f) for f in fams},
            "accuracy_quote_diff": {f: float(np.mean([r["ok_" + f] for r in rows if r["kind"] != "where"] or [np.nan]))
                                    for f in fams},
            "kl_mean": {f: mean("kl_" + f) for f in fams if f != "full"},
            "kl_median": {f: (float(np.median([r["kl_" + f] for r in rows if r.get("kl_" + f) is not None])) if f != "full" else None)
                          for f in fams if f != "full"},
            "top1_same": {f: mean("top1_" + f) for f in fams if f != "full"},
            "first_read_ms": {f: res[f][0]["read_ms"] for f in fams},
            "first_parts": {f: res[f][0]["parts"] for f in fams}}
    noise = summ["kl_mean"]["full2"] or 1e-9
    summ["kl_vs_noise"] = {f: round(v / noise, 2) for f, v in summ["kl_mean"].items() if v is not None}
    log(name, json.dumps({k: summ[k] for k in ("accuracy", "kl_vs_noise", "top1_same")})[:900])
    return summ, rows


def file_case(name, msgs_y_build, doc, nq):
    msgs_x = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_X + HEAD + doc + "\n\nContinue."}]
    c0 = len(CTX_X + HEAD)
    caps = capture_all(name, [(msgs_x, 1, c0, c0 + len(doc))])
    msgs_y, toks, pads, n = align(msgs_y_build, [caps["comp"][0][1]])
    if msgs_y is None:
        return {"error": "no alignment"}
    summ, rows = evaluate(name, msgs_y, toks, caps, questions(doc, *nq))
    return {"file_tokens": toks[0][1] - toks[0][0], "file_at_token": toks[0][0], "prompt_tokens": n, "summary": summ, "rows": rows}


def large():
    doc = render(LARGE)

    def build(p):
        ctx = CTX_Y + " ." * p[0]
        cy = len(ctx + HEAD)
        return ([{"role": "system", "content": SYS}, {"role": "user", "content": ctx + HEAD + doc}],
                [{"message": 1, "start": cy, "end": cy + len(doc)}])
    return file_case("large", build, doc, (10, 4, 6))


def deep():
    ctx = "Context from the repository:"
    for p in DEEP_SRC:
        ctx += f"\n\n// ===== {os.path.basename(p)} =====\n" + open(p, errors="replace").read()
        if len(ctx) > 200000:
            break
    doc = render(MEDIUM)

    def build(p):
        c = ctx + " ." * p[0]
        cy = len(c + HEAD)
        return ([{"role": "system", "content": SYS}, {"role": "user", "content": c + HEAD + doc}],
                [{"message": 1, "start": cy, "end": cy + len(doc)}])
    return file_case("deep", build, doc, (8, 4, 4))


def replay_msgs(blocks):
    msgs, system, tools = [], [], ""
    for b in blocks:
        c = b["content"]
        if b["kind"] == "system":
            system.append(c)
            continue
        if b["kind"] == "tools":
            tools = c
            continue
        text = "Tool result:\n" + c.split("\n\x1e")[0] if b["kind"] == "tool" else c.replace("\x1e", "")
        role = "assistant" if b["kind"] == "assistant" else "user"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": role, "content": text})
    return [{"role": "system", "content": "\n".join(system) + "\n\nAvailable tools (JSON):\n" + tools}] + msgs


def replay():
    blocks = json.load(open(REPLAY))
    b17, b50 = [b for b in blocks if b["rid"] == 17], [b for b in blocks if b["rid"] == 50]
    doc = [b for b in b17 if b["content"].startswith('{"content": "1|#!/usr/bin/env python3')][0]["content"].split("\n\x1e")[0]
    msgs_x = replay_msgs(b17)
    c0 = msgs_x[-1]["content"].find(doc)
    if c0 < 0:
        return {"error": "file not found in request 17"}
    msgs_x[-1]["content"] += "\n\nContinue."
    caps = capture_all("replay", [(msgs_x, len(msgs_x) - 1, c0, c0 + len(doc))])
    base_y = replay_msgs(b50)

    def build(p):
        msgs = json.loads(json.dumps(base_y))
        say = "I will re-read connect4.py." + " ." * p[0]
        if msgs[-1]["role"] == "assistant":
            msgs[-1]["content"] += "\n\n" + say
        else:
            msgs.append({"role": "assistant", "content": say})
        msgs.append({"role": "user", "content": "Tool result:\n" + doc})
        s = len("Tool result:\n")
        return msgs, [{"message": len(msgs) - 1, "start": s, "end": s + len(doc)}]
    msgs_y, toks, pads, n = align(build, [caps["comp"][0][1]])
    if msgs_y is None:
        return {"error": "no alignment"}
    summ, rows = evaluate("replay", msgs_y, toks, caps, questions(doc, 10, 4, 6))
    return {"file_tokens": toks[0][1] - toks[0][0], "prompt_tokens": n, "summary": summ, "rows": rows}


def three_files():
    paths = [SMALL, MEDIUM, LARGE]
    docs = [render(p) for p in paths]
    files = []
    for doc in docs:
        m = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_X + HEAD + doc + "\n\nContinue."}]
        c0 = len(CTX_X + HEAD)
        files.append((m, 1, c0, c0 + len(doc)))
    caps = capture_all("three", files)
    sep = "\n\nHere is the result of the next read_file:\n"

    def build(p):
        text, segs = CTX_Y + " ." * p[0] + HEAD, []
        for i, doc in enumerate(docs):
            if i:
                text += " ." * p[i] + sep
            segs.append({"message": 1, "start": len(text), "end": len(text) + len(doc)})
            text += doc
        return [{"role": "system", "content": SYS}, {"role": "user", "content": text}], segs
    msgs_y, toks, pads, n = align(build, [fx for _, fx, _ in caps["comp"]], n_pads=3)
    if msgs_y is None:
        return {"error": "no alignment", "pads": pads}
    qs = []
    for i, doc in enumerate(docs):
        qs += questions(doc, 3, 2, 1, label=f"file {i + 1} ({os.path.basename(paths[i])})")
    summ, rows = evaluate("three", msgs_y, toks, caps, qs)
    return {"prompt_tokens": n, "summary": summ, "rows": rows}


os.makedirs(OUT, exist_ok=True)
os.makedirs(COMP, exist_ok=True)
REPORT["variants"] = VARIANTS
REPORT["gpu_resets_at_start"] = resets()
if not start_server():
    REPORT["error"] = "F1 server did not start"
    save()
    sys.exit(1)
for step, fn in [("deep", deep), ("three_files", three_files), ("replay", replay), ("large", large)]:
    t0 = time.time()
    log("== step", step)
    try:
        REPORT[step] = fn()
    except Exception as ex:
        REPORT[step] = {"error": repr(ex), "trace": traceback.format_exc()[-1500:]}
        log(step, "ERROR", repr(ex))
    REPORT[step + "_seconds"] = round(time.time() - t0)
    save()
REPORT["gpu_resets_at_end"] = resets()
REPORT["finished"] = time.strftime("%H:%M:%S")
save()
stop_server()
log("F17_CLIENT_DONE")
