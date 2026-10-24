#!/bin/bash
# Construit Vexa.app en vraie application native macOS : une fenêtre
# WKWebView (vexa-native/app/main.swift) qui démarre et affiche son propre
# serveur local empaqueté (vexa_app.py via PyInstaller) — jamais d'onglet
# de navigateur, jamais de dépendance à internet.
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/Vexa.app"
PYINSTALLER="${PYINSTALLER:-$HOME/Library/Python/3.9/bin/pyinstaller}"

echo "1/4 — Construction du serveur Python (PyInstaller)..."
rm -rf build dist-serveur
"$PYINSTALLER" --noconfirm --distpath dist-serveur --specpath build --name Vexa --onedir --console \
  --add-data "templates:templates" --add-data "static:static" \
  vexa_app.py >/dev/null

echo "2/4 — Compilation de la fenêtre native (Swift)..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O vexa-native/app/main.swift -o "$APP/Contents/MacOS/Vexa" -framework Cocoa -framework WebKit

echo "3/4 — Assemblage (serveur + icône + Info.plist)..."
cp -R dist-serveur/Vexa "$APP/Contents/Resources/vexa_server"
cp vexa_icon.icns "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Vexa</string>
  <key>CFBundleDisplayName</key><string>Vexa</string>
  <key>CFBundleIdentifier</key><string>com.ryva.vexa</string>
  <key>CFBundleExecutable</key><string>Vexa</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

echo "4/4 — Signature ad-hoc..."
codesign --force --deep -s - "$APP"
rm -rf dist-serveur build

echo "Terminé : $APP"
