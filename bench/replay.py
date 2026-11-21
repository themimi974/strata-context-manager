#!/usr/bin/env python3
"""Replay recorded Hermes requests (ctxcache CTX_RECORD jsonl) and measure the engine's prefill per request.

  replay.py nonce   REC OUT          straight to Strata, a random line first in the system prompt: nothing reusable
  replay.py litellm REC OUT [DATE]   through LiteLLM (ctxcache hook path); DATE replaces Hermes' date line ("next day")

Generation is capped at 1 token: only prompt reading is measured. The prefill of each request is read from Strata's
engine log ("strata serve: prompt N tokens = R reused + X read in T ms"), restore time from ctxcache's request_log."""
import json, os, os, re, subprocess, sys, time, urllib.request, uuid

VM = os.environ.get("STRATA_SSH", "ssh root@strata-host").split()      # how to run a command on the Strata machine
ELOG = os.environ.get("STRATA_ENGINE_LOG", "/var/log/strata-engine.log")   # "log" in the Strata config
LINE = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms")
DATE = re.compile(r"^(Conversation started: ).*$", re.M)


def log_size():
    return int(subprocess.check_output(VM + [f"sudo stat -c %s {ELOG}"]).strip())


def log_since(off):
    return subprocess.check_output(VM + [f"sudo tail -c +{off + 1} {ELOG}"]).decode(errors="replace")


def post(url, body, key=None):
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, json.dumps(body).encode(), h)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as r:
        r.read()
    return time.time() - t0


def main():
    mode, rec, out = sys.argv[1], sys.argv[2], sys.argv[3]
    date = sys.argv[4] if len(sys.argv) > 4 else None
    key = os.environ.get("LITELLM_KEY")
    rows = [json.loads(l) for l in open(rec)]
    with open(out, "w") as f:
        for i, r in enumerate(rows):
            msgs = json.loads(json.dumps(r["messages"]))
            if mode == "nonce" and msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = f"[run {uuid.uuid4().hex}]\n" + msgs[0]["content"]
            if date and msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = DATE.sub(lambda m: m.group(1) + date, msgs[0]["content"])
            body = {"model": "qwen3.8-flash-next" if mode == "litellm" else "x", "messages": msgs, "max_tokens": 1,
                    "temperature": 0}
            if r.get("tools"):
                body["tools"] = r["tools"]
            off = log_size()
            url = (os.environ.get("LITELLM_URL", "http://gateway:4000") + "/v1/chat/completions" if mode == "litellm"
                   else os.environ.get("STRATA_URL", "http://strata-host:8080") + "/v1/chat/completions")
            wall = post(url, body, key if mode == "litellm" else None)
            time.sleep(1.0)
            m = LINE.findall(log_since(off))
            prompt, reused, read, ms = map(int, m[-1]) if m else (None, None, None, None)
            row = {"i": i, "prompt": prompt, "reused": reused, "read": read, "prefill_ms": ms, "wall_s": round(wall, 2)}
            f.write(json.dumps(row) + "\n"); f.flush()
            print(row, flush=True)


if __name__ == "__main__":
    main()
