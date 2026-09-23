#!/bin/bash
# Nainstaluje plánovanou úlohu (launchd) pro stahování z Tennis Abstract v 8:00 a 20:00.
# Pokud Mac v tu dobu spí, launchd úlohu spustí po probuzení; pokud je vypnutý, další běh
# dožene nedokončené turnaje za poslední 3 týdny (delší výpadek: ta_sync.sh --backfill-from RRRR-MM-DD).
#   ./scripts/install_launchd.sh            # instalace / aktualizace
#   ./scripts/install_launchd.sh --remove   # odinstalace
set -euo pipefail
LABEL="cz.hrivasek.tenis-predikce.tennisabstract"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WORK="$HOME/Library/Application Support/tenis-predikce"
SCRIPT="$WORK/ta_sync.sh"
LOG="$HOME/Library/Logs/tenis-predikce-tennisabstract.log"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$PLIST"; echo "Odinstalováno."; exit 0
fi

mkdir -p "$WORK" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
# skript kopírujeme mimo pracovní kopii – launchd tak nezávisí na tom, kde máš projekt
cp "$(cd "$(dirname "$0")" && pwd)/ta_sync.sh" "$SCRIPT"
chmod +x "$SCRIPT"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$SCRIPT</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Nainstalováno: $PLIST"
echo "Log: $LOG"
echo "Ruční spuštění: launchctl kickstart gui/$(id -u)/$LABEL"
