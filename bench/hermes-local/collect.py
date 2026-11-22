#!/usr/bin/env python3
"""Benchmark collector for a Hermes-local task (host side). One JSON line per run on stdout, appended to a timeline.
Sources: Hermes' state.db (tool calls, loops), the gateway's request_log (tokens, cache, latency), Strata's log
(read/generation speed, file-cache events) and git (progress). Needs `pct` and `ssh` access to the lab; prints no secrets."""
import json
import os
import re
import statistics
import subprocess
import sys
import time

CT_HERMES, CT_GATEWAY = os.environ.get("CT_HERMES", "104"), os.environ.get("CT_GATEWAY", "105")
# How to run a command on the Strata VM, e.g. STRATA_VM_SSH="ssh -o StrictHostKeyChecking=no user@host" (keys or sshpass: your choice)
VM = os.environ["STRATA_VM_SSH"].split()
STRATA_LOG = os.environ.get("STRATA_LOG", "/root/Strata-data/bench/ctx-strata-iq2xs.log")
MARKER = sys.argv[1] if len(sys.argv) > 1 else "You are working on paraworld-remake"
STRATA_LOG_START = int(sys.argv[2]) if len(sys.argv) > 2 else 0     # line number where the task began


def sh(cmd, inp=None, timeout=120):
    r = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def hermes():
    t = sh(["pct", "exec", CT_HERMES, "--", "python3", "/home/hermes/hermes_side.py", MARKER])
    for line in reversed(t.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"error": "no output"}


def gateway(since):
    q = ("select count(*), coalesce(sum(prompt_tokens),0), coalesce(sum(cached_tokens),0), coalesce(sum(completion_tokens),0),"
         " coalesce(avg(latency_ms),0), coalesce(percentile_cont(0.5) within group (order by ttft_ms),0),"
         " coalesce(percentile_cont(0.95) within group (order by ttft_ms),0), coalesce(max(prompt_tokens),0),"
         " count(restored_chain), count(saved_chain), coalesce(sum(restore_ms),0), coalesce(sum(save_ms),0)"
         f" from request_log where at >= to_timestamp({since})")
    cmd = ["pct", "exec", CT_GATEWAY, "--", "bash", "-c",
           "cd /opt/strata-context-manager/deploy && docker compose exec -T db sh -c "
           "'psql -U \"$POSTGRES_USER\" -d context -At -F \"|\" -c \"" + q + "\"'"]
    row = [x for x in sh(cmd).strip().splitlines() if "|" in x][-1].split("|")
    n, pt, ct, comp, lat, t50, t95, pmax, rest, save, rms, sms = [float(x) for x in row]
    return {"requests": int(n), "prompt_tokens": int(pt), "cached_tokens": int(ct),
            "reuse_pct": round(100 * ct / pt, 1) if pt else None, "completion_tokens": int(comp),
            "avg_latency_s": round(lat / 1000, 1), "ttft_p50_s": round(t50 / 1000, 2), "ttft_p95_s": round(t95 / 1000, 2),
            "max_prompt_tokens": int(pmax), "restores": int(rest), "saves": int(save)}


def strata():
    text = sh(VM + [f"sudo tail -n +{STRATA_LOG_START + 1} {STRATA_LOG}"], timeout=180)
    pat = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms.*?, (\d+) generated in (\d+) ms")
    reqs = read = read_ms = gen = gen_ms = 0
    for m in pat.finditer(text):
        reqs += 1
        read += int(m[3]); read_ms += int(m[4]); gen += int(m[5]); gen_ms += int(m[6])
    return {"requests": reqs, "tokens_read": read, "read_s": round(read_ms / 1000),
            "read_tok_s": round(read / (read_ms / 1000)) if read_ms else None,
            "tokens_generated": gen, "gen_s": round(gen_ms / 1000), "gen_tok_s": round(gen / (gen_ms / 1000), 1) if gen_ms else None,
            "file_captures": len(re.findall(r"seg capture .*: written", text)),
            "file_loads": len(re.findall(r"segc compose .*: done", text)),
            "file_fallbacks": len(re.findall(r"segc compose .*read normally", text)),
            "gpu_resets": len(re.findall(r"GPU reset", text))}


def git():
    script = ("cd ~/paraworld-remake && echo BR $(git branch --show-current) && echo AHEAD $(git rev-list --count master..HEAD) && "
              "echo STAT $(git diff --shortstat master) && echo DIRTY $(git status --short | wc -l) && "
              "git diff --name-only master | sed 's/^/FILE /' | head -40 && git ls-files --others --exclude-standard | sed 's/^/FILE /' | head -40")
    lines = sh(["pct", "exec", CT_HERMES, "--", "su", "-", "hermes", "-c", script]).splitlines()
    get = lambda k: next((l[len(k) + 1:].strip() for l in lines if l.startswith(k + " ")), "")
    return {"branch": get("BR"), "commits": int(get("AHEAD") or 0), "diff": get("STAT"),
            "uncommitted_files": int(get("DIRTY") or 0), "changed_files": [l[5:] for l in lines if l.startswith("FILE ")]}


if __name__ == "__main__":
    h = hermes()
    since = int(h.get("started_at", time.time())) - 5
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "hermes": h, "gateway": gateway(since) if "started_at" in h else {},
           "strata": strata(), "git": git()}
    print(json.dumps(rec))
