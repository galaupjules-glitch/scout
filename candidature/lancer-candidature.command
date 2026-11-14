#!/bin/bash
# Double-clique : lance le site de candidature + un lien public https a envoyer aux candidats.
cd "$(dirname "$0")"
clear
python3 serveur.py &
SERVEUR=$!
trap "kill $SERVEUR 2>/dev/null" EXIT
sleep 1
open "http://127.0.0.1:8790/admin#$(cat donnees/cle_admin.txt)"
if command -v cloudflared >/dev/null; then
  echo "Creation du lien public... le lien a envoyer aux candidats va s'afficher ci-dessous (https://....trycloudflare.com)"
  cloudflared tunnel --url http://localhost:8790 2>&1 | grep --line-buffered -o 'https://[a-z0-9-]*\.trycloudflare\.com'
else
  echo "Pour avoir un lien a envoyer aux candidats, installe cloudflared une fois :  brew install cloudflared"
  echo "puis relance ce fichier. (Laisse cette fenetre ouverte, ferme-la pour arreter le site.)"
  wait $SERVEUR
fi
