#!/bin/bash
# Double-clique ce fichier : ca ouvre l'application Scout dans ton navigateur.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.local/node-v20.18.0-darwin-x64/bin:$PATH"
# Node/OpenCLI installes via nvm
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"
clear
# Deja en route en arriere-plan (demarrage-auto.command) ? On ouvre juste la page.
if curl -s -o /dev/null http://127.0.0.1:8765/status; then
  open -a "Google Chrome" "http://127.0.0.1:8765/"
  exit 0
fi
echo "Ouverture de Scout dans ton navigateur..."
echo "(Laisse cette fenetre ouverte tant que tu utilises l'app. Ferme-la pour quitter.)"
python3 app.py
