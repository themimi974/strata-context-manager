# Engine-side tools (the Strata machine)

Optional, but used in the reference setup. Run as root.

| File | What it does |
|---|---|
| `strata-watchdog.sh` + `strata-watchdog.service` | keeps the Strata server alive; after a GPU reset it stops everything, drops the page cache and starts fresh ([WATCHDOG.md](WATCHDOG.md)) |
| `strata-slots-janitor.sh` | deletes orphaned snapshot files (no record points to them, e.g. after a database reset), and keeps the folder under a size budget, lowest-value snapshots first |
| `strata-restart.sh` | clean stop + start of Strata through the watchdog, waits until the model is loaded |

## Snapshot folder

Give Strata a folder on a fast disk; the watchdog passes extra server flags through `SERVER_EXTRA`:

```bash
mkdir -p /nvme/strata-slots
cat > /etc/default/strata-watchdog <<'EOF'
SERVE_DIR=/opt/Strata/terminal
CONFIG=strata-128k.json
SERVER_EXTRA="--slot-save-path /nvme/strata-slots"
EOF
```

## Install

```bash
install -m 755 strata-watchdog.sh     /usr/local/bin/strata-watchdog
install -m 755 strata-slots-janitor.sh /usr/local/bin/strata-slots-janitor
install -m 755 strata-restart.sh       /usr/local/bin/strata-restart
install -m 644 strata-watchdog.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now strata-watchdog

# janitor settings and token (the same value as CTX_ADMIN_TOKEN on the gateway)
cat > /etc/default/strata-context <<'EOF'
SLOT_DIR=/nvme/strata-slots
BUDGET_GB=80
CONTEXT_MANAGER_URL=http://gateway:8090
EOF
install -m 600 /dev/null /etc/strata-context-token && echo 'the-CTX_ADMIN_TOKEN-value' > /etc/strata-context-token
echo '*/10 * * * * root /usr/local/bin/strata-slots-janitor >> /var/log/strata-slots-janitor.log 2>&1' \
  > /etc/cron.d/strata-slots-janitor
```

## Notes

* The watchdog was written for a passed-through MI100 whose driver occasionally faults; on a stable machine it simply
  restarts Strata if it dies.
* If you use your own service manager for Strata, skip the watchdog; `strata-restart.sh` assumes it.
* Process patterns: `strata-restart.sh` looks for `/strata --serve` (override with `ENGINE_PATTERN`). Never run
  `pkill -f` with a pattern that also appears in your own command line: it kills the shell running it.
