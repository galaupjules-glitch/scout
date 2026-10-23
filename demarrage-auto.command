#!/bin/bash
# Fait tourner l'app Scout en arriere-plan, des l'ouverture de session.
# Le bouton "Lancer la recherche" de la page Vivier sur Claude marche alors a tout moment.
#   Double-clic : active.      ./demarrage-auto.command off : desactive.
ICI="$(cd "$(dirname "$0")" && pwd -P)"
PLIST="$HOME/Library/LaunchAgents/com.scout.app.plist"

if [ "$1" = "off" ]; then
  launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST"
  echo "Demarrage automatique de Scout desactive."; exit 0
fi

PY="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.scout.app</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ICI/app.py</string><string>--fond</string></array>
  <key>WorkingDirectory</key><string>$ICI</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ICI/resultats/app.log</string>
  <key>StandardErrorPath</key><string>$ICI/resultats/app.log</string>
</dict></plist>
EOF
mkdir -p "$ICI/resultats"
launchctl unload "$PLIST" 2>/dev/null; launchctl load "$PLIST"
echo "Scout tourne en arriere-plan sur http://127.0.0.1:8765 (et redemarre a chaque ouverture de session)."
echo "Pour l'arreter : ./demarrage-auto.command off"
