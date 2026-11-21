#!/bin/bash
# strata-watchdog: keeps the Strata server alive (restarts it cleanly after a GPU reset or a crash).
# See WATCHDOG.md. Every setting below can be overridden in /etc/default/strata-watchdog.
SERVE_DIR=/opt/Strata/terminal    # Strata checkout, its terminal/ folder
CONFIG=strata-128k.json           # Strata config file, relative to SERVE_DIR
PORT=8080
SERVER_EXTRA=""        # extra server.py flags, e.g. --slot-save-path DIR
SERVER_OUT=/var/log/strata-server.out
LOG=/var/log/strata-watchdog.log
INTERVAL=30            # seconds between checks
FAIL_LIMIT=6           # consecutive failed checks before a restart (6 x 30 s = 3 min)
GRACE=300              # seconds after a (re)start during which no restart is done
[ -f /etc/default/strata-watchdog ] && . /etc/default/strata-watchdog

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
resets() { dmesg | grep -c "GPU reset(" ; }
server_pids() { pgrep -f "python serve/server.py .*--config $CONFIG" ; }
healthy() { curl -s --max-time 10 "http://127.0.0.1:$PORT/v1/models" | grep -q '"loaded"' ; }
restart_loop() { tail -n 40 "$SERVER_OUT" 2>/dev/null | grep -q "the engine did not start" ; }

start_server() {
    cd "$SERVE_DIR" || return
    setsid .venv/bin/python serve/server.py --engine strata --config "$CONFIG" --port "$PORT" --no-api-key $SERVER_EXTRA \
        > "$SERVER_OUT" 2>&1 < /dev/null &
    STARTED=$(date +%s); FAILS=0; R_LAST=$(resets)
    log "started server (pid $!)"
}
stop_all() {
    for p in $(pgrep -f "python serve/server.py"); do kill "$p"; done
    for i in $(seq 1 60); do pgrep -f "/strata --serve" > /dev/null || break; sleep 1; done
    pkill -9 -f "/strata --serve"; sleep 2
}
full_restart() {
    log "RESTART: $1"
    cp "$SERVER_OUT" "$SERVER_OUT.crash-$(date +%m%d-%H%M)" 2>/dev/null
    ls -1t "$SERVER_OUT".crash-* 2>/dev/null | tail -n +6 | xargs -r rm -f   # keep the last 5
    stop_all
    sync; echo 3 > /proc/sys/vm/drop_caches
    sleep 5
    start_server
}

log "watchdog up (config $CONFIG, port $PORT)"
STARTED=$(date +%s); FAILS=0; R_LAST=$(resets)
if server_pids > /dev/null; then log "server already running: adopted"; else start_server; fi

while sleep "$INTERVAL"; do
    now=$(date +%s); r=$(resets)
    if ! server_pids > /dev/null; then full_restart "server process gone"; continue; fi
    if healthy; then
        [ "$FAILS" -gt 0 ] && log "healthy again after $FAILS failed checks"
        FAILS=0; R_LAST=$r; continue
    fi
    FAILS=$((FAILS + 1))
    [ $((now - STARTED)) -lt "$GRACE" ] && continue               # still starting up
    if [ "$r" -gt "$R_LAST" ] && restart_loop; then
        full_restart "GPU reset ($((r - R_LAST)) new) and the engine cannot start"; continue
    fi
    [ "$FAILS" -ge "$FAIL_LIMIT" ] && full_restart "no loaded model for $FAILS checks"
done
