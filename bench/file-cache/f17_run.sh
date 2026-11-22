#!/bin/bash
# F1.6 on VM 102 (root): stop the live Strata, let the client run the F1 engine on :8082, then restore the live Strata.
set -u
OUT=/nvme/f1/f17-$(date +%m%d-%H%M); mkdir -p /nvme/f1/cap /nvme/f1/comp "$OUT"
cp /root/f1test/f17_client.py /root/f1test/replay_blocks.json "$OUT/"
exec > >(tee -a "$OUT/run.log") 2>&1
PY=/root/Strata/terminal/.venv/bin/python
echo "== $(date) start, OUT=$OUT"
$PY - <<'EOF'
import json
c = json.load(open("/root/Strata/terminal/strata-908-iq2xs-128k.json"))
c["exe"] = "/root/Strata-f1/build-f1/strata"
c["log"] = "/root/Strata-data/bench/f1-engine.log"
c["port"] = 8082
c.setdefault("env", {}).update({"STRATA_SEGCAP_DIR": "/nvme/f1/cap", "STRATA_SEGCOMP_DIR": "/nvme/f1/comp",
                                "STRATA_DUMP_FIRST_LOGITS": "/nvme/f1/first.f32", "STRATA_TRACE": "1",
                                "STRATA_CACHE_MESSAGE_BOUNDARY": "1"})
json.dump(c, open("/root/Strata-f1/terminal/f1-iq2xs-128k.json", "w"), indent=1)
print("config written")
EOF
systemctl stop strata-watchdog
for p in $(pgrep -f '(^|/)[.]venv/bin/python serve/server[.]py'); do kill "$p"; done
for i in $(seq 1 60); do pgrep -f '^/root/Strata[^ ]*/strata --serve' >/dev/null || break; sleep 1; done
pkill -9 -f '^/root/Strata[^ ]*/strata --serve'; sleep 3
echo "live server stopped"
MED=$(find /root/Strata-f1/engine/src/core -name "*.cpp" -size +14k -size -17k | sort | head -1)
DEEP=$(ls /root/Strata-f1/engine/src/kernels/cuda/*.cu | head -40)
echo "medium file: $MED"
$PY /root/f1test/f17_client.py "$OUT" /root/Strata-f1/engine/src/prefill/segcap.cpp "$MED" \
    /root/Strata-f1/engine/src/core/peer_experts.cpp /root/f1test/replay_blocks.json $DEEP
for p in $(pgrep -f '(^|/)[.]venv/bin/python serve/server[.]py'); do kill "$p"; done
for i in $(seq 1 60); do pgrep -f '^/root/Strata[^ ]*/strata --serve' >/dev/null || break; sleep 1; done
pkill -9 -f '^/root/Strata[^ ]*/strata --serve'; sleep 3
systemctl start strata-watchdog
for i in $(seq 1 120); do curl -sf -m3 localhost:8080/v1/models | grep -q loaded && break; sleep 5; done
echo "== $(date) live server back: $(curl -s -m3 localhost:8080/health | head -c 80)"
echo "F17_DONE $OUT"
