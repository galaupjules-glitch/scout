#!/bin/bash
# Construit l'app Mac à partir de index.html et l'installe dans /Applications.
# Usage : ./build-app.sh "Nom de l'app"   (à relancer après chaque modification de index.html)
set -euo pipefail
cd "$(dirname "$0")"

NAME="${1:-Infloww}"
# L'utilisateur garde la vraie app Infloww sous le nom "Infloww 2" : notre clone peut donc
# s'appeler "Infloww" tout court, fichier et nom affiché identiques.
FOLDER="${2:-Infloww}"
ID="local.$(echo "$FOLDER" | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]')"
APP="build/$FOLDER.app"
TMP="build/tmp"

rm -rf "$APP" "$TMP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$TMP/icon.iconset"

swiftc -O app/main.swift -o "$APP/Contents/MacOS/app" -framework Cocoa -framework WebKit
cp index.html chart.umd.min.js "$APP/Contents/Resources/"

# Icône : si app/AppIcon.icns existe (icône choisie), on la reprend telle quelle ; sinon on en dessine une
if [ -f app/AppIcon.icns ]; then
  cp app/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
else
  swiftc app/icon.swift -o "$TMP/icon" -framework Cocoa
  "$TMP/icon" "$TMP/icon.png"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/icon.png" --out "$TMP/icon.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$TMP/icon.png" --out "$TMP/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMP/icon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$ID</string>
  <key>CFBundleExecutable</key><string>app</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

codesign --force --deep -s - "$APP"
rm -rf "$TMP"

rm -rf "/Applications/$FOLDER.app"
cp -R "$APP" "/Applications/"
rm -rf "$APP"
echo "Installée : /Applications/$FOLDER.app (affichée comme « $NAME »)"
