#!/bin/bash
# Keeps the file state cache (/nvme/strata-segments, research build) under a budget, least recently used first:
# Strata touches a stored file's directory each time it loads it, so the oldest mtime is the least recently used.
# Cron: */10 * * * * root /usr/local/bin/strata-segments-janitor >> /var/log/strata-segments-janitor.log 2>&1  Settings: /etc/default/strata-context (SEG_DIR, SEG_BUDGET_GB).
[ -f /etc/default/strata-context ] && . /etc/default/strata-context
DIR=${SEG_DIR:-/nvme/strata-segments}; BUDGET_GB=${SEG_BUDGET_GB:-40}
[ -d "$DIR" ] || exit 0
find "$DIR" -maxdepth 1 -name "*.part" -mmin +60 -exec rm -rf {} +     # abandoned captures
total=$(du -sb "$DIR" | cut -f1); limit=$((BUDGET_GB * 1024 * 1024 * 1024))
[ "$total" -le "$limit" ] && exit 0
while read -r t d; do
  [ "$total" -le "$limit" ] && break
  sz=$(du -sb "$d" | cut -f1); rm -rf "$d"; total=$((total - sz))
  echo "$(date '+%F %T') deleted $(basename "$d") ($sz bytes)"
done < <(find "$DIR" -mindepth 1 -maxdepth 1 -type d ! -name "*.part" -printf '%T@ %p\n' | sort -n)
