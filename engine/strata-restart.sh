#!/bin/bash
# strata-restart [drop]: stop the Strata server and engine, optionally drop the page cache, start the watchdog
# (which starts Strata again), and wait until the model is loaded. Used by the benchmark; handy after a GPU reset.
# Run as root. Settings: /etc/default/strata-watchdog (PORT) ; ENGINE_PATTERN overrides how the engine process is found.
[ -f /etc/default/strata-watchdog ] && . /etc/default/strata-watchdog
PORT=${PORT:-8080}
ENGINE_PATTERN=${ENGINE_PATTERN:-'/strata --serve'}
systemctl stop strata-watchdog
for p in $(pgrep -f '(^|/)[.]venv/bin/python serve/server[.]py'); do kill "$p"; done
# patterns are matched by pgrep/pkill on their own; this script's command line never contains them
for i in $(seq 1 60); do pgrep -f -- "$ENGINE_PATTERN" >/dev/null || break; sleep 1; done
pkill -9 -f -- "$ENGINE_PATTERN"; sleep 2
[ "${1:-}" = drop ] && { sync; echo 3 > /proc/sys/vm/drop_caches; }
systemctl start strata-watchdog
for i in $(seq 1 120); do curl -sf -m3 "localhost:$PORT/v1/models" | grep -q loaded && { echo "engine loaded after $((i*5))s"; exit 0; }; sleep 5; done
echo "engine NOT loaded after 600s"; exit 1
