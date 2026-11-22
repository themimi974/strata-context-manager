"""F1.6 quality test of the file state cache on the F1 engine (:8082), run on VM 102 as root with the Strata venv.

Families per case: full (normal read), full2 (normal read, other run id = the engine's own noise), comp (file composed
from its stored state on EVERY question), and on the large file comp_noT (composed without T).  Yardstick:
KL(comp||full) vs KL(full2||full) on the first generated token, plus accuracy on questions with known answers
(quote a line, find the line of a text, rename as a unified diff).  The client starts the F1 server itself and
restarts it after a GPU fault, then redoes the interrupted family.  Results: $OUT/f16.json after every case."""
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
    json.dump(REPORT, open(f"{OUT}/f16.json", "w"), indent=1)


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
def capture(msgs_x, msg_i, c0, c1, dst):
    for attempt in range(3):
        try:
            toks, _ = dry_spans(msgs_x, [{"message": msg_i, "start": c0, "end": c1}])
            if not toks or not toks[0]:
                raise RuntimeError(f"span not resolved: {toks}")
            f0, f1 = toks[0]
            a, e = ((f0 + 8 + 3) // 4) * 4, ((f1 - 4) // 4) * 4
            os.makedirs(CAP, exist_ok=True)
            for f in os.listdir(CAP):
                os.remove(f"{CAP}/{f}")
            ask(with_nonce(msgs_x), {"strata_seg": {"tokens": [a, e]}}, max_tokens=1)
            got = sorted(os.listdir(CAP))
            if not {"T.f32", "after.f32", "before.f32", "qsa.bin", "ple.f32", "meta.txt"} <= set(got):
                raise Retry(f"capture incomplete: {got}")
            os.makedirs(dst, exist_ok=True)
            for f in os.listdir(dst):
                if os.path.isfile(f"{dst}/{f}"):
                    os.remove(f"{dst}/{f}")
            for f in got:
                shutil.copy(f"{CAP}/{f}", f"{dst}/{f}")
            return (a, e), (f0, f1)
        except Retry as r:
            restart(f"capture: {r}")
    raise RuntimeError("capture failed 3 times")


def align(build, spans_x, files_x, n_pads=1):
    """build(pads) -> (msgs, segments). Each file must start at the same position mod 4 as when captured, same length."""
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
    spans = [[t[0] + (sx[0] - fx[0]), t[0] + (sx[1] - fx[0])] for t, sx, fx in zip(toks, spans_x, files_x)]
    return msgs, spans, pads, n


def run_family(fam, msgs_y, spans, qs):
    for attempt in range(3):
        try:
            base = with_nonce(msgs_y)
            extra = None
            if fam.startswith("comp"):
                extra = {"strata_segc": {"spans": spans}} if len(spans) > 1 else {"strata_segc": {"tokens": spans[0]}}
                if fam == "comp_noT":
                    extra["strata_segc_not"] = True
            out = []
            for q in qs:
                msgs = base + [{"role": "assistant", "content": ACK}, {"role": "user", "content": q["q"]}]
                out.append(ask(msgs, extra, max_tokens=q["max"]))
            return out
        except Retry as r:
            if not restart(f"{fam}: {r}"):
                raise RuntimeError("server did not come back")
    raise RuntimeError(f"family {fam} failed 3 times")


def evaluate(name, msgs_y, spans, qs, families=("full", "full2", "comp")):
    res = {}
    for fam in families:
        t0 = time.time()
        res[fam] = run_family(fam, msgs_y, spans, qs)
        log(f"{name}/{fam}: {len(qs)} questions in {time.time() - t0:.0f} s")
    rows = []
    for i, q in enumerate(qs):
        row = {"kind": q["kind"], "line": q["line"]}
        for fam in families:
            row["ok_" + fam] = score(q, res[fam][i]["text"])
            row["text_" + fam] = res[fam][i]["text"][:200]
        row["kl_comp_full"] = kl(res["full"][i]["logits"], res["comp"][i]["logits"])
        row["kl_full2_full"] = kl(res["full"][i]["logits"], res["full2"][i]["logits"])
        if "comp_noT" in res:
            row["kl_noT_full"] = kl(res["full"][i]["logits"], res["comp_noT"][i]["logits"])
        row["comp_composes"] = res["comp"][i]["composes"]
        row["comp_reused"] = res["comp"][i]["reused"]
        row["full_read_ms"], row["comp_read_ms"] = res["full"][i]["read_ms"], res["comp"][i]["read_ms"]
        row["comp_parts"] = res["comp"][i]["parts"]
        rows.append(row)

    def stat(key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return None if not v else {"n": len(v), "mean": float(np.mean(v)), "median": float(np.median(v)),
                                   "p90": float(np.quantile(v, .9)), "max": float(np.max(v))}
    summ = {"n": len(qs), "accuracy": {f: float(np.mean([r["ok_" + f] for r in rows])) for f in families},
            "accuracy_by_kind": {k: {f: float(np.mean([r["ok_" + f] for r in rows if r["kind"] == k])) for f in families}
                                 for k in sorted({r["kind"] for r in rows})},
            "kl_comp_full": stat("kl_comp_full"), "kl_full2_full": stat("kl_full2_full"), "kl_noT_full": stat("kl_noT_full"),
            "top1_same_comp": float(np.mean([int(np.argmax(res["comp"][i]["logits"]) == np.argmax(res["full"][i]["logits"]))
                                             for i in range(len(qs)) if res["comp"][i]["logits"] is not None
                                             and res["full"][i]["logits"] is not None] or [np.nan])),
            "top1_same_full2": float(np.mean([int(np.argmax(res["full2"][i]["logits"]) == np.argmax(res["full"][i]["logits"]))
                                              for i in range(len(qs)) if res["full2"][i]["logits"] is not None
                                              and res["full"][i]["logits"] is not None] or [np.nan])),
            "comp_questions_with_a_compose": int(sum(1 for r in rows if r["comp_composes"] > 0)),
            "comp_questions_reusing_cache": int(sum(1 for r in rows if (r["comp_reused"] or 0) > 0))}
    log(name, json.dumps({k: summ[k] for k in ("accuracy", "kl_comp_full", "kl_full2_full")})[:600])
    return summ, rows


def synthetic(path, nq, extra_fams=()):
    doc = render(path)
    msgs_x = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_X + HEAD + doc + "\n\nContinue."}]
    c0 = len(CTX_X + HEAD)
    span_x, file_x = capture(msgs_x, 1, c0, c0 + len(doc), COMP)

    def build(p):
        ctx = CTX_Y + " ." * p[0]
        cy = len(ctx + HEAD)
        return ([{"role": "system", "content": SYS}, {"role": "user", "content": ctx + HEAD + doc}],
                [{"message": 1, "start": cy, "end": cy + len(doc)}])
    msgs_y, spans, pads, n = align(build, [span_x], [file_x])
    if msgs_y is None:
        return {"error": "no alignment"}
    summ, rows = evaluate(os.path.basename(path), msgs_y, spans, questions(doc, *nq), ("full", "full2", "comp") + extra_fams)
    return {"file": os.path.basename(path), "file_tokens": file_x[1] - file_x[0], "shift": spans[0][0] - span_x[0],
            "prompt_tokens": n, "summary": summ, "rows": rows}


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
        if b["kind"] == "tool":
            text = "Tool result:\n" + c.split("\n\x1e")[0]
        else:
            text = c.replace("\x1e", "")
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
    last = msgs_x[-1]
    c0 = last["content"].find(doc)
    if c0 < 0:
        return {"error": "file not found in request 17"}
    last["content"] += "\n\nContinue."
    span_x, file_x = capture(msgs_x, len(msgs_x) - 1, c0, c0 + len(doc), COMP)
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
    msgs_y, spans, pads, n = align(build, [span_x], [file_x])
    if msgs_y is None:
        return {"error": "no alignment"}
    summ, rows = evaluate("compaction-replay", msgs_y, spans, questions(doc, 10, 4, 6))
    return {"file_tokens": file_x[1] - file_x[0], "captured_at_token": span_x[0], "composed_at_token": spans[0][0],
            "prompt_tokens": n, "summary": summ, "rows": rows}


