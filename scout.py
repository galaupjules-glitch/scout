#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout - moteur de lead-gen Instagram local (zero token IA).

Pilote OpenCLI (deja installe) pour :
  1. Recolter des comptes candidats (carousels "Comptes similaires" + explore/people)
  2. Extraire leurs stats en texte et FILTRER automatiquement (regles deterministes)
  3. Telecharger 1 screenshot par finaliste
  4. Generer une page de swipe locale (review.html) pour le tri visuel humain

Usage :
    python3 scout.py --target 100
    python3 scout.py --target 50 --min 2000 --max 20000
    python3 scout.py --seeds compte1,compte2,compte3

Le compte Instagram utilise = celui connecte dans Chrome (via l'extension OpenCLI).
"""

import argparse
import random
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ----------------------------------------------------------------------------
# CONFIG (modifiable en haut de fichier ou via les arguments CLI)
# ----------------------------------------------------------------------------

# Fourchette d'abonnes cible (comptes vitrine, ni trop petits ni deja etablis)
FOLLOWERS_MIN = 1500
FOLLOWERS_MAX = 25000

# Ratio following/followers max. Au-dessus = compte perso/college (pas une vitrine)
MAX_FOLLOWING_RATIO = 1.6

# Engagement minimum : likes sur le post le plus recent. En dessous = followers fantomes.
MIN_LIKES_PER_POST = 500

# Un profil dont on a deroule les 'Comptes similaires' n'est pas re-mine avant N jours
MINE_COOLDOWN_DAYS = 7

# Mots-cles bio qui disqualifient (marques, coachs, business, deja managees...)
BIO_BLACKLIST = [
    "coach", "nutrition", "personal trainer", "nail", "barber", "realtor",
    "nurse", "crna", "phd", "ambassador", "trad wife", "homestead", "diesel",
    "truck", "photograph", "esthetician", "makeup artist", "mua", "salon",
    "agency", "talent", "management", "repulse", "grail", "fanfix agency",
    "licensed", "cosmo", "student nurse",
]

# Comptes de depart pour la recolte (les meilleurs seeds "vitrine" trouves).
# Le moteur ajoute automatiquement chaque nouveau lead comme seed (effet boule de neige).
DEFAULT_SEEDS = []

# Ton pseudo perso : les comptes qu'il suit deja sont exclus (deja contactes)
YOUR_HANDLE = ""

# Compte scouting connecte dans Chrome (toujours exclu des leads)
SCOUT_ACCOUNT = ""

# Compte connecte dans Chrome (rempli automatiquement au demarrage)
CONNECTED_ACCOUNT = None

# Valeurs perso (comptes, seeds) : config.local.py, jamais commite. Voir config.example.py
try:
    from config_local import YOUR_HANDLE, SCOUT_ACCOUNT, DEFAULT_SEEDS  # noqa: F401,F811
except ImportError:
    pass

# Handles a toujours ignorer (comptes systeme / navigation Instagram)
IGNORE = {
    "explore", "reels", "accounts", "about", "directory", "popular", "reel",
    "p", "stories", "direct", "your_story", "tv",
}

# ----------------------------------------------------------------------------
# Localisation d'OpenCLI et de la session du navigateur
# ----------------------------------------------------------------------------

def find_opencli():
    """Trouve le binaire opencli."""
    for cand in [
        os.path.expanduser("~/.local/node-v20.18.0-darwin-x64/bin/opencli"),
        "opencli",
    ]:
        try:
            subprocess.run([cand, "--version"], capture_output=True, timeout=15)
            return cand
        except Exception:
            continue
    sys.exit("ERREUR : opencli introuvable. Installe-le : npm install -g @jackwener/opencli")


OPENCLI = find_opencli()


def run(args, timeout=60):
    """Lance une commande opencli, retourne stdout (str)."""
    try:
        r = subprocess.run(
            [OPENCLI] + args, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout or ""
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        print(f"  (erreur opencli: {e})")
        return ""


def get_session():
    """Detecte l'id du profil Chrome connecte au bridge OpenCLI."""
    out = run(["profile", "list"], timeout=20)
    m = re.search(r"([0-9a-z]{8})\s+—\s+connected", out)
    if m:
        return m.group(1)
    # fallback doctor
    out = run(["doctor"], timeout=20)
    m = re.search(r"•\s*([0-9a-z]{8}):\s*connected", out)
    if m:
        return m.group(1)
    sys.exit("ERREUR : aucun navigateur connecte au bridge OpenCLI.\n"
             "Ouvre Chrome, connecte-toi au compte scouting, verifie avec: opencli doctor")


def whoami(session):
    out = run(["instagram", "whoami"], timeout=30)
    m = re.search(r"username:\s*([A-Za-z0-9_.]+)", out)
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# Navigation + extraction
# ----------------------------------------------------------------------------

def open_profile(session, user):
    run(["browser", session, "open", f"https://www.instagram.com/{user}/"], timeout=40)
    time.sleep(3)


def links_on_page(session, limit=60):
    """Extrait tous les @ (liens de profil) de la page courante."""
    out = run(["browser", session, "find", "--css", "a[role='link']",
               "--limit", str(limit)], timeout=40)
    handles = re.findall(r'"href":\s*"/([A-Za-z0-9_.]+)/"', out)
    clean = []
    for h in handles:
        hl = h.lower()
        if hl in IGNORE or hl == YOUR_HANDLE or (CONNECTED_ACCOUNT and hl == CONNECTED_ACCOUNT):
            continue
        clean.append(h)
    return list(dict.fromkeys(clean))  # unique, ordre conserve


def header_of(session):
    """Retourne le texte du header du profil courant (stats + bio + bouton)."""
    out = run(["browser", session, "find", "--css", "main header",
               "--limit", "1", "--text-max", "400"], timeout=40)
    m = re.search(r'"text":\s*"([^"]*)"', out)
    return m.group(1) if m else ""


def similar_accounts(session, user):
    """Ouvre un profil, deroule 'Comptes similaires', renvoie les @ suggeres."""
    open_profile(session, user)
    out = run(["browser", session, "find", "--role", "button",
               "--name", "Comptes similaires", "--limit", "1"], timeout=40)
    m = re.search(r'"ref":\s*(\d+)', out)
    if not m:
        return []
    run(["browser", session, "click", m.group(1)], timeout=30)
    time.sleep(3)
    return links_on_page(session, limit=60)


def explore_people(session):
    """Recupere une fournee de comptes suggeres depuis explore/people."""
    run(["browser", session, "open",
         "https://www.instagram.com/explore/people/"], timeout=40)
    time.sleep(3)
    for _ in range(6):
        run(["browser", session, "scroll", "down", "--repeat", "2"], timeout=20)
        time.sleep(1)
    return links_on_page(session, limit=250)


FEED_JS = r"""(async()=>{const s=new Set();for(let i=0;i<%d;i++){
document.querySelectorAll("article").forEach(a=>{
 const l=a.querySelector('header a[href^="/"], a[role=link][href^="/"]');
 if(l){const m=l.getAttribute("href").match(/^\/([A-Za-z0-9_.]+)\/$/);if(m)s.add(m[1])}});
window.scrollBy(0,1400);await new Promise(r=>setTimeout(r,1300))}
return "HANDLES:"+[...s].join(",")})()"""


def feed_authors(session, scrolls=30):
    """Recupere les auteurs des posts du feed 'Pour toi' (FYP) du compte scouting."""
    run(["browser", session, "open", "https://www.instagram.com/"], timeout=40)
    time.sleep(4)
    out = run(["browser", session, "eval", FEED_JS % scrolls], timeout=scrolls * 3 + 60)
    m = re.search(r"HANDLES:([A-Za-z0-9_.,]*)", out)
    if not m:
        return []
    clean = []
    for h in m.group(1).split(","):
        hl = h.lower()
        if not h or hl in IGNORE or hl == YOUR_HANDLE or hl == SCOUT_ACCOUNT \
                or (CONNECTED_ACCOUNT and hl == CONNECTED_ACCOUNT):
            continue
        clean.append(h)
    return list(dict.fromkeys(clean))


def screenshot(session, user, path):
    run(["browser", session, "screenshot", str(path)], timeout=40)


def first_post_likes(session, user):
    """Ouvre le post le plus recent du profil et renvoie son nb de likes (ou None)."""
    open_profile(session, user)
    out = run(["browser", session, "find", "--css", "a[href*='/p/']",
               "--limit", "1"], timeout=40)
    m = re.search(r"/p/([A-Za-z0-9_-]+)", out)
    if not m:  # pas de post photo/carrousel -> tente un reel
        out = run(["browser", session, "find", "--css", "a[href*='/reel/']",
                   "--limit", "1"], timeout=40)
        m = re.search(r"/reel/([A-Za-z0-9_-]+)", out)
        if not m:
            return None
        code, kind = m.group(1), "reel"
    else:
        code, kind = m.group(1), "p"
    run(["browser", session, "open",
         f"https://www.instagram.com/{kind}/{code}/"], timeout=40)
    time.sleep(3)
    # les likes sont dans le JSON de la page ("like_count":N), pas dans le texte visible.
    # Les petites valeurs (0,1,2,3...) sont des likes de COMMENTAIRES -> on les ignore.
    # Le post principal = 1er like_count >= 100 (les posts d'un compte 2k+ depassent 100).
    body = run(["browser", session, "get", "text", "body"], timeout=40)
    nums = [int(x) for x in re.findall(r'like_count\\?"\s*:\s*(\d+)', body)]
    big = [n for n in nums if n >= 100]
    if big:
        return big[0]
    if nums:
        return max(nums)
    return None


# ----------------------------------------------------------------------------
# Parsing des stats + filtrage
# ----------------------------------------------------------------------------

def to_number(s):
    """'6 406' -> 6406, '10,8 k' -> 10800, '1,5 m' -> 1500000."""
    s = s.strip().lower().replace(" ", " ").replace("\xa0", " ")
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1000000, s[:-1]
    s = s.replace(" ", "").replace(",", ".")
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def parse_header(text):
    """Extrait username, posts, followers, following, bio, statut suivi, verifie."""
    info = {"raw": text}
    m = re.search(
        r"([\d.,\s]+?)\s*(?:publication|publications)\s*"
        r"([\d.,\s]*[km]?)\s*followers\s*"
        r"([\d.,\s]*[km]?)\s*suivi",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    info["posts"] = to_number(m.group(1))
    info["followers"] = to_number(m.group(2))
    info["following"] = to_number(m.group(3))
    info["verified"] = "Vérifié" in text or "Verifie" in text
    info["followed_by_you"] = f"Suivi(e) par {YOUR_HANDLE}" in text
    # bouton "Suivi(e)" (= le compte connecte suit deja) vs "Suivre"
    tail = text.split("suivi(e)s", 1)[-1] if "suivi(e)s" in text else text
    info["followed_by_account"] = bool(
        re.search(r"Suivi\(e\)(Icône|Contacter|Comptes)", tail)
    )
    # bio = ce qui suit le compteur de following, avant les boutons
    bio = tail
    for cut in ["Suivre", "Suivi(e)", "Contacter", "Comptes similaires",
                "Icône de lien", "Threads"]:
        bio = bio.split(cut)[0]
    info["bio"] = bio.strip()
    return info


def passes(info):
    """Retourne (ok: bool, raison: str)."""
    if not info or info.get("followers") is None:
        return False, "stats illisibles"
    f = info["followers"]
    if f < FOLLOWERS_MIN:
        return False, f"trop petit ({f})"
    if f > FOLLOWERS_MAX:
        return False, f"trop gros ({f})"
    if info.get("following") and f and info["following"] / max(f, 1) > MAX_FOLLOWING_RATIO:
        return False, "ratio perso/college"
    if info.get("followed_by_account") or info.get("followed_by_you"):
        return False, "deja suivi (deja contacte)"
    bio_l = info.get("bio", "").lower()
    for kw in BIO_BLACKLIST:
        if kw in bio_l:
            return False, f"bio: {kw}"
    if info.get("posts") == 0:
        return False, "0 post"
    return True, "ok"


# ----------------------------------------------------------------------------
# Boucle principale
# ----------------------------------------------------------------------------

def main():
    global FOLLOWERS_MIN, FOLLOWERS_MAX, CONNECTED_ACCOUNT

    try:
        sys.stdout.reconfigure(line_buffering=True)  # progression visible en direct
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Scout - lead-gen Instagram local")
    ap.add_argument("--target", type=int, default=50, help="nb de leads a retenir")
    ap.add_argument("--min", type=int, default=FOLLOWERS_MIN, help="abonnes min")
    ap.add_argument("--max", type=int, default=FOLLOWERS_MAX, help="abonnes max")
    ap.add_argument("--seeds", type=str, default="", help="seeds separes par virgule")
    ap.add_argument("--similaires", action="store_true",
                    help="ancienne methode : derouler les 'Comptes similaires' des seeds (sinon FYP)")
    ap.add_argument("--out", type=str, default="resultats", help="dossier de sortie")
    args = ap.parse_args()

    FOLLOWERS_MIN, FOLLOWERS_MAX = args.min, args.max
    out = Path(__file__).parent / args.out
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    session = get_session()
    CONNECTED_ACCOUNT = (whoami(session) or "").lower()
    print(f"Bridge : {session}  |  compte connecte : @{CONNECTED_ACCOUNT}")
    print(f"Cible : {args.target} leads  |  fourchette : {FOLLOWERS_MIN}-{FOLLOWERS_MAX} abonnes\n")

    # liste des @ deja connus (backlog + runs precedents) -> jamais re-exportes
    known_file = out / "known.txt"
    known = set()
    if known_file.exists():
        known = set(l.strip().lower() for l in known_file.read_text().splitlines() if l.strip())
    if known:
        print(f"{len(known)} comptes deja connus seront ignores (que du nouveau).\n")

    # profils deja examines et rejetes -> jamais re-qualifies
    rejected_file = out / "rejetes.txt"
    rejected = set()
    if rejected_file.exists():
        rejected = set(l.strip().lower() for l in rejected_file.read_text().splitlines() if l.strip())
    if rejected:
        print(f"{len(rejected)} profils deja rejetes ne seront pas re-verifies.")

    # profils dont on a deja deroule les 'Comptes similaires' recemment (handle -> timestamp)
    mined_file = out / "mines.json"
    mined = {}
    if mined_file.exists():
        try:
            mined = json.loads(mined_file.read_text())
        except Exception:
            mined = {}
    fresh_cut = time.time() - MINE_COOLDOWN_DAYS * 86400
    def recently_mined(h):
        return mined.get(h.lower(), 0) > fresh_cut

    # seeds : ceux donnes + les leads deja connus (boule de neige entre runs),
    # en sautant ceux deja mines recemment -> chaque run explore de nouvelles branches
    base = [s.strip() for s in args.seeds.split(",") if s.strip()] or list(DEFAULT_SEEDS)
    pool = list(dict.fromkeys(base + sorted(known)))
    random.shuffle(pool)
    seeds = [h for h in pool if not recently_mined(h)] if args.similaires else []
    if args.similaires:
        print(f"Mode comptes similaires : {len(seeds)} seeds frais (sur {len(pool)}).\n")
    else:
        print("Mode FYP : recolte sur le feed 'Pour toi' du compte scouting.\n")
    queue = list(seeds)
    seen = set(s.lower() for s in pool)
    kept = []          # leads retenus (dicts)
    examined = 0

    def harvest_more():
        """Ajoute de nouveaux candidats a la file : FYP d'abord, explore/people en secours."""
        sources = [("FYP", feed_authors), ("explore/people", explore_people)]
        for name, fn in sources:
            new = 0
            for h in fn(session):
                if h.lower() not in seen and h.lower() not in done:
                    seen.add(h.lower())
                    queue.append(h)
                    new += 1
            print(f"  ... {name} : {new} nouveaux profils a verifier")
            if new:
                return
    empty_harvests = 0

    def save_outputs():
        """Ecrit toutes les sorties + known.txt (appele a chaque lead et en fin de run)."""
        (out / "candidates.json").write_text(
            json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv(out / "leads.csv", kept)
        write_review(out / "review.html", kept)              # version ordi (photos = dossier shots/)
        write_mobile(out / "mobile.html", kept, shots)       # version autonome pour le tel
        write_cloud_export(out / "export-vivier.json", kept, shots)  # a importer dans l'artifact Vivier
        # memorise tous les @ connus pour que le prochain run ne ramene que du nouveau
        all_known = known | set(k["handle"].lower() for k in kept)
        known_file.write_text("\n".join(sorted(all_known)), encoding="utf-8")

    idx = 0
    done = set(known) | rejected  # connus + deja qualifies : recoltes mais jamais re-exportes
    dry = 0            # rejets consecutifs (detection de tarissement/throttle)
    while len(kept) < args.target:
        if idx >= len(queue):
            before = len(queue)
            harvest_more()
            if len(queue) == before:
                empty_harvests += 1
                if empty_harvests >= 3:
                    print("  Sources epuisees. Laisse reposer quelques heures et relance.")
                    break
                continue
            empty_harvests = 0
        user = queue[idx]
        idx += 1

        # 1) recolte les comptes similaires de ce profil (BFS) -> nouveaux candidats
        if args.similaires and not recently_mined(user):
            new = 0
            for h in similar_accounts(session, user):
                if h.lower() not in seen:
                    seen.add(h.lower())
                    queue.insert(idx + new, h)  # qualifies juste apres ce seed
                    new += 1
            mined[user.lower()] = time.time()
            mined_file.write_text(json.dumps(mined), encoding="utf-8")
            if user.lower() in done:
                print(f"  · seed @{user} -> {new} nouveaux candidats (file: {len(queue) - idx})")

        # deja qualifie ? on a juste re-recolte son reseau, on ne le re-qualifie pas
        if user.lower() in done:
            continue
        done.add(user.lower())

        # 2) qualifie ce profil (on est deja dessus apres similar_accounts)
        open_profile(session, user)
        info = parse_header(header_of(session))
        if info is None:  # chargement lent -> un retry
            time.sleep(2)
            info = parse_header(header_of(session))
        examined += 1
        ok, why = passes(info)
        likes = None
        if ok:  # ne verifie l'engagement que si les stats passent (economie)
            likes = first_post_likes(session, user)
            if likes is not None and likes < MIN_LIKES_PER_POST:
                ok, why = False, f"engagement faible ({likes} likes)"
            elif likes is None:
                why = "ok (likes masques)"
        tag = "GARDE" if ok else "jette"
        fol = info.get("followers") if info else "?"
        lk = f"{likes} likes" if likes is not None else "likes ?"
        print(f"[{len(kept)}/{args.target}] {tag:5} @{user}  ({fol} abo, {lk})  {why}")
        if ok:
            shot = shots / f"{user}.png"
            open_profile(session, user)  # revient sur le profil pour la capture
            screenshot(session, user, shot)
            kept.append({
                "handle": user,
                "followers": info["followers"],
                "following": info.get("following"),
                "posts": info.get("posts"),
                "likes": likes,
                "verified": info.get("verified"),
                "bio": info.get("bio", ""),
                "url": f"https://www.instagram.com/{user}/",
                "shot": f"shots/{user}.png",
            })
            dry = 0
            save_outputs()  # sauvegarde a chaque lead : un arret ne perd rien
        else:
            if info is not None:  # rejet reel (pas un echec de chargement) -> memorise
                rejected.add(user.lower())
                with open(rejected_file, "a", encoding="utf-8") as f:
                    f.write(user.lower() + "\n")
            dry += 1
            if dry >= 40:
                print("  40 rejets d'affilee -> sources taries ou Instagram throttle.")
                print("  Laisse reposer quelques heures et relance.")
                break

    save_outputs()

    print(f"\nTermine. {len(kept)} leads retenus sur {examined} profils examines.")
    print(f"  -> {out/'leads.csv'}")
    print(f"  -> ORDI : ouvre {out/'review.html'} pour swiper")
    print(f"  -> TEL  : envoie {out/'mobile.html'} sur ton telephone (AirDrop), autonome")
    print(f"  -> CLOUD: importe {out/'export-vivier.json'} dans ton artifact Vivier (synchro partout)")


def write_csv(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["handle", "followers", "following", "posts", "likes",
                    "verified", "bio", "url"])
        for r in rows:
            w.writerow([r["handle"], r["followers"], r["following"], r["posts"],
                        r.get("likes"), r["verified"], r["bio"], r["url"]])


def write_review(path, rows):
    """Page de swipe pour l'ordi (photos = chemins vers le dossier shots/)."""
    data = json.dumps(rows, ensure_ascii=False)
    path.write_text(REVIEW_TEMPLATE.replace("__DATA__", data), encoding="utf-8")


def thumb_datauri(png_path):
    """Reduit un screenshot en vignette JPEG (via 'sips' macOS) + base64. Fallback PNG brut."""
    try:
        thumb = Path(str(png_path) + ".thumb.jpg")
        subprocess.run(["sips", "-Z", "560", "-s", "format", "jpeg", "-s",
                        "formatOptions", "70", str(png_path), "--out", str(thumb)],
                       capture_output=True, timeout=30)
        src = thumb if thumb.exists() else png_path
    except Exception:
        src = png_path
    mime = "image/jpeg" if str(src).endswith(".jpg") else "image/png"
    b64 = base64.b64encode(Path(src).read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def write_mobile(path, rows, shots_dir):
    """Page autonome (photos compressees integrees) a envoyer sur le telephone."""
    mrows = []
    for r in rows:
        png = shots_dir / f"{r['handle']}.png"
        r2 = dict(r)
        if png.exists():
            r2["shot"] = thumb_datauri(png)
        mrows.append(r2)
    data = json.dumps(mrows, ensure_ascii=False)
    path.write_text(MOBILE_TEMPLATE.replace("__DATA__", data), encoding="utf-8")


def write_cloud_export(path, rows, shots_dir):
    """Fichier JSON a importer dans l'artifact Vivier (leads + vignettes base64)."""
    out = []
    for r in rows:
        png = shots_dir / f"{r['handle']}.png"
        d = {k: r.get(k) for k in ("handle", "followers", "following",
                                   "posts", "likes", "verified", "bio", "url")}
        d["thumb"] = thumb_datauri(png) if png.exists() else ""
        out.append(d)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


REVIEW_TEMPLATE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Scout - Tri des leads</title>
<style>
  :root{--bg:#0f0f11;--card:#1b1b1f;--txt:#f2f2f3;--mut:#9a9aa2;--keep:#2ecc71;--drop:#e74c3c}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  header{padding:16px 20px;position:sticky;top:0;background:var(--bg);border-bottom:1px solid #26262b;
    display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0} .count{color:var(--mut);font-size:13px}
  button{cursor:pointer;border:0;border-radius:8px;padding:9px 14px;font-weight:600;font-size:13px}
  .exp{background:#3b82f6;color:#fff;margin-left:auto}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;padding:18px}
  .card{background:var(--card);border-radius:12px;overflow:hidden;border:2px solid transparent}
  .card.keep{border-color:var(--keep)} .card.drop{opacity:.4;border-color:var(--drop)}
  .card img{width:100%;display:block;aspect-ratio:.82;object-fit:cover;background:#000}
  .meta{padding:10px 12px} .h{font-weight:700} .s{color:var(--mut);font-size:12px;margin-top:2px}
  .bio{color:#c7c7cf;font-size:11px;margin-top:6px;max-height:32px;overflow:hidden}
  .row{display:flex;gap:8px;padding:0 12px 12px}
  .row button{flex:1} .k{background:#123d24;color:var(--keep)} .d{background:#3d1717;color:var(--drop)}
  .card.keep .k{background:var(--keep);color:#00220f} .card.drop .d{background:var(--drop);color:#2b0606}
  a{color:#7ab7ff;font-size:12px;text-decoration:none}
  #export{padding:18px;white-space:pre-wrap;font-family:monospace;font-size:12px;color:var(--mut)}
</style></head><body>
<header>
  <h1>🔎 Scout — tri des leads</h1>
  <span class="count" id="c"></span>
  <button class="exp" onclick="exportKeep()">Exporter les gardes ⬇️</button>
</header>
<div class="grid" id="grid"></div>
<div id="export"></div>
<script>
const DATA = __DATA__;
const state = {}; // handle -> 'keep'|'drop'
function render(){
  const g=document.getElementById('grid'); g.innerHTML='';
  DATA.forEach(d=>{
    const st=state[d.handle]||'';
    const c=document.createElement('div'); c.className='card '+st;
    c.innerHTML=`<img src="${d.shot}" loading="lazy">
      <div class="meta"><div class="h">@${d.handle} ${d.verified?'✔️':''}</div>
      <div class="s">${d.followers.toLocaleString()} abonnés · ${d.posts} posts${d.likes!=null?' · '+d.likes.toLocaleString()+' likes':''}</div>
      <div class="bio">${(d.bio||'').replace(/</g,'&lt;')}</div>
      <a href="${d.url}" target="_blank">ouvrir le profil ↗</a></div>
      <div class="row"><button class="k" onclick="set('${d.handle}','keep')">Garder</button>
      <button class="d" onclick="set('${d.handle}','drop')">Jeter</button></div>`;
    g.appendChild(c);
  });
  const k=Object.values(state).filter(v=>v==='keep').length;
  document.getElementById('c').textContent=`${DATA.length} candidats · ${k} gardés`;
}
function set(h,v){ state[h]=(state[h]===v?'':v); render(); }
function exportKeep(){
  const keep=DATA.filter(d=>state[d.handle]==='keep');
  const csv='handle,followers,posts,url\n'+keep.map(d=>
    `${d.handle},${d.followers},${d.posts},${d.url}`).join('\n');
  const blob=new Blob([csv],{type:'text/csv'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='leads_gardes.csv'; a.click();
  document.getElementById('export').textContent=
    'Gardés ('+keep.length+') :\n'+keep.map(d=>'@'+d.handle).join('\n');
}
render();
</script></body></html>"""


MOBILE_TEMPLATE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Scout — swipe</title>
<style>
  :root{--bg:#0c0c0e;--txt:#fff;--mut:#a8a8b0;--keep:#2ecc71;--drop:#ff4d4d;--go:#4a9bff}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    overflow:hidden;display:flex;flex-direction:column;
    padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
  .top{padding:12px 18px;display:flex;align-items:center;gap:12px;font-size:14px}
  .bar{flex:1;height:6px;border-radius:99px;background:#26262b;overflow:hidden}
  .bar>i{display:block;height:100%;background:var(--keep);width:0;transition:width .25s}
  .kc{color:var(--keep);font-weight:700}
  .stage{flex:1;position:relative;margin:4px 14px 0}
  .card{position:absolute;inset:0;border-radius:22px;overflow:hidden;background:#000;
    box-shadow:0 12px 40px rgba(0,0,0,.6);will-change:transform;touch-action:pan-y}
  .card img{width:100%;height:100%;object-fit:cover;display:block;pointer-events:none}
  .grad{position:absolute;left:0;right:0;bottom:0;padding:70px 18px 18px;
    background:linear-gradient(to top,rgba(0,0,0,.92),rgba(0,0,0,.55) 55%,transparent);
    pointer-events:none}
  .h{font-size:22px;font-weight:800;display:flex;align-items:center;gap:6px}
  .s{color:#e7e7ee;font-size:14px;margin-top:4px;font-weight:600}
  .bio{color:#c7c7cf;font-size:13px;margin-top:8px;max-height:54px;overflow:hidden;line-height:1.35}
  .stamp{position:absolute;top:26px;font-size:34px;font-weight:900;padding:6px 14px;
    border:4px solid;border-radius:12px;opacity:0;transform:rotate(-14deg);letter-spacing:1px}
  .stamp.keep{left:22px;color:var(--keep);border-color:var(--keep)}
  .stamp.drop{right:22px;color:var(--drop);border-color:var(--drop);transform:rotate(14deg)}
  .btns{display:flex;justify-content:center;align-items:center;gap:22px;padding:16px 0 20px}
  .btns button{border:0;border-radius:50%;color:#fff;font-size:26px;
    display:flex;align-items:center;justify-content:center;cursor:pointer;
    box-shadow:0 6px 18px rgba(0,0,0,.4);active:scale(.9)}
  .b-drop{width:62px;height:62px;background:var(--drop)}
  .b-go{width:74px;height:74px;background:var(--go);font-size:15px;font-weight:800;flex-direction:column;gap:1px}
  .b-go small{font-size:10px;opacity:.85;font-weight:600}
  .b-keep{width:62px;height:62px;background:var(--keep)}
  .done{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;
    justify-content:center;text-align:center;padding:30px;gap:14px}
  .done h2{margin:0;font-size:24px}
  .done p{color:var(--mut);margin:0}
  .keeplist{width:100%;max-width:420px;text-align:left;background:#16161a;border-radius:14px;
    padding:14px;max-height:44vh;overflow:auto;font-size:14px;line-height:1.9}
  .keeplist a{color:var(--go);text-decoration:none}
  .cp{background:var(--keep);color:#04240f;border:0;border-radius:12px;padding:13px 22px;
    font-weight:800;font-size:15px;cursor:pointer}
  .undo{background:none;border:0;color:var(--mut);font-size:13px;cursor:pointer;text-decoration:underline}
</style></head><body>
<div class="top">
  <button class="undo" id="undo" onclick="undo()">↩︎ annuler</button>
  <div class="bar"><i id="prog"></i></div>
  <span class="kc" id="kc">0 ♥</span>
</div>
<div class="stage" id="stage"></div>
<div class="btns" id="btns">
  <button class="b-drop" onclick="decide('drop')">✕</button>
  <button class="b-go" onclick="openInsta()">GO<small>voir insta</small></button>
  <button class="b-keep" onclick="decide('keep')">♥</button>
</div>
<script>
const DATA=__DATA__;
const KEY='scout_swipe_'+DATA.length+'_'+(DATA[0]?DATA[0].handle:'x');
let state=JSON.parse(localStorage.getItem(KEY)||'{}'); // handle->keep/drop
let i=0, hist=[];
function nextIdx(){ while(i<DATA.length && state[DATA[i].handle]) i++; return i; }
function esc(s){return (s||'').replace(/</g,'&lt;');}
function card(d){
  return `<div class="card" id="cur">
    <img src="${d.shot}">
    <div class="stamp keep" id="sk">GARDER</div>
    <div class="stamp drop" id="sd">JETER</div>
    <div class="grad">
      <div class="h">@${d.handle} ${d.verified?'✔️':''}</div>
      <div class="s">${(d.followers||0).toLocaleString()} abonnés · ${d.posts} posts${d.likes!=null?' · '+d.likes.toLocaleString()+' likes':''}</div>
      <div class="bio">${esc(d.bio)}</div>
    </div></div>`;
}
function render(){
  const st=document.getElementById('stage');
  nextIdx();
  const done=Object.values(state).filter(v=>v==='keep').length;
  document.getElementById('kc').textContent=done+' ♥';
  document.getElementById('prog').style.width=(Object.keys(state).length/DATA.length*100)+'%';
  document.getElementById('undo').style.visibility=hist.length?'visible':'hidden';
  if(i>=DATA.length){ showDone(); return; }
  st.innerHTML=card(DATA[i]);
  attachDrag(document.getElementById('cur'));
}
function decide(v){
  if(i>=DATA.length) return;
  hist.push(DATA[i].handle);
  state[DATA[i].handle]=v;
  localStorage.setItem(KEY,JSON.stringify(state));
  const c=document.getElementById('cur');
  if(c){ c.style.transition='transform .3s'; c.style.transform=
    `translateX(${v==='keep'?600:-600}px) rotate(${v==='keep'?25:-25}deg)`; }
  setTimeout(render,220);
}
function undo(){
  const h=hist.pop(); if(!h) return;
  delete state[h]; localStorage.setItem(KEY,JSON.stringify(state));
  i=DATA.findIndex(d=>d.handle===h); render();
}
function openInsta(){
  if(i>=DATA.length) return;
  window.open(DATA[i].url,'_blank');
}
function attachDrag(el){
  let sx=0,sy=0,dx=0,drag=false;
  const sk=document.getElementById('sk'), sd=document.getElementById('sd');
  const start=e=>{const t=e.touches?e.touches[0]:e;sx=t.clientX;sy=t.clientY;drag=true;el.style.transition='';};
  const move=e=>{if(!drag)return;const t=e.touches?e.touches[0]:e;dx=t.clientX-sx;const dy=t.clientY-sy;
    if(Math.abs(dx)<Math.abs(dy)&&Math.abs(dx)<10)return;
    el.style.transform=`translateX(${dx}px) rotate(${dx/20}deg)`;
    sk.style.opacity=dx>0?Math.min(dx/120,1):0; sd.style.opacity=dx<0?Math.min(-dx/120,1):0;};
  const end=()=>{if(!drag)return;drag=false;
    if(dx>110){decide('keep');} else if(dx<-110){decide('drop');}
    else{el.style.transition='transform .2s';el.style.transform='';sk.style.opacity=0;sd.style.opacity=0;}dx=0;};
  el.addEventListener('touchstart',start,{passive:true});
  el.addEventListener('touchmove',move,{passive:true});
  el.addEventListener('touchend',end);
  el.addEventListener('mousedown',start);
  window.addEventListener('mousemove',move);
  window.addEventListener('mouseup',end);
}
function showDone(){
  document.getElementById('btns').style.display='none';
  const keep=DATA.filter(d=>state[d.handle]==='keep');
  const list=keep.map(d=>`<a href="${d.url}" target="_blank">@${d.handle}</a>`).join('<br>')||'<span style="color:#666">aucune gardée</span>';
  document.getElementById('stage').innerHTML=`<div class="done" style="display:flex">
    <h2>Terminé 🎉</h2>
    <p>${keep.length} gardées sur ${DATA.length}</p>
    <div class="keeplist">${list}</div>
    <button class="cp" onclick="copyKeep()">📋 Copier les @ gardées</button>
    <button class="undo" style="visibility:visible" onclick="hist=['_'];i=0;document.getElementById('btns').style.display='flex';render()">↩︎ revoir</button>
  </div>`;
}
function copyKeep(){
  const keep=DATA.filter(d=>state[d.handle]==='keep').map(d=>'@'+d.handle).join('\n');
  navigator.clipboard.writeText(keep).then(()=>alert('Copié : '+keep.split('\n').length+' @'));
}
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
