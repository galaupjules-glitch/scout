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
  --add-data "$(pwd)/templates:templates" --add-data "$(pwd)/static:static" \
  vexa_app.py >/dev/null

# Le SDK installé ici cible la toute dernière version de macOS par défaut
# (ex: macOS 26) : sans -target explicite, le binaire Swift refuse de se
# lancer sur n'importe quel Mac qui n'a pas CETTE version précise. On force
# donc une cible beaucoup plus ancienne (14.0 = la même limite que numpy,
# le composant le plus exigeant du serveur Python embarqué — pas la peine
# de viser plus bas, ça ne marcherait pas de toute façon).
CIBLE_MIN="14.0"

echo "2/4 — Compilation de la fenêtre native (Swift, cible macOS $CIBLE_MIN+)..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -target "arm64-apple-macos$CIBLE_MIN" \
  vexa-native/app/main.swift -o "$APP/Contents/MacOS/Vexa" -framework Cocoa -framework WebKit

echo "3/4 — Assemblage (serveur + icône + Info.plist)..."
cp -R dist-serveur/Vexa "$APP/Contents/Resources/vexa_server"
cp vexa_icon.icns "$APP/Contents/Resources/AppIcon.icns"

# Identifiant de build affiché en petit dans l'interface : permet de
# vérifier à coup sûr, en testant, qu'on a bien la dernière version et pas
# une ancienne copie restée sur la machine.
date "+%d/%m %H:%M" > "$APP/Contents/Resources/build_info.txt"

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
  <key>LSMinimumSystemVersion</key><string>$CIBLE_MIN</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

echo "4/4 — Signature ad-hoc..."
codesign --force --deep -s - "$APP"
rm -rf dist-serveur build

echo "Terminé : $APP"