def three_files():
    paths = [SMALL, MEDIUM, LARGE]
    docs = [render(p) for p in paths]
    spans_x, files_x = [], []
    for i, doc in enumerate(docs):
        msgs_x = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_X + HEAD + doc + "\n\nContinue."}]
        c0 = len(CTX_X + HEAD)
        s, f = capture(msgs_x, 1, c0, c0 + len(doc), f"{COMP}/{i}")
        spans_x.append(s)
        files_x.append(f)
    sep = "\n\nHere is the result of the next read_file:\n"

    def build(p):
        text, segs = CTX_Y + " ." * p[0] + HEAD, []
        for i, doc in enumerate(docs):
            if i:
                text += " ." * p[i] + sep
            segs.append({"message": 1, "start": len(text), "end": len(text) + len(doc)})
            text += doc
        return [{"role": "system", "content": SYS}, {"role": "user", "content": text}], segs
    msgs_y, spans, pads, n = align(build, spans_x, files_x, n_pads=3)
    if msgs_y is None:
        return {"error": "no alignment", "pads": pads}
    qs = []
    for i, doc in enumerate(docs):
        qs += questions(doc, 3, 2, 1, label=f"file {i + 1} ({os.path.basename(paths[i])})")
    summ, rows = evaluate("three-files", msgs_y, spans, qs)
    return {"prompt_tokens": n, "summary": summ, "rows": rows}


