#!/bin/bash
# strata-slots-janitor: keep Strata's snapshot folder (--slot-save-path) clean and under a size budget.
# Run it from cron on the Strata machine, e.g. every 10 minutes:
#   */10 * * * * root /usr/local/bin/strata-slots-janitor >> /var/log/strata-slots-janitor.log 2>&1
# Settings: /etc/default/strata-context (or the environment)
#   SLOT_DIR=/nvme/strata-slots            the folder given to Strata with --slot-save-path
#   BUDGET_GB=80                           size to keep it under
#   ORPHAN_MIN_AGE_MIN=60                  orphan files younger than this are left alone (a save may be in progress)
#   CONTEXT_MANAGER_URL=http://gateway:8090
#   TOKEN_FILE=/etc/strata-context-token   holds CTX_ADMIN_TOKEN (chmod 600)
# Each run:
#   1. if the Context Manager answers: delete orphans, i.e. .bin files no snapshot record points to (for example
#      after a database reset or a model change), once they are older than ORPHAN_MIN_AGE_MIN;
#   2. if the folder is over budget: delete in the Context Manager's value order (P(reuse) x recompute cost / size,
#      pinned kept), or oldest first if it cannot be reached.
[ -f /etc/default/strata-context ] && . /etc/default/strata-context
DIR=${SLOT_DIR:-/nvme/strata-slots}; BUDGET_GB=${BUDGET_GB:-80}; ORPHAN_MIN_AGE_MIN=${ORPHAN_MIN_AGE_MIN:-60}
CM=${CONTEXT_MANAGER_URL:-http://127.0.0.1:8090}; TOKEN=$(cat "${TOKEN_FILE:-/etc/strata-context-token}" 2>/dev/null)
[ -d "$DIR" ] || { echo "no folder $DIR"; exit 1; }

resp=$(curl -sf --max-time 10 -H "X-Ctx-Token: $TOKEN" "$CM/v1/snapshots/eviction-order")
order=""; known=""
if [ -n "$resp" ]; then
  order=$(python3 -c "import json,sys; print('\n'.join(x['location'] for x in json.loads(sys.argv[1])['order']))" "$resp")
  known=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print('\n'.join([x['location'] for x in d['order']] + d.get('keep', [])))" "$resp")
fi
gone=()
delete() {   # $1 = file name, $2 = reason
  local sz; sz=$(stat -c %s "$DIR/$1"); rm -f "$DIR/$1"; gone+=("$1")
  echo "$(date '+%F %T') deleted $1 ($sz bytes, $2)"
}

# 1. orphans (only when the Context Manager answered, so an outage never looks like "nothing is known")
if [ -n "$resp" ]; then
  while read -r f; do
    [ -n "$f" ] || continue
    grep -qxF "$f" <<< "$known" || delete "$f" orphan
  done < <(find "$DIR" -maxdepth 1 -name '*.bin' -mmin "+$ORPHAN_MIN_AGE_MIN" -printf '%f\n')
fi

# 2. budget
total=$(du -sb "$DIR" | cut -f1); limit=$((BUDGET_GB * 1024 * 1024 * 1024))
if [ "$total" -gt "$limit" ]; then
  [ -z "$resp" ] && order=$(ls -1tr "$DIR" | grep '\.bin$')            # fallback: oldest first
  while read -r f; do
    [ "$total" -le "$limit" ] && break
    [ -n "$f" ] && [ -f "$DIR/$f" ] || continue
    sz=$(stat -c %s "$DIR/$f"); delete "$f" budget; total=$((total - sz))
  done <<< "$order"
fi

if [ ${#gone[@]} -gt 0 ] && [ -n "$resp" ]; then
  body=$(printf '%s\n' "${gone[@]}" | python3 -c "import json,sys; print(json.dumps({'locations': sys.stdin.read().split()}))")
  curl -sf --max-time 10 -H "X-Ctx-Token: $TOKEN" -H "Content-Type: application/json" -d "$body" "$CM/v1/snapshots/evicted" >/dev/null
fi
exit 0
