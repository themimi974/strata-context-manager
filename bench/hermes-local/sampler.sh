#!/bin/bash
# Samples the benchmark every 5 minutes while Hermes runs; appends to gltf-timeline.jsonl; writes alerts.log.
# Needs STRATA_VM_SSH in the environment and a file strata_log_start holding the Strata log line where the task began. Alerts: a call repeated 8x in a row, a failing call repeated 4x,
# changes in crates/strata-core|strata-defs|strata-content, 45 min without a commit after the first hour.
cd "$(dirname "$0")"
START=$(date +%s)
while true; do
  OUT=$(python3 collect.py "You are working on paraworld-remake" "$(cat strata_log_start)" 2>/dev/null | tail -1)
  [ -n "$OUT" ] && echo "$OUT" >> gltf-timeline.jsonl
  python3 - "$OUT" "$START" <<'PY' >> alerts.log
import json, sys, time
try: d = json.loads(sys.argv[1])
except Exception: sys.exit()
h, g = d.get("hermes", {}), d.get("git", {})
a = []
if h.get("max_same_call_in_a_row", 0) >= 8: a.append(f"LOOP: same call {h['max_same_call_in_a_row']}x in a row: {h.get('max_same_call_in_a_row_what')}")
if any(n >= 4 for n, _ in h.get("failing_calls_repeated_2plus", [])): a.append(f"LOOP: failing call repeated: {h['failing_calls_repeated_2plus'][0]}")
bad = [f for f in g.get("changed_files", []) if f.startswith(("crates/strata-core", "crates/strata-defs", "crates/strata-content"))]
if bad: a.append(f"RULE: forbidden paths changed: {bad}")
if time.time() - int(sys.argv[2]) > 3600 and g.get("commits", 0) == 0 and g.get("uncommitted_files", 0) == 0: a.append("STALL: no change after an hour")
for x in a: print(d["at"], x)
PY
  pgrep -f "hermes.* chat --oneshot" > /dev/null || { echo "$(date '+%F %T') hermes process ended" >> alerts.log; break; }
  sleep 300
done