def deep():
    ctx = "Context from the repository:"
    for p in DEEP_SRC:
        ctx += f"\n\n// ===== {os.path.basename(p)} =====\n" + open(p, errors="replace").read()
        if len(ctx) > 200000:
            break
    doc = render(MEDIUM)
    msgs_x = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_X + HEAD + doc + "\n\nContinue."}]
    c0 = len(CTX_X + HEAD)
    span_x, file_x = capture(msgs_x, 1, c0, c0 + len(doc), COMP)

    def build(p):
        c = ctx + " ." * p[0]
        cy = len(c + HEAD)
        return ([{"role": "system", "content": SYS}, {"role": "user", "content": c + HEAD + doc}],
                [{"message": 1, "start": cy, "end": cy + len(doc)}])
    msgs_y, spans, pads, n = align(build, [span_x], [file_x])
    if msgs_y is None:
        return {"error": "no alignment"}
    summ, rows = evaluate("deep", msgs_y, spans, questions(doc, 8, 4, 4))
    return {"file_tokens": file_x[1] - file_x[0], "file_at_token": spans[0][0], "prompt_tokens": n, "summary": summ,
            "rows": rows}


def realistic_timing():
    doc = render(LARGE)
    msgs_x = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_X + HEAD + doc + "\n\nContinue."}]
    c0 = len(CTX_X + HEAD)
    span_x, file_x = capture(msgs_x, 1, c0, c0 + len(doc), COMP)
    prefix = [{"role": "system", "content": SYS}, {"role": "user", "content": CTX_Y}, {"role": "assistant", "content": "Noted."}]

    def build(p):
        m = json.loads(json.dumps(prefix))
        m[2]["content"] += " ." * p[0]
        m.append({"role": "user", "content": HEAD.strip() + "\n" + doc})
        s = len(HEAD.strip() + "\n")
        return m, [{"message": 3, "start": s, "end": s + len(doc)}]
    msgs_y, spans, pads, n = align(build, [span_x], [file_x])
    runs = []
    for rep in range(3):
        for mode in ("normal", "composed"):
            for attempt in range(3):
                try:
                    base = with_nonce(msgs_y)
                    ask(base[:3] + [{"role": "user", "content": "Say OK."}], max_tokens=1)   # caches the turn before the file
                    extra = {"strata_segc": {"tokens": spans[0]}} if mode == "composed" else None
                    r = ask(base + [{"role": "assistant", "content": ACK}, {"role": "user", "content": "Say OK."}], extra, 1)
                    runs.append({"mode": mode, "rep": rep, "read_ms": r["read_ms"], "reused": r["reused"], "parts": r["parts"],
                                 "composes": r["composes"], "wall": round(r["wall"], 2)})
                    log("timing", runs[-1])
                    break
                except Retry as rr:
                    restart(f"timing: {rr}")
    return {"file_tokens": file_x[1] - file_x[0], "runs": runs}


os.makedirs(OUT, exist_ok=True)
REPORT["gpu_resets_at_start"] = resets()
if not start_server():
    REPORT["error"] = "F1 server did not start"
    save()
    sys.exit(1)
plan = [("realistic_timing", realistic_timing), ("replay", replay),
        ("small", lambda: synthetic(SMALL, (10, 4, 6))), ("medium", lambda: synthetic(MEDIUM, (10, 4, 6))),
        ("large", lambda: synthetic(LARGE, (12, 4, 8), ("comp_noT",))), ("three_files", three_files), ("deep", deep)]
for step, fn in plan:
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
log("F16_CLIENT_DONE")
