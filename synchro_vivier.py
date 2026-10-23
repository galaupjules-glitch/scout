#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synchro Scout <-> page Vivier Scout sur Claude (partie locale, zero token).

Claude (tache planifiee toutes les heures) s'en sert ainsi :
  1. python3 synchro_vivier.py preparer
       -> prepare les nouveaux profils (resultats/pour-claude/*.json + stats) et affiche
          la liste des fichiers a envoyer dans la page (rien = rien de nouveau).
  2. Claude envoie ces fichiers dans la base de la page (collection "profils", doc = pseudo).
  3. python3 synchro_vivier.py envoyes pseudo1 pseudo2 ...
       -> les note comme envoyes (jamais renvoyes).
  4. Claude lit les profils marques "vu" / "jete" dans la page et les ecrit dans un JSON
     {"pseudo": "vu"|"jete", ...}, puis :
     python3 synchro_vivier.py etats fichier.json
       -> reporte tes choix dans l'app Scout locale (jete = jete aussi dans l'app).
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
RES = BASE / "resultats"
OUT = RES / "pour-claude"
SENT = RES / "envoyes-claude.txt"
ETATS = RES / "etats.json"


def preparer():
    subprocess.run([sys.executable, str(BASE / "preparer_vivier.py")], check=True,
                   stdout=subprocess.DEVNULL)
    files = sorted(OUT.glob("*.json")) if OUT.exists() else []
    print(json.dumps({"profils": [str(f) for f in files],
                      "stats": str(RES / "pour-claude-stats.json")}, indent=1))


def envoyes(handles):
    sent = set(SENT.read_text().split()) if SENT.exists() else set()
    sent |= {h.lower().lstrip("@") for h in handles}
    SENT.write_text("\n".join(sorted(sent)) + "\n", encoding="utf-8")
    for h in handles:
        (OUT / f"{h.lower()}.json").unlink(missing_ok=True)
    print(f"{len(handles)} profils notes comme envoyes ({len(sent)} au total).")


def etats(path):
    choix = json.loads(Path(path).read_text(encoding="utf-8"))
    local = json.loads(ETATS.read_text(encoding="utf-8")) if ETATS.exists() else {}
    n = 0
    for h, e in choix.items():
        v = {"jete": "drop", "vu": "vu"}.get(e)
        if v and local.get(h.lower()) != v and local.get(h.lower()) != "keep":
            local[h.lower()] = v
            n += 1
    ETATS.write_text(json.dumps(local, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{n} choix de Vivier reportes dans l'app Scout.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "preparer":
        preparer()
    elif cmd == "envoyes":
        envoyes(sys.argv[2:])
    elif cmd == "etats" and len(sys.argv) > 2:
        etats(sys.argv[2])
    else:
        print(__doc__)
