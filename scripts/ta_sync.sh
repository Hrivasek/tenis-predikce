#!/bin/bash
# Stáhne challengery / WTA 125 a Elo žebříčky z Tennis Abstract a pushne je do repozitáře.
# Spouští launchd (scripts/install_launchd.sh) 2× denně. Pracuje ve vlastním klonu repozitáře,
# aby nezasahoval do rozdělané práce v tvé pracovní kopii.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_URL="https://github.com/Hrivasek/tenis-predikce.git"
WORK="$HOME/Library/Application Support/tenis-predikce"
REPO="$WORK/repo"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
mkdir -p "$WORK"
if [ ! -d "$REPO/.git" ]; then
  git clone -q "$REPO_URL" "$REPO"
fi
cd "$REPO"
git pull -q --rebase origin main

python3 tennisabstract.py "$@"

git add data/ta_matches.csv data/ta_elo_atp.csv data/ta_elo_wta.csv data/ta/ 2>/dev/null || true
if git diff --cached --quiet; then
  echo "Beze změn."
  exit 0
fi
git -c user.name="tenis-predikce (Mac)" -c user.email="74265384+Hrivasek@users.noreply.github.com" \
  commit -q -m "Tennis Abstract $(date '+%Y-%m-%d %H:%M')"
for i in 1 2 3 4 5; do
  if git pull -q --rebase origin main && git push -q origin HEAD:main; then
    echo "Pushnuto."
    exit 0
  fi
  sleep $((i * 10))
done
echo "Push se nepovedl." >&2
exit 1
