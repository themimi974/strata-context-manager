#!/usr/bin/env python3
"""Does strata_prefix (pin) on the first request after a slot restore throw away the restored state?

Runs on the Proxmox host against Strata directly. Uses request #12 of the recorded C-1 session (~20K tokens)."""
import json, os, re, subprocess, time, urllib.request

VM = os.environ.get("STRATA_SSH", "ssh root@strata-host").split()      # how to run a command on the Strata machine
S = os.environ.get("STRATA_URL", "http://strata-host:8080")
ELOG = os.environ.get("STRATA_ENGINE_LOG", "/var/log/strata-engine.log")   # "log" in the Strata config
LINE = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms")


def vm(cmd):
    return subprocess.check_output(VM + [cmd]).decode()


def post(path, body):
    req = urllib.request.Request(S + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        return json.loads(r.read() or b"{}")


def chat(msgs, tools, pin):
    off = int(vm(f"sudo stat -c %s {ELOG}").strip())
    body = {"model": "x", "messages": msgs, "tools": tools, "max_tokens": 1, "temperature": 0}
    if pin:
        body["strata_prefix"] = {"messages": 1}
    post("/v1/chat/completions", body)
    time.sleep(1)
    m = LINE.findall(vm(f"sudo tail -c +{off + 1} {ELOG}"))
    return dict(zip(("prompt", "reused", "read", "ms"), map(int, m[-1])))


rows = [json.loads(l) for l in open("bench-data/s-C-1.requests.jsonl")]
r = rows[12]
from_cm = json.load(open("bench-data/canon12.json"))      # the messages as ctxcache rewrote them (reorder on)
msgs, tools = from_cm, r["tools"]
nxt = json.loads(json.dumps(msgs)) + [{"role": "user", "content": "Continue."}]

import sys
save_pin = sys.argv[1] == "1"
vm("sudo /usr/local/bin/strata-restart")
print(f"cold pin={save_pin}", chat(msgs, tools, pin=save_pin))
print("save   ", post("/slots/0?action=save", {"filename": "pintest.bin"}).get("n_saved"))
vm("sudo /usr/local/bin/strata-restart")
print("restore", post("/slots/0?action=restore", {"filename": "pintest.bin"}).get("n_restored"))
print("next pin=False", chat(nxt, tools, pin=False))
