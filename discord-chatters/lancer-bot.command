#!/bin/bash
# Double-clique ce fichier : ca lance le bot de recrutement Discord.
cd "$(dirname "$0")"
clear
python3 -m pip install -q -r requirements.txt
echo "Bot de recrutement en route. (Laisse cette fenetre ouverte. Ferme-la pour arreter le bot.)"
python3 bot.py
