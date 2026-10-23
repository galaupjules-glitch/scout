#!/bin/bash
# Réinstalle OpenCLI 1.8.8 + son extension Chrome depuis cette sauvegarde,
# sans internet et sans dépendre de npm ni de GitHub.
set -e
ICI="$(cd "$(dirname "$0")" && pwd)"

if ! command -v node >/dev/null; then
  echo "ERREUR : Node.js n'est pas installé (il faut node >= 20.18.1)."
  read -p "Entrée pour fermer"; exit 1
fi

# Dossier global de node (là où npm installe les outils)
GLOBAL="$(npm root -g)"
BIN="$(dirname "$(dirname "$GLOBAL")")/bin"

echo "→ Installation d'OpenCLI dans $GLOBAL/@jackwener/opencli"
mkdir -p "$GLOBAL/@jackwener"
rm -rf "$GLOBAL/@jackwener/opencli"
tar -xzf "$ICI/opencli-1.8.8-complet.tgz" -C "$GLOBAL/@jackwener"
chmod +x "$GLOBAL/@jackwener/opencli/dist/src/main.js"
ln -sf "$GLOBAL/@jackwener/opencli/dist/src/main.js" "$BIN/opencli"

echo "→ Extension Chrome dans ~/Documents/opencli-extension"
rm -rf ~/Documents/opencli-extension
tar -xzf "$ICI/extension-chrome-1.0.24.tgz" -C ~/Documents

echo
echo "OK : $("$BIN/opencli" --version)"
echo
echo "Dernière étape (si l'extension n'est plus dans Chrome) :"
echo "  chrome://extensions → Mode développeur → Charger l'extension non empaquetée"
echo "  → choisir ~/Documents/opencli-extension"
read -p "Entrée pour fermer"
