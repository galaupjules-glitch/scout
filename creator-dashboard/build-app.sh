#!/bin/bash
# Construit l'app Mac à partir de index.html et l'installe dans /Applications.
# Usage : ./build-app.sh "Nom de l'app"   (à relancer après chaque modification de index.html)
set -euo pipefail
cd "$(dirname "$0")"

NAME="${1:-Creator Hub}"
ID="local.$(echo "$NAME" | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]')"
APP="build/$NAME.app"
TMP="build/tmp"

rm -rf "$APP" "$TMP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$TMP/icon.iconset"

swiftc -O app/main.swift -o "$APP/Contents/MacOS/app" -framework Cocoa -framework WebKit
cp index.html "$APP/Contents/Resources/"

# Icône
swiftc app/icon.swift -o "$TMP/icon" -framework Cocoa
"$TMP/icon" "$TMP/icon.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$TMP/icon.png" --out "$TMP/icon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$TMP/icon.png" --out "$TMP/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/icon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

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

rm -rf "/Applications/$NAME.app"
cp -R "$APP" "/Applications/"
echo "Installée : /Applications/$NAME.app"
