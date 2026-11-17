#!/bin/bash
# Double-clique : ouvre Pilote (publication auto Drive -> Publer + super analyse).
cd "$(dirname "$0")/pilote"
clear
if curl -s -o /dev/null http://127.0.0.1:8790/api/status; then
  open "http://127.0.0.1:8790/"; exit 0
fi
echo "Pilote demarre... (laisse cette fenetre ouverte pour l'autopilote)"
python3 pilote.py
