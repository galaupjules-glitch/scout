#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apprentissage a partir de ton tri dans Vivier (0 credit, tout en local).

A chaque recherche, Scout relit ce que tu as fait dans Vivier :
  - tes "♥ Gardees" = ce qu'il faut trouver  (elles deviennent aussi des profils types)
  - tes "Jetes"     = ce qu'il faut eviter
et en deduit des regles simples, recalculees a chaque fois (donc de plus en plus justes) :
  - mots de bio qu'on retrouve chez plusieurs jetees mais jamais chez tes gardees -> ecartes
  - liens en bio (domaines) qu'on retrouve chez plusieurs jetees mais jamais chez tes gardees -> ecartes
  - taille minimale : les plus petits comptes que tu gardes donnent le plancher d'abonnes

    python3 apprentissage.py      -> affiche ce qui est appris de ton tri actuel
"""
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent
LIB = BASE / "resultats" / "bibliotheque.json"
OUT = BASE / "resultats" / "apprentissage.json"
MIN_EXEMPLES = 5   # il faut au moins 5 gardees et 5 jetees pour apprendre quelque chose
MIN_REPET = 2      # un mot / lien doit revenir chez au moins 2 jetees

# mots trop courants pour dire quoi que ce soit
VIDES = set("""the and for you are with this that from your just not all its it's i'm im me my our
une les des est pour avec dans sur pas qui que fl nyc usa new girl life love xo xx""".split())


def _mots(bio):
    return {m for m in re.findall(r"[a-zà-ÿ]{3,}", (bio or "").lower()) if m not in VIDES}


def _domaine(lien):
    l = (lien or "").lower()
    l = re.sub(r"^https?://", "", l).removeprefix("www.")
    return l.split("/")[0] if l else ""


def apprendre(choix, lib=None):
    """choix = [(pseudo, etat, favori)] lus dans Vivier. Renvoie les regles apprises (dict)."""
    if lib is None:
        lib = json.loads(LIB.read_text(encoding="utf-8")) if LIB.exists() else []
    meta = {d["handle"].lower(): d for d in lib}
    gardes = [meta[h] for h, e, fav in choix if fav and e != "jete" and h in meta]
    jetes = [meta[h] for h, e, fav in choix if e == "jete" and h in meta]
    regles = {"gardes": len(gardes), "jetes": len(jetes), "mots": [], "domaines": [], "abonnes_min": None}
    if len(gardes) < MIN_EXEMPLES or len(jetes) < MIN_EXEMPLES:
        OUT.write_text(json.dumps(regles, ensure_ascii=False, indent=1), encoding="utf-8")
        return regles

    mots_g = Counter(m for d in gardes for m in _mots(d.get("bio")))
    mots_j = Counter(m for d in jetes for m in _mots(d.get("bio")))
    regles["mots"] = sorted(m for m, n in mots_j.items() if n >= MIN_REPET and mots_g[m] == 0)

    dom_g = Counter(_domaine(d.get("lien")) for d in gardes)
    dom_j = Counter(_domaine(d.get("lien")) for d in jetes)
    regles["domaines"] = sorted(dm for dm, n in dom_j.items()
                                if dm and n >= MIN_REPET and dom_g[dm] == 0 and "onlyfans" not in dm)

    # plancher d'abonnes : juste sous la plus petite fille que tu as gardee (aucune de tes ♥ exclue)
    ab = sorted(d["followers"] for d in gardes if d.get("followers"))
    if len(ab) >= MIN_EXEMPLES:
        regles["abonnes_min"] = int(ab[0] * 0.9)

    OUT.write_text(json.dumps(regles, ensure_ascii=False, indent=1), encoding="utf-8")
    return regles


def charger():
    """Regles apprises lors de la derniere recherche (ou vides)."""
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"mots": [], "domaines": [], "abonnes_min": None}


def domaine(lien):
    return _domaine(lien)


if __name__ == "__main__":
    import vivier_live
    r = apprendre(vivier_live.lire_choix() or [])
    print(f"Appris de ton tri : {r['gardes']} gardees, {r['jetes']} jetees")
    print("  mots de bio a eviter :", ", ".join(r["mots"]) or "(aucun)")
    print("  liens a eviter       :", ", ".join(r["domaines"]) or "(aucun)")
    print("  abonnes minimum      :", r["abonnes_min"] or "(inchange)")
