#!/usr/bin/env python3
"""Summarise benchmark runs: engine prefill (from the engine log slice), ctxcache restores/saves and TTFT (request_log).

  analyze.py RUN [RUN ...]      RUN = runs/<name> prefix (s-C-1, r-B-1, ...) or a replay .jsonl"""
import json, re, subprocess, sys

LINE = re.compile(r"strata serve: prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms(?:.*?, (\d+) generated in (\d+) ms)?")


def db(sql):
    cmd = ["pct", "exec", "105", "--", "bash", "-c",
           "cd /opt/litellm && docker compose -f docker-compose.yml -f compose.ctxcache.yml exec -T db "
           f"psql -U litellm -d ctxcache -At -F '|' -c \"{sql}\""]
    return [l.split("|") for l in subprocess.check_output(cmd).decode().splitlines() if l]


def live(name):
    meta = open(f"bench-data/runs/{name}.meta").read()
    e0 = int(re.search(r"start \S+ epoch (\d+)", meta).group(1))
    e1 = int(re.search(r"end \S+ epoch (\d+)", meta).group(1))
    wall = float(re.search(r"wall ([\d.]+)", meta).group(1))
    reqs = [tuple(int(x) if x else 0 for x in m) for m in LINE.findall(open(f"bench-data/runs/{name}.engine.log").read())]
    rows = db("SELECT coalesce(restore_ms,0), coalesce(save_ms,0), coalesce(ttft_ms,0), coalesce(prompt_tokens,0), "
              "coalesce(cached_tokens,0), coalesce(pinned::text,'') FROM request_log "
              f"WHERE at BETWEEN to_timestamp({e0}) AND to_timestamp({e1 + 60}) ORDER BY id")
    return summarize(name, reqs, rows, wall)


def summarize(name, reqs, rows, wall=None):
    prompt = sum(r[0] for r in reqs); reused = sum(r[1] for r in reqs); read = sum(r[2] for r in reqs)
    prefill_s = sum(r[3] for r in reqs) / 1000
    gen_s = sum(r[5] for r in reqs if len(r) > 5) / 1000
    restore_s = sum(float(r[0]) for r in rows) / 1000 if rows else 0.0
    save_s = sum(float(r[1]) for r in rows) / 1000 if rows else 0.0
    ttft_s = sum(float(r[2]) for r in rows) / 1000 if rows else None
    out = {"run": name, "requests": len(reqs), "prompt_tokens": prompt, "reused_tokens": reused, "read_tokens": read,
           "reuse_pct": round(100 * reused / prompt, 1) if prompt else 0, "prefill_s": round(prefill_s, 1),
           "restore_s": round(restore_s, 1), "prefill_wait_s": round(prefill_s + restore_s, 1),
           "save_s": round(save_s, 1), "decode_s": round(gen_s, 1), "ttft_sum_s": round(ttft_s, 1) if ttft_s else None,
           "wall_s": wall, "per_request": [{"prompt": r[0], "reused": r[1], "read": r[2], "ms": r[3]} for r in reqs]}
    return out


def replay(path):
    rs = [json.loads(l) for l in open(path)]
    reqs = [(r["prompt"] or 0, r["reused"] or 0, r["read"] or 0, r["prefill_ms"] or 0) for r in rs]
    return summarize(path, reqs, [])


if __name__ == "__main__":
    res = [replay(a) if a.endswith(".jsonl") else live(a) for a in sys.argv[1:]]
    for r in res:
        print({k: v for k, v in r.items() if k != "per_request"})
    json.dump(res, open("bench-data/summary-last.json", "w"), indent=1)
