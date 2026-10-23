#!/bin/bash
# Double-clique ce fichier : ca ouvre le Spoofer video dans ton navigateur.
cd "$(dirname "$0")"
clear
if curl -s -o /dev/null http://127.0.0.1:8766/; then
  open "http://127.0.0.1:8766/"
  exit 0
fi
echo "Ouverture du Spoofer video dans ton navigateur..."
echo "(Laisse cette fenetre ouverte tant que tu utilises l'app. Ferme-la pour quitter.)"
( sleep 1 && open "http://127.0.0.1:8766/" ) &
python3 video_spoofer_web.py
