#!/usr/bin/env python3
"""End-to-end check of the shell-read cache without an agent: two different conversations show the same `cat` (or `sed -n`)
output; the gateway must name it (strata_segx) and Strata must store it the first time and load it the second."""
import json, subprocess, sys, time, urllib.request, uuid

STRATA = "http://192.168.1.223:8080/v1/chat/completions"
SRC = open(sys.argv[1]).read()


def observe(messages):
    body = json.dumps({"call_id": str(uuid.uuid4()), "model": "qwen3.8-flash-next-iq2_xs", "messages": messages, "tools": None})
    out = subprocess.run(["pct", "exec", "105", "--", "curl", "-s", "-m", "60", "-H", "Content-Type: application/json",
                          "-d", "@-", "localhost:8090/v1/observe"], input=body, capture_output=True, text=True).stdout
    return json.loads(out[out.index("{"):])


def convo(intro, command, text):
    res = json.dumps({"output": text, "exit_code": 0, "error": None, "cwd": "/home/hermes/proj"})
    return [{"role": "system", "content": "You are a coding assistant working in /home/hermes/proj."},
            {"role": "user", "content": intro},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c" + uuid.uuid4().hex[:8], "type": "function",
             "function": {"name": "terminal", "arguments": json.dumps({"command": command})}}]},
            {"role": "tool", "tool_call_id": None, "content": res},
            {"role": "user", "content": "In one short sentence, what is this file about?"}]


def run(label, intro, command, text):
    m = convo(intro, command, text)
    m[3]["tool_call_id"] = m[2]["tool_calls"][0]["id"]
    seg = observe(m).get("segx")
    body = {"model": "qwen3.8-flash-next-iq2_xs", "messages": m, "max_tokens": 60, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False}}
    if seg:
        body["strata_segx"] = seg
    t0 = time.time()
    d = json.load(urllib.request.urlopen(urllib.request.Request(STRATA, json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=600))
    print(f"{label:34} gateway named {len(seg['files']) if seg else 0} segment(s) | {time.time()-t0:5.1f} s | prompt {d['usage']['prompt_tokens']} tok, "
          f"cached {(d['usage'].get('prompt_tokens_details') or {}).get('cached_tokens')} | answer: {(d['choices'][0]['message'].get('content') or '')[:70]!r}", flush=True)


lines = SRC.splitlines()
slice_text = "\n".join(lines[40:160])
tag = uuid.uuid4().hex[:6]                       # a fresh first message each run, so nothing comes from the prefix cache
run("cat, conversation 1 (expect store)", f"[{tag}-A] Please look at segcompose.cu.", "cd /home/hermes/proj && cat src/segcompose.cu", SRC)
run("cat, conversation 2 (expect load)", f"[{tag}-B] Another task: open segcompose.cu.", "cat src/segcompose.cu", SRC)
run("slice, conversation 1 (expect store)", f"[{tag}-C] Show lines 41-160.", "sed -n '41,160p' src/segcompose.cu", slice_text)
run("slice, conversation 2 (expect load)", f"[{tag}-D] Lines 41 to 160 again please.", "sed -n '41,160p' src/segcompose.cu", slice_text)
