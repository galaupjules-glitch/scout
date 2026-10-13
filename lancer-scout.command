#!/bin/bash
# Double-clique ce fichier : ca ouvre l'application Scout dans ton navigateur.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.local/node-v20.18.0-darwin-x64/bin:$PATH"
clear
echo "Ouverture de Scout dans ton navigateur..."
echo "(Laisse cette fenetre ouverte tant que tu utilises l'app. Ferme-la pour quitter.)"
python3 app.py
