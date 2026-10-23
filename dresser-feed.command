#!/bin/bash
# Double-clic : une seance de dressage du feed du compte scouting (profils dans profils_types.txt).
# Seance douce : 15 profils, 10 likes max, jamais d'abonnement. A faire 1 fois par jour, 2-3 jours.
cd "$(dirname "$0")"
clear
echo "Dressage du feed en cours dans la fenetre Chrome Scout (ne la ferme pas)."
echo "Ferme cette fenetre-ci pour arreter."
echo
python3 -u dresser_feed.py --likes 10 --profils 15 2>&1 | tee resultats/dressage.log
echo
read -p "Termine. Entree pour fermer."
