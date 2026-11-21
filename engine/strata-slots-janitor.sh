#!/bin/bash
# strata-slots-janitor: keep Strata's snapshot folder (--slot-save-path) under a size budget.
# Run it from cron on the Strata machine, e.g. every 10 minutes:
#   */10 * * * * root /usr/local/bin/strata-slots-janitor >> /var/log/strata-slots-janitor.log 2>&1
# Settings: /etc/default/strata-context (or the environment)
#   SLOT_DIR=/nvme/strata-slots            the folder given to Strata with --slot-save-path
#   BUDGET_GB=80                           size to keep it under
#   CONTEXT_MANAGER_URL=http://gateway:8090
#   TOKEN_FILE=/etc/strata-context-token   holds CTX_ADMIN_TOKEN (chmod 600)
# Order: the Context Manager's value ranking (P(reuse) x recompute cost / size; pinned kept). If the Context Manager
# cannot be reached, oldest files go first.
[ -f /etc/default/strata-context ] && . /etc/default/strata-context
DIR=${SLOT_DIR:-/nvme/strata-slots}; BUDGET_GB=${BUDGET_GB:-80}
CM=${CONTEXT_MANAGER_URL:-http://127.0.0.1:8090}; TOKEN=$(cat "${TOKEN_FILE:-/etc/strata-context-token}" 2>/dev/null)
[ -d "$DIR" ] || { echo "no folder $DIR"; exit 1; }
total=$(du -sb "$DIR" | cut -f1); limit=$((BUDGET_GB * 1024 * 1024 * 1024))
[ "$total" -le "$limit" ] && exit 0
order=$(curl -sf --max-time 10 -H "X-Ctx-Token: $TOKEN" "$CM/v1/snapshots/eviction-order" \
        | python3 -c "import json,sys; print('\n'.join(x['location'] for x in json.load(sys.stdin)['order']))" 2>/dev/null)
[ -z "$order" ] && order=$(ls -1tr "$DIR" | grep '\.bin$')            # fallback: oldest first
gone=()
while read -r f; do
  [ "$total" -le "$limit" ] && break
  [ -n "$f" ] && [ -f "$DIR/$f" ] || continue
  sz=$(stat -c %s "$DIR/$f"); rm -f "$DIR/$f"; total=$((total - sz)); gone+=("$f")
  echo "$(date '+%F %T') evicted $f ($sz bytes)"
done <<< "$order"
if [ ${#gone[@]} -gt 0 ]; then
  body=$(printf '%s\n' "${gone[@]}" | python3 -c "import json,sys; print(json.dumps({'locations': sys.stdin.read().split()}))")
  curl -sf --max-time 10 -H "X-Ctx-Token: $TOKEN" -H "Content-Type: application/json" -d "$body" "$CM/v1/snapshots/evicted" >/dev/null
fi
