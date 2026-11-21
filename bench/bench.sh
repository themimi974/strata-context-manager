#!/bin/bash
# ctxcache A/B benchmark driver (runs on the Proxmox host).
#   bench.sh session ARM REP     Hermes writes the infra docs from a cold engine (ARM = B passthrough | C full ctxcache)
#   bench.sh resume  ARM REP     continue that session after an engine restart ("next morning" / GPU reset)
# Each run writes runs/<name>.{meta,engine.log,hermes.out} on the host; ctxcache rows are pulled afterwards.
set -u
D=${BENCH_DIR:-$(cd "$(dirname "$0")" && pwd)}; mkdir -p $D/runs
VM="${STRATA_SSH:-ssh root@strata-host}"     # how to run a command on the Strata machine
ELOG=${STRATA_ENGINE_LOG:-/var/log/strata-engine.log}   # "log" in the Strata config
DC="cd /opt/litellm && docker compose -f docker-compose.yml -f compose.ctxcache.yml"
TASK='You are documenting my home lab. The folder ~/infra/bundle holds configuration files exported from each machine (Proxmox host, VM 102 running the Strata inference server on an AMD MI100, CT 105 running LiteLLM + PostgreSQL + the ctxcache Context Manager, CT 104 running you, Hermes). Read EVERY file in it with your file tools, one at a time. Then write ~/infra/docs/INFRASTRUCTURE.md containing: 1) an overview with a mermaid diagram of machines, services and the request path; 2) one section per machine/service with IPs, ports, resources and key settings; 3) an operations runbook (start/stop order, watchdog, logs, where state lives); 4) known issues and limits. Use only facts from the files and name the file each fact comes from.'
FOLLOWUP='Now add a section "Troubleshooting" to ~/infra/docs/INFRASTRUCTURE.md: for each failure the files mention (GPU reset, engine restart loop, devices stuck in D3cold, Context Manager down, snapshot folder full) give the symptom, how to confirm it and the fix, citing the files. Re-read files if you need exact values.'

now() { date +%s.%N; }
ts() { TZ=Europe/Paris date '+%H:%M:%S'; }

set_arm() {   # $1 = B|C, $2 = record file name
  local env
  case $1 in
    B) env="CTX_MODE=passthrough CTX_REORDER=0 CTX_PIN_SYSTEM=0" ;;
    C) env="CTX_MODE=snapshot CTX_REORDER=1 CTX_PIN_SYSTEM=0" ;;   # pin off: Strata bug, a slot saved from a pinned request restores only the pinned prefix
  esac
  pct exec 105 -- bash -c "cd /opt/litellm && $env CTX_RECORD=/bench/$2.jsonl docker compose -f docker-compose.yml -f compose.ctxcache.yml up -d ctxcache >/dev/null 2>&1; sleep 6; curl -s localhost:8090/health"
  echo
}

engine_restart() {   # fresh Strata process, same as a GPU reset / reboot; $1 = 1 to also drop the page cache
  $VM "sudo /usr/local/bin/strata-restart $([ "${1:-0}" = 1 ] && echo drop)"
}

forget_snapshots() {   # cold start for both arms: no snapshot files, no snapshot rows
  $VM "sudo find /nvme/strata-slots -name '*.bin' -delete"
  pct exec 105 -- bash -c "$DC exec -T db psql -U litellm -d ctxcache -qc 'DELETE FROM kv_snapshot;'"
}

warm_expert_cache() {  # one throwaway request so both arms start with the same expert cache, then a fresh process
  $VM "curl -s -m 300 localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"x\",\"messages\":[{\"role\":\"user\",\"content\":\"Write a haiku about servers.\"}],\"max_tokens\":64}' >/dev/null"
}

run_hermes() {   # $1 = name, $2 = extra hermes args, $3 = prompt
  local name=$1 off t0 t1
  off=$($VM "sudo stat -c %s $ELOG")
  echo "start $(ts) epoch $(date +%s)" >> $D/runs/$name.meta
  t0=$(now)
  pct exec 104 -- su - hermes -c "cd ~/infra && timeout 3600 hermes chat $2 --oneshot -Q -q $(printf %q "$3")" > $D/runs/$name.hermes.out 2>&1
  t1=$(now)
  echo "end $(ts) epoch $(date +%s) wall $(python3 -c "print(round($t1 - $t0, 1))")" >> $D/runs/$name.meta
  $VM "sudo tail -c +$((off + 1)) $ELOG" > $D/runs/$name.engine.log
  grep -o 'session_id: [^ ]*' $D/runs/$name.hermes.out | tail -1 >> $D/runs/$name.meta
  echo "$name: wall $(python3 -c "print(int($t1 - $t0))")s, $(grep -c '^strata serve: prompt' $D/runs/$name.engine.log) engine requests"
}

case $1 in
  session)
    ARM=$2; REP=$3; name=s-$ARM-$REP
    rm -f $D/runs/$name.*
    pct exec 104 -- bash -c 'rm -rf /home/hermes/infra/docs; mkdir -p /home/hermes/infra/docs; chown hermes:hermes /home/hermes/infra/docs'
    set_arm $ARM $name
    forget_snapshots
    engine_restart 0; warm_expert_cache; engine_restart 0
    echo "arm $ARM session rep $REP" >> $D/runs/$name.meta
    run_hermes $name "" "$TASK"
    pct exec 104 -- bash -c "cp /home/hermes/infra/docs/INFRASTRUCTURE.md /tmp/$name.md 2>/dev/null" && pct pull 104 /tmp/$name.md $D/runs/$name.INFRASTRUCTURE.md 2>/dev/null
    ;;
  resume)
    ARM=$2; REP=$3; name=r-$ARM-$REP; src=s-$ARM-$REP
    sid=$(grep -o 'session_id: [^ ]*' $D/runs/$src.meta | tail -1 | cut -d' ' -f2)
    rm -f $D/runs/$name.*
    set_arm $ARM $name
    sleep 20                                    # let the background snapshot save of the last call finish
    engine_restart 0                            # the engine forgets everything (GPU reset / next morning)
    echo "arm $ARM resume of $sid rep $REP" >> $D/runs/$name.meta
    run_hermes $name "--resume $sid" "$FOLLOWUP"
    pct exec 104 -- bash -c "cp /home/hermes/infra/docs/INFRASTRUCTURE.md /tmp/$name.md 2>/dev/null" && pct pull 104 /tmp/$name.md $D/runs/$name.INFRASTRUCTURE.md 2>/dev/null
    ;;
esac
