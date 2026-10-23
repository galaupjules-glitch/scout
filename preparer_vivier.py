#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prepare les profils de la bibliotheque pour la page "Vivier Scout" sur Claude.

  python3 preparer_vivier.py
      -> un JSON par profil pas encore envoye dans resultats/pour-claude/ (envoi par Claude)
  python3 preparer_vivier.py --fichier
      -> UN fichier a glisser dans le bouton "Importer" de la page (0 credit) :
         ~/scout/A-IMPORTER-dans-Vivier.json (profils des 14 derniers jours + panneau de suivi).
         La page n'ajoute que les profils qu'elle n'a pas deja : on peut le reglisser sans risque.
         Fait automatiquement par l'app a la fin de chaque recherche.
"""
import json
import re
import sys
import time
from pathlib import Path

from scout import thumb_datauri

BASE = Path(__file__).parent
RES = BASE / "resultats"
LIB = RES / "bibliotheque.json"
OUT = RES / "pour-claude"
SENT = RES / "envoyes-claude.txt"
FICHIER = BASE / "A-IMPORTER-dans-Vivier.json"
JOURS_FICHIER = 14


def build_doc(d):
    """Profil de la bibliotheque -> document au format de la page Vivier."""
    added = d.get("added") or time.strftime("%Y-%m-%d")
    shot = ""
    png = RES / (d.get("shot") or "")
    if d.get("shot") and png.is_file():
        shot = thumb_datauri(png)
        if len(shot) > 180_000:  # la page limite chaque profil a 256 Ko
            shot = ""
    return {
        "handle": d["handle"],
        "followers": d.get("followers"),
        "following": d.get("following"),
        "verified": bool(d.get("verified")),
        "shot": shot,
        "lien_type": d.get("lien_type") or "",
        "lien": d.get("lien") or "",
        "priorite": d.get("priorite") or 1,
        "creatorsinc": bool(d.get("creatorsinc")),
        "dernier_post": d.get("dernier_post"),
        "posts": d.get("posts"),
        "likes": d.get("likes"),
        "bio": (d.get("bio") or "")[:300],
        "avatar": d.get("avatar") or "",
        "added": added if "T" in added else added + "T" + time.strftime("%H:%M:%S") + "Z",
        "etat": "",
    }


def valid_handle(h):
    return bool(re.fullmatch(r"[a-z0-9_.]{1,30}", h)) and h not in (".", "..")


def stats():
    """Panneau de suivi de la page (doc stats/scout) : objectif du jour + historique."""
    hist = json.loads((RES / "recherches.json").read_text(encoding="utf-8")) \
        if (RES / "recherches.json").exists() else []
    today = time.strftime("%Y-%m-%d")
    return {
        "quota": 120,
        "jour": today,
        "gardes_jour": sum(h.get("gardes", 0) for h in hist if str(h.get("debut", "")).startswith(today)),
        "historique": hist[-20:][::-1],
    }


def main():
    if not LIB.exists():
        print("Bibliotheque vide : lance d'abord Scout.")
        return
    lib = json.loads(LIB.read_text(encoding="utf-8"))

    if "--fichier" in sys.argv:
        limite = time.strftime("%Y-%m-%d", time.localtime(time.time() - JOURS_FICHIER * 86400))
        docs = [build_doc(d) for d in lib
                if valid_handle(d["handle"].lower()) and str(d.get("added", "")) >= limite]
        FICHIER.write_text(json.dumps({"format": "vivier-scout", "version": 1, "profils": docs,
                                       "stats": stats()}, ensure_ascii=False), encoding="utf-8")
        print(f"{len(docs)} profils dans {FICHIER}")
        return

    sent = set(SENT.read_text().split()) if SENT.exists() else set()
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    n = 0
    for d in lib:
        h = d["handle"].lower()
        if h in sent or not valid_handle(h):
            continue
        (OUT / f"{h}.json").write_text(json.dumps(build_doc(d), ensure_ascii=False), encoding="utf-8")
        n += 1
    print(f"{n} profils prets a envoyer dans {OUT}")
    (RES / "pour-claude-stats.json").write_text(json.dumps(stats(), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
