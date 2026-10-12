#!/bin/bash
# Nainstaluje plánované úlohy (launchd) na Macu:
#  1. Tennis Abstract (scripts/ta_sync.sh) v 8:00 a 20:00 – challengery, WTA 125, Elo žebříčky
#  2. Spouštění aktualizace webu (scripts/trigger_update.sh) každou hodinu v :15 – GitHub plánované
#     běhy často vynechává, tímhle se web aktualizuje, kdykoli je Mac zapnutý
# Pokud Mac v danou chvíli spí, launchd úlohu spustí po probuzení; pokud je vypnutý, další běh
# dožene, co chybí (TA: nedokončené turnaje za 3 týdny; delší výpadek: ta_sync.sh --backfill-from RRRR-MM-DD).
#   ./scripts/install_launchd.sh            # instalace / aktualizace
#   ./scripts/install_launchd.sh --remove   # odinstalace
set -euo pipefail
WORK="$HOME/Library/Application Support/tenis-predikce"
SRC="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$WORK" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

install_agent() {   # label, skript, log, XML s intervaly
  local label="$1" script="$2" log="$3" intervals="$4"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  if [ "${REMOVE:-}" = "1" ]; then rm -f "$plist"; echo "Odinstalováno: $label"; return; fi
  # skript kopírujeme mimo pracovní kopii – launchd tak nezávisí na tom, kde máš projekt
  cp "$SRC/$script" "$WORK/$script"; chmod +x "$WORK/$script"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$WORK/$script</string></array>
  <key>StartCalendarInterval</key>
  $intervals
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "Nainstalováno: $label (log: $log)"
}

[ "${1:-}" = "--remove" ] && REMOVE=1

install_agent "cz.hrivasek.tenis-predikce.tennisabstract" "ta_sync.sh" \
  "$HOME/Library/Logs/tenis-predikce-tennisabstract.log" \
  '<array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  </array>'

install_agent "cz.hrivasek.tenis-predikce.trigger" "trigger_update.sh" \
  "$HOME/Library/Logs/tenis-predikce-trigger.log" \
  '<dict><key>Minute</key><integer>15</integer></dict>'

echo "Ruční spuštění: launchctl kickstart gui/$(id -u)/<label>"
