#!/bin/bash
# Spustí aktualizaci webu v GitHub Actions (workflow_dispatch). Volá launchd z Macu každou hodinu,
# protože GitHub plánované běhy (cron) často vynechává – reálně jich proběhnou jen ~4 z 20 denně.
# Běží jen 6:00–1:00 pražského času a nespouští nic, když poslední běh začal před méně než 40 minutami.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
REPO="Hrivasek/tenis-predikce"
h=$(TZ=Europe/Prague date +%-H)
if [ "$h" -ge 2 ] && [ "$h" -le 5 ]; then exit 0; fi
last=$(gh run list -R "$REPO" --workflow update.yml --limit 1 --json createdAt --jq '.[0].createdAt' 2>/dev/null)
if [ -n "$last" ]; then
  age=$(( $(date -u +%s) - $(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$last" +%s) ))
  if [ "$age" -lt 2400 ]; then echo "$(date '+%F %T') poslední běh před $((age / 60)) min – nespouštím"; exit 0; fi
fi
full=false
case "$h" in 6|12|18) full=true ;; esac
gh workflow run update.yml -R "$REPO" -f full=$full && echo "$(date '+%F %T') spuštěno (full=$full)"
