#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dressage du feed "Pour toi" du compte scouting.

Le compte scouting interagit, a rythme humain, UNIQUEMENT avec des profils du type
recherche (tes profils types + leurs "Comptes similaires") : visite du profil, temps
passe sur les posts et les reels, quelques likes. Instagram remplit alors le feed
avec ce genre de modeles -> Scout trouve ensuite de bons leads dans le feed.

Regles de securite :
  - JAMAIS d'abonnement (Scout exclut les comptes suivis par le compte scouting).
  - Likes plafonnes (defaut 20 par seance), jamais 2 likes d'affilee sans pause.
  - Pauses au hasard partout, grande pause reguliere.

Usage :
    python3 dresser_feed.py compte1 compte2 compte3
    python3 dresser_feed.py --fichier profils_types.txt      (un @ par ligne)
    python3 dresser_feed.py --likes 0 compte1                 (aucun like : visites seulement)
"""

import argparse
import random
import re
import sys
import time
from pathlib import Path

import navigateur
from reseau import connexion_ok
from scout import LATEST_POST_JS, SESSION_ID_MAISON, similar_accounts

BASE = Path(__file__).parent
TYPES_FILE = BASE / "profils_types.txt"   # tes profils types, memorises pour les prochaines seances


def pause(a, b):
    time.sleep(random.uniform(a, b))


# bouton "J'aime" du post ouvert (le grand coeur, pas ceux des commentaires) -> coordonnees
LIKE_JS = r"""(()=>{const svgs=[...document.querySelectorAll('svg[aria-label="J’aime"],svg[aria-label="J\'aime"],svg[aria-label="Like"]')]
 .filter(s=>s.getBoundingClientRect().height>=20);
 const s=svgs[0];if(!s)return null;const b=s.closest('[role="button"],button')||s;
 b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()"""


def scroll(o, fois=2):
    for _ in range(fois):
        o.souris(640, 450, "mouseWheel", deltaX=0, deltaY=random.randint(400, 900))
        pause(1.5, 3.5)


def like(o):
    pt = o.js(LIKE_JS)
    if not pt:
        return False
    x, y = pt
    o.souris(x, y, "mouseMoved")
    pause(0.3, 0.9)
    o.souris(x, y, "mousePressed", button="left", clickCount=1)
    o.souris(x, y, "mouseReleased", button="left", clickCount=1)
    return True


def dresser_profil(o, user, likes_restants, profond=True):
    """Visite un profil comme un humain interesse. Renvoie le nb de likes faits (0 ou 1)."""
    o.ouvrir(f"https://www.instagram.com/{user}/")
    pause(4, 9)                       # regarde le haut du profil
    scroll(o, random.randint(1, 3))   # parcourt la grille
    fait = 0
    out = o.js(LATEST_POST_JS) or ""
    posts = re.findall(r"/(p|reel)/([A-Za-z0-9_-]+)", out)
    for kind, code in posts[: (2 if profond else 1)]:
        o.ouvrir(f"https://www.instagram.com/{kind}/{code}/")
        pause(8, 20) if kind == "reel" else pause(5, 12)   # regarde le post / le reel
        if likes_restants - fait > 0 and random.random() < 0.6 and like(o):
            fait += 1
            print(f"    ♥ like sur un post de @{user}")
            pause(4, 9)
    return fait


# 4 derniers posts de la grille, hors posts epingles
POSTS4_JS = r"""(()=>{const as=[...document.querySelectorAll('main a[href*="/p/"],main a[href*="/reel/"]')];
const pinned=a=>a.querySelector('svg[aria-label*="pingl" i],svg[aria-label*="pinned" i]');
const ok=as.filter(x=>!pinned(x));return 'POSTS:'+(ok.length?ok:as).slice(0,4).map(a=>a.getAttribute('href')).join(' ')})()"""

# motif demande : like, on saute un post, like, like (3 likes sur les 4 derniers posts)
MOTIF = [True, False, True, True]


def dresser_motif(o, user):
    """Profil type / favori : ouvre ses 4 derniers posts et like selon le motif 1, saut, 1, 1.
    Meme rythme que le reste (pauses humaines). Ne retire jamais un like deja mis. Renvoie le nb de likes."""
    o.ouvrir(f"https://www.instagram.com/{user}/")
    pause(3, 7)
    scroll(o, 1)
    posts = re.findall(r"/(p|reel)/([A-Za-z0-9_-]+)", o.js(POSTS4_JS) or "")
    fait = 0
    for (kind, code), aimer in zip(posts, MOTIF):
        o.ouvrir(f"https://www.instagram.com/{kind}/{code}/")
        pause(6, 14) if kind == "reel" else pause(3, 8)   # regarde le post comme un humain
        if aimer and like(o):
            fait += 1
            pause(2, 5)
    print(f"    ♥ {fait} likes sur @{user} (motif 1, saut, 1, 1)")
    return fait


def main():
    ap = argparse.ArgumentParser(description="Dresse le feed du compte scouting avec des profils types")
    ap.add_argument("comptes", nargs="*", help="profils types (@ ou lien)")
    ap.add_argument("--fichier", help="fichier avec un profil type par ligne")
    ap.add_argument("--likes", type=int, default=20, help="likes max pour la seance (0 = aucun)")
    ap.add_argument("--profils", type=int, default=25, help="profils visites max pour la seance")
    args = ap.parse_args()

    clean = lambda s: re.sub(r"^@|https?://(www\.)?instagram\.com/|/.*$", "", s.strip()).lower()
    types = [clean(c) for c in args.comptes]
    src = Path(args.fichier) if args.fichier else TYPES_FILE
    if src.exists():
        types += [clean(l) for l in src.read_text().splitlines() if l.strip() and not l.startswith("#")]
    types = [t for t in dict.fromkeys(types) if re.fullmatch(r"[a-z0-9_.]{1,30}", t)]
    if not types:
        sys.exit("Donne au moins un profil type : python3 dresser_feed.py compte1 compte2 ...")
    # memorise les profils types pour les prochaines seances
    TYPES_FILE.write_text("\n".join(types) + "\n", encoding="utf-8")

    ok_net, msg_net = connexion_ok()  # jamais sur l'internet normal
    if not ok_net:
        sys.exit("BLOQUE : " + msg_net)
    o = navigateur.onglet()
    print(f"Dressage du feed : {len(types)} profils types, {args.profils} visites max, {args.likes} likes max\n")
    random.shuffle(types)
    file, vus, likes = list(types), set(), 0
    while file and len(vus) < args.profils:
        if not connexion_ok()[0]:
            print("Connexion normale detectee : arret immediat pour proteger le compte.")
            break
        user = file.pop(0)
        if user in vus:
            continue
        vus.add(user)
        est_type = user in types
        print(f"[{len(vus)}/{args.profils}] @{user}" + ("  (profil type)" if est_type else "  (similaire)"))
        try:
            likes += dresser_profil(o, user, args.likes - likes, profond=est_type)
            if est_type:  # ses "Comptes similaires" = d'autres modeles du meme genre a visiter ensuite
                for h in [h.lower() for h in similar_accounts(SESSION_ID_MAISON, user)][:4]:
                    if h not in vus and h not in file:
                        file.append(h)
        except Exception as e:
            print(f"    (passe : {e})")
        pause(10, 25)
        if len(vus) % random.randint(6, 9) == 0:
            b = random.uniform(60, 150)
            print(f"  ... pause de {int(b)} s (rythme humain)")
            time.sleep(b)

    # fin : un passage sur le feed "Pour toi" pour qu'Instagram enregistre les nouveaux gouts
    print("\nPassage sur le feed pour finir...")
    o.ouvrir("https://www.instagram.com/")
    for _ in range(random.randint(8, 14)):
        scroll(o, 1)
        pause(3, 8)
    print(f"\nTermine : {len(vus)} profils visites, {likes} likes, 0 abonnement.")
    print("Le feed met quelques heures a changer. Refais une seance par jour pendant 2-3 jours.")


if __name__ == "__main__":
    main()
