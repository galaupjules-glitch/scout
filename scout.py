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
import signal
import subprocess
import sys
import time
from pathlib import Path

from reseau import connexion_ok

# ----------------------------------------------------------------------------
# CONFIG (modifiable en haut de fichier ou via les arguments CLI)
# ----------------------------------------------------------------------------

# Fourchette d'abonnes cible (comptes vitrine, ni trop petits ni deja etablis)
FOLLOWERS_MIN = 1500
FOLLOWERS_MAX = 25000

# Exception rare : au-dessus de FOLLOWERS_MAX, garde quand meme jusqu'a ce plafond SI lien OnlyFans direct en bio
OF_EXCEPTION_MAX = 60000

# Ratio following/followers max. Au-dessus = compte perso/college (pas une vitrine)
MAX_FOLLOWING_RATIO = 1.6

# Engagement minimum : likes sur le post le plus recent. En dessous = followers fantomes.
MIN_POSTS = 6  # au moins 6 publications sur le profil
MAX_MENTIONS_BIO = 2  # au-dessus de 2 @ dans la bio : profil ecarte

# regles apprises de ton tri dans Vivier (apprentissage.py), rechargees a chaque recherche
try:
    import apprentissage as _app
    APPRIS = _app.charger()
except Exception:
    APPRIS = {"mots": [], "domaines": [], "abonnes_min": None}
APPRIS.setdefault("gardes", 0)
APPRIS.setdefault("jetes", 0)

# avant chaque recherche : likes sur N profils types / favoris (1 like chacun) pour entretenir le feed
ECHAUFFEMENT_PROFILS = 3

# liens en bio prioritaires (juste derriere un lien OnlyFans direct)
LINK_PRIORITAIRES = ["hoo.be", "link.me", "linkscale", "linktr.ee", "linktree"]
PRIORITE_LIEN = {"of": 4, "agreg": 3, "autre": 2}  # + 1 si suivie par creatorsinc ; sans lien : 1

MIN_LIKES_PER_POST = 600  # chaque post regarde (hors le dernier) doit faire au moins ca
MIN_LIKES_DERNIER = 100    # le dernier post peut etre sous 600 (recent), mais pas sous ca

# Activite : dernier post (hors posts epingles) il y a 6 mois maximum
MAX_DAYS_SINCE_POST = 183

# Rythme : pause au hasard entre deux profils, et une grande pause tous les 8 a 14 profils
PAUSE_ENTRE_PROFILS = (6, 13)   # secondes (un peu plus rapide qu avant : 8-18)
GRANDE_PAUSE = (45, 110)        # secondes (avant : 60-150)

# Bio qui laisse penser a une mineure -> toujours exclue (18 ans minimum)
MINOR_PATTERNS = [
    r"\b(1[0-7])\s*(y/?o|yo|yrs?|years?\s*old|ans)\b",   # "16 y/o", "17yo", "15 ans"
    r"\b(age|âge)\s*:?\s*1[0-7]\b",
    r"\bclass\s*of\s*'?(20)?(2[7-9]|3\d)\b",              # lycee : promo 2027 et apres
    r"\bhigh\s*school\b|\bhighschool\b|\bfreshman\b|\bsophomore\b|\bminor\b|\blycée\b|\bcollège\b",
    r"\b(9th|10th|11th|12th)\s*grade\b",
]

# Un profil dont on a deroule les 'Comptes similaires' n'est pas re-mine avant N jours
MINE_COOLDOWN_DAYS = 7

# Mots-cles bio qui disqualifient (marques, coachs, business, deja managees...)
BIO_BLACKLIST = [
    "coach", "nutrition", "personal trainer", "nail", "barber", "realtor",
    "crna", "phd", "ambassador", "trad wife", "homestead", "diesel",
    "truck", "photograph", "esthetician", "makeup artist", "mua", "salon",
    "agency", "talent", "management", "repulse", "grail", "fanfix agency",
    "licensed", "cosmo",
    # bios religieuses : pas le profil recherche
    "jesus", "jésus", "in christ", "christ is", "psalm", "bible",  # pas "christ" seul : Christina... "god first", "child of god", "daughter of the king",
    "proverbs", "john 3:16", "philippians", "jeremiah 29", "romans 8", "✝", "🙏🏼✝",
    # bague en bio = fiancee / mariee
    "💍",
    # bios business / "achete mon produit"
    "shop now", "order now", "dm to order", "dm for orders", "free shipping", "worldwide shipping",
    "customer service", "wholesale", "boutique", "online store", "official store", "small business",
    "pre-order", "preorder", "handmade", "llc", "commandez", "livraison offerte", "e-shop", "eshop",
]

# Contenu francais = pas interessant (on cherche des modeles US).
# Mots tres frequents en francais et rares en anglais/espagnol ; 2 occurrences suffisent.
FR_WORDS = [
    "le", "les", "des", "du", "une", "est", "et", "je", "tu", "nous", "vous", "pour", "avec", "dans",
    "sur", "pas", "mais", "qui", "que", "c'est", "j'ai", "mon", "ma", "mes", "ton", "ta", "tes",
    "très", "trop", "été", "ça", "où", "à", "merci", "bonjour", "bisous", "toujours", "jamais",
    "ici", "chez", "aussi", "avoir", "être", "petit", "petite", "belle", "beau", "soir", "demain",
    "moi", "toi", "juste", "vie", "amour",
]
# bio qui dit "je vis en France / Belgique / Suisse / Quebec"
FR_PLACES = [
    "🇫🇷", "🇧🇪", "🇨🇭", "france", "paris", "lyon", "marseille", "bordeaux", "toulouse", "lille",
    "nantes", "montpellier", "strasbourg", "nice 06", "belgique", "bruxelles", "suisse", "genève",
    "québec", "quebec",
]
_FR_RE = re.compile(r"(?<![\w'’])(" + "|".join(re.escape(w) for w in FR_WORDS) + r")(?![\w'’])", re.I)


def is_french(text, places=False):
    """Vrai si le texte est en francais (ou, avec places=True, situe en pays francophone)."""
    t = (text or "").replace("’", "'")
    if places and any(p in t.lower() for p in FR_PLACES):
        return True
    return len(_FR_RE.findall(t)) >= 2


# Categories de compte affichees par Instagram sous le nom (comptes business) -> exclus
CATEGORY_BLACKLIST = [
    "Produit/service", "Product/service", "Shopping et vente au détail", "Shopping & retail",
    "Entreprise locale", "Local business", "Vêtements (marque)", "Clothing (Brand)",
    "Site web de commerce", "E-commerce website", "Media/news company", "Société de médias/actualités",
    "Restaurant", "Agence de marketing", "Marketing agency",
]  # pas "Brand"/"Marque" seuls : beaucoup de modeles ecrivent "brand collabs" dans leur bio

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


# Moteur navigateur : "maison" (navigateur.py, defaut) ou "opencli" (secours).
# Pour repasser sur OpenCLI :  SCOUT_MOTEUR=opencli python3 scout.py ...
MOTEUR = os.environ.get("SCOUT_MOTEUR", "maison")
if MOTEUR == "opencli":
    OPENCLI = find_opencli()
else:
    import navigateur
    SESSION_ID_MAISON = navigateur.SESSION_ID


def run(args, timeout=60):
    """Lance une commande navigateur (maison ou opencli), retourne stdout (str)."""
    if MOTEUR != "opencli":
        return navigateur.run(args)
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
    if MOTEUR != "opencli":
        sys.exit("ERREUR : le Chrome Scout ne demarre pas.\n"
                 "Verifie avec: python3 navigateur.py doctor")
    sys.exit("ERREUR : aucun navigateur connecte au bridge OpenCLI.\n"
             "Ouvre Chrome, connecte-toi au compte scouting, verifie avec: opencli doctor")


def whoami(session):
    out = run(["instagram", "whoami"], timeout=30)
    m = re.search(r"username:\s*([A-Za-z0-9_.]+)", out)
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# Navigation + extraction
# ----------------------------------------------------------------------------

def pause(a, b):
    """Pause au hasard entre a et b secondes : Instagram repere les rythmes trop reguliers/rapides."""
    time.sleep(random.uniform(a, b))


def _sur_profil(user):
    """Vrai si l'onglet affiche deja le profil de user (arrive par un clic depuis le feed)."""
    if MOTEUR == "opencli":
        return False
    try:
        return bool(navigateur.onglet().js(
            "location.pathname.toLowerCase()===%s && !!document.querySelector('main header')"
            % json.dumps(f"/{user.lower()}/"), timeout=10))
    except Exception:
        return False


def open_profile(session, user):
    if _sur_profil(user):  # deja dessus (clic depuis le feed) : pas de rechargement
        pause(1, 2.5)
        return
    run(["browser", session, "open", f"https://www.instagram.com/{user}/"], timeout=40)
    pause(2.5, 5)


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
    if '"ref"' not in out:  # Instagram en anglais
        out = run(["browser", session, "find", "--role", "button",
                   "--name", "Similar accounts", "--limit", "1"], timeout=40)
    m = re.search(r'"ref":\s*(\d+)', out)
    if not m:
        return []
    run(["browser", session, "click", m.group(1)], timeout=30)
    pause(2.5, 5)
    return links_on_page(session, limit=60)


def explore_people(session):
    """Recupere une fournee de comptes suggeres depuis explore/people."""
    run(["browser", session, "open",
         "https://www.instagram.com/explore/people/"], timeout=40)
    pause(2.5, 5)
    for _ in range(6):
        run(["browser", session, "scroll", "down", "--repeat", "2"], timeout=20)
        pause(2, 4)
    return links_on_page(session, limit=250)


# Feed : auteurs des posts + filtre AVANT visite (pas de pub, pas de compte geant).
# Defilement lent et irregulier (2,5 a 5,5 s) pour ressembler a un humain.
FEED_JS = r"""(async()=>{const keep=new Map(),skip=new Set();
const FRW=__FR_WORDS__;const pat="(?<![\\w'’])("+FRW.map(w=>w.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")).join("|")+")(?![\\w'’])";
const FR=new RegExp(pat,"i"),FRG=new RegExp(pat,"gi");
// toujours sur l'onglet "For you" (jamais "Following") : on le selectionne s'il ne l'est pas
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const tab=ts=>[...document.querySelectorAll('[role=tab]')].find(e=>ts.includes(e.textContent.trim()));
const forYou=async()=>{const fy=tab(['For you','Pour vous']);
  if(fy&&fy.getAttribute('aria-selected')!=='true'){fy.click();await wait(3000+Math.random()*1500)}};
await forYou();
// le feed n'est jamais recharge : on reprend la ou on s'etait arrete, en sautant les posts deja vus
if(!window.__sv){window.__sv=new Set();window.scrollTo(0,0)}
// profils types / favoris (deja vus, deja aimes) et posts deja likes : servent de reference, on ne les touche plus
const AIMES=new Set(__AIMES__),aimes=new Set();
const dejaLike=a=>!!a.querySelector('svg[aria-label="Unlike"],svg[aria-label="Je n’aime plus"]');
const num=t=>{const m=t.trim().match(/^(\d[\d\s.,\u202f\u00a0]*)\s*([kKmM])?$/);if(!m)return null;
 let v=parseFloat(m[1].replace(/[\s\u202f\u00a0]/g,'').replace(',','.'));if(m[2])v*=/m/i.test(m[2])?1e6:1e3;
 else v=parseFloat(m[1].replace(/[\s.,\u202f\u00a0]/g,''));return v};
for(let i=0;i<%d;i++){
 if(i%%5===0)await forYou();  // on reverifie regulierement qu'on est bien sur "For you"
 document.querySelectorAll("article").forEach(a=>{
  const l=a.querySelector('header a[href^="/"], a[role=link][href^="/"]');if(!l)return;
  const m=l.getAttribute("href").match(/^\/([A-Za-z0-9_.]+)\/$/);if(!m)return;const h=m[1];
  const pid=h+'|'+((a.querySelector('a[href*="/p/"],a[href*="/reel/"]')||{}).getAttribute?.('href')||a.innerText.slice(0,40));
  if(window.__sv.has(pid))return;window.__sv.add(pid);  // post deja passe lors d'un tour precedent
  // deja like ou deja connu (profil type / favori) : reference seulement -> on passe, sans rien toucher
  if(AIMES.has(h.toLowerCase())||dejaLike(a)){skip.add(h);return}
  const lines=a.innerText.split("\n").slice(1,12);
  if(lines.some(t=>/^(Sponsoris|Sponsored|Publicit)/.test(t.trim()))){skip.add(h);return}
  const likes=lines.map(num).find(v=>v!=null);
  if(likes!=null&&likes>%d){skip.add(h);return}
  // legende en francais -> pas interessant (la legende suit la 2e occurrence du pseudo)
  const all=a.innerText.split("\n").map(t=>t.trim()).filter(Boolean);
  const i2=all.indexOf(h,all.indexOf(h)+1);
  const cap=i2>0?all.slice(i2+1,i2+4).filter(t=>!/^(Voir la traduction|Afficher|Ajouter un commentaire|plus|Voir les|See translation|View all|View more|Add a comment|more)/i.test(t)).join(" "):"";
  if(FR.test(cap)&&(cap.replace(/’/g,"'").match(FRG)||[]).length>=2){skip.add(h);return}
  if(!skip.has(h))keep.set(h,Math.max(keep.get(h)||0,likes||0))});  // likes du post vu dans le feed
 window.scrollBy(0,700+Math.random()*700);
 await new Promise(r=>setTimeout(r,2500+Math.random()*3000))}
const ok=[...keep.entries()].filter(([h])=>!skip.has(h)).sort((x,y)=>y[1]-x[1]);  // plus gros likes d'abord
return "HANDLES:"+ok.map(e=>e[0]).join(",")+" AIMES:"+[...aimes].join(",")+" SKIP:"+skip.size+" LIKES:"+JSON.stringify(Object.fromEntries(ok))})()"""

# profils types + favoris : reference seulement, ignores dans le feed (ni like, ni visite) ; rempli dans main()
AIMES_FEED = set()

# likes du post vu dans le feed, par pseudo (sert de preuve d'engagement + priorite de visite)
FEED_LIKES = {}
# un post du feed a partir de ce nombre de likes : profil TOUJOURS verifie, en priorite
FEED_LIKES_PRIORITE = 1000

# likes au-dessus desquels un post du feed trahit un tres gros compte : pas de visite.
# Large expres : une modele a 10K abonnes peut avoir un post viral a 30-50K likes.
MAX_FEED_LIKES = 10000  # au-dessus de 10 000 likes sur un post du feed : tres rare chez nos profils -> ignore


# Feed, un post a la fois : on descend jusqu'au prochain post interessant (memes filtres que le feed),
# on le marque, et on s'arrete dessus. Les posts deja passes ne sont jamais reexamines.
FEED_NEXT_JS = FEED_JS.replace(
    "  if(!skip.has(h))keep.set(h,Math.max(keep.get(h)||0,likes||0))});  // likes du post vu dans le feed",
    "  if(!skip.has(h)&&!DEJA.has(h.toLowerCase())&&!cible){cible=h;cibleLikes=likes||0;"
    "a.setAttribute('data-scout-cible',h)}});\n if(cible)break;").replace(
    "const AIMES=new Set(__AIMES__),aimes=new Set();",
    "const AIMES=new Set(__AIMES__),aimes=new Set(),DEJA=new Set(__DEJA__);let cible=null,cibleLikes=0;\n"
    "document.querySelectorAll('article[data-scout-cible]').forEach(x=>x.removeAttribute('data-scout-cible'));").replace(
    'return "HANDLES:"',
    "if(cible){const a=document.querySelector('article[data-scout-cible]');a&&a.scrollIntoView({block:'center'});"
    "await new Promise(r=>setTimeout(r,800+Math.random()*1200))}\n"
    "return 'CIBLE:'+(cible||'')+'|'+cibleLikes+' '+\"HANDLES:\"").replace(
    ' document.querySelectorAll("article").forEach(a=>{',
    ' document.querySelectorAll("article").forEach(a=>{if(cible)return;  // les posts suivants attendent leur tour')

# clic sur le nom de la fille dans le post marque -> son profil s'ouvre (comme un humain)
CLIC_PROFIL_JS = r"""(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
const a=document.querySelector('article[data-scout-cible]');if(!a)return 'NON';const h=a.getAttribute('data-scout-cible');
const l=a.querySelector('a[href="/'+h+'/"]');if(!l)return 'NON';l.click();
for(let i=0;i<60;i++){await w(250);if(location.pathname.toLowerCase()==='/'+h.toLowerCase()+'/'&&document.querySelector('main header'))return 'OK'}
return 'NON'})()"""

# retour arriere jusqu'au feed (accueil), la ou on s'etait arrete
RETOUR_FEED_JS = r"""(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
for(let k=0;k<4&&location.pathname!=='/';k++){history.back();for(let i=0;i<30&&location.pathname!=='/';i++)await w(200)}
for(let i=0;i<40;i++){if(location.pathname==='/'&&document.querySelector('article'))return 'OK';await w(250)}
return 'NON'})()"""

FEED_ETAT = {"ouvert": False, "vides": 0}


def feed_prochaine(exclus):
    """Un seul onglet, comme un humain : retour au feed -> descend jusqu'au prochain post interessant ->
    clic sur le nom -> on est sur son profil. Renvoie [pseudo] (ou [] si rien de neuf ce coup-ci)."""
    o = navigateur.onglet()
    if FEED_ETAT["ouvert"]:
        if (o.js(RETOUR_FEED_JS, timeout=30) or "NON") != "OK":
            FEED_ETAT["ouvert"] = False  # feed perdu : on le rouvre
    if not FEED_ETAT["ouvert"] or FEED_ETAT["vides"] >= 4:
        try:  # feed neuf : memoire Instagram videe (sans deconnexion)
            o.cmd("Storage.clearDataForOrigin", {"origin": "https://www.instagram.com",
                  "storageTypes": "local_storage,indexeddb,cache_storage,service_workers"})
        except Exception:
            pass
        o.ouvrir("https://www.instagram.com/")
        pause(4, 7)
        FEED_ETAT.update(ouvert=True, vides=0)
    js = (FEED_NEXT_JS % (6, MAX_FEED_LIKES)).replace("__FR_WORDS__", json.dumps(FR_WORDS)) \
        .replace("__AIMES__", json.dumps(sorted(AIMES_FEED))) \
        .replace("__DEJA__", json.dumps(sorted(h.lower() for h in exclus)))
    try:
        out = o.js(js, timeout=6 * 8 + 60) or ""
    except Exception:
        FEED_ETAT["ouvert"] = False
        return []
    m = re.search(r"CIBLE:([A-Za-z0-9_.]*)\|(\d*)", out)
    h = m.group(1) if m else ""
    if not h or h.lower() in IGNORE or h.lower() in (YOUR_HANDLE, SCOUT_ACCOUNT, CONNECTED_ACCOUNT or ""):
        FEED_ETAT["vides"] += 1
        return []
    FEED_ETAT["vides"] = 0
    if m.group(2):
        FEED_LIKES[h.lower()] = int(m.group(2))
    if (o.js(CLIC_PROFIL_JS, timeout=30) or "NON") != "OK":
        return [h]  # clic rate : Scout ouvrira le profil normalement
    return [h]


def feed_suite(scrolls=3):
    """Continue a descendre dans le feed (onglet dedie, jamais recharge) et renvoie les nouvelles
    autrices interessantes. Scout revient ensuite ici apres chaque profil verifie, la ou il s'etait arrete."""
    f = navigateur.onglet_feed()
    try:
        f.cmd("Page.bringToFront", timeout=10)  # on revient sur le feed
    except Exception:
        pass
    if not FEED_ETAT["ouvert"] or FEED_ETAT["vides"] >= 4:
        # 1re ouverture (ou feed vraiment a sec) : feed neuf, memoire Instagram videe (sans deconnexion)
        try:
            f.cmd("Storage.clearDataForOrigin", {"origin": "https://www.instagram.com",
                  "storageTypes": "local_storage,indexeddb,cache_storage,service_workers"})
        except Exception:
            pass
        f.ouvrir("https://www.instagram.com/")
        pause(4, 7)
        FEED_ETAT.update(ouvert=True, vides=0)
    js = (FEED_JS % (scrolls, MAX_FEED_LIKES)).replace("__FR_WORDS__", json.dumps(FR_WORDS)) \
        .replace("__AIMES__", json.dumps(sorted(AIMES_FEED)))
    try:
        out = f.js(js, timeout=scrolls * 8 + 60) or ""
    except Exception:
        FEED_ETAT["ouvert"] = False  # onglet perdu : on le rouvrira
        return []
    handles = _parse_feed(out)
    FEED_ETAT["vides"] = 0 if handles else FEED_ETAT["vides"] + 1
    try:
        navigateur.onglet().cmd("Page.bringToFront", timeout=10)  # on part verifier les profils
    except Exception:
        pass
    return handles


def _parse_feed(out):
    """Resultat du script du feed -> liste des autrices a verifier (+ likes notes)."""
    m = re.search(r"HANDLES:([A-Za-z0-9_.,]*)", out)
    if not m:
        return []
    lk = re.search(r"LIKES:(\{.*\})", out)
    if lk:
        try:
            FEED_LIKES.update({h.lower(): int(v) for h, v in json.loads(lk.group(1)).items() if v})
        except Exception:
            pass
    clean = []
    for h in m.group(1).split(","):
        hl = h.lower()
        if not h or hl in IGNORE or hl == YOUR_HANDLE or hl == SCOUT_ACCOUNT \
                or (CONNECTED_ACCOUNT and hl == CONNECTED_ACCOUNT):
            continue
        clean.append(h)
    return list(dict.fromkeys(clean))


def feed_authors(session, scrolls=20):
    """Recupere les auteurs des posts du feed 'Pour toi' (FYP) du compte scouting."""
    # feed tout neuf a chaque ouverture : on vide la memoire d'Instagram dans le navigateur
    # (feed deja charge, cache), SANS les cookies -> le compte reste connecte
    if MOTEUR != "opencli":
        try:
            navigateur.onglet().cmd("Storage.clearDataForOrigin", {
                "origin": "https://www.instagram.com",
                "storageTypes": "local_storage,indexeddb,cache_storage,service_workers"})
        except Exception:
            pass
    run(["browser", session, "open", "https://www.instagram.com/"], timeout=40)
    pause(4, 7)
    js = (FEED_JS % (scrolls, MAX_FEED_LIKES)).replace("__FR_WORDS__", json.dumps(FR_WORDS)) \
        .replace("__AIMES__", json.dumps(sorted(AIMES_FEED)))
    out = run(["browser", session, "eval", js], timeout=scrolls * 7 + 60)
    m = re.search(r"HANDLES:([A-Za-z0-9_.,]*)", out)
    if not m:
        return []
    lk = re.search(r"LIKES:(\{.*\})", out)
    if lk:
        try:
            FEED_LIKES.update({h.lower(): int(v) for h, v in json.loads(lk.group(1)).items() if v})
        except Exception:
            pass
    sk = re.search(r"SKIP:(\d+)", out)
    if sk and int(sk.group(1)):
        print(f"  ... feed : {sk.group(1)} comptes ecartes sans visite (pub, francais, trop gros ou deja connus)")
    am = re.search(r"AIMES:([A-Za-z0-9_.,]*)", out)
    if am and am.group(1):
        print("  ... ♥ 1 like dans le feed (sans ouvrir le compte) : "
              + ", ".join("@" + h for h in am.group(1).split(",")))
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


BIO_LINK_JS = r"""(()=>{const h=document.querySelector('main header');if(!h)return 'LINK:';
for(const a of h.querySelectorAll('a[href]')){let u=a.href;
  try{const p=new URL(u);if(p.hostname==='l.instagram.com')u=p.searchParams.get('u')||u;}catch(e){}
  if(/^https?:\/\//.test(u)&&!/(^|\.)(instagram|threads)\.(com|net)\//.test(u.replace(/^https?:\/\//,'')+'/'))return 'LINK:'+u;}
const t=h.textContent.match(/(?:Icône de lien|Link icon)\s*([^\s]+)/);return 'LINK:'+(t?t[1]:'')})()"""


PRIVATE_JS = r"""(()=>{const m=document.querySelector('main');
return 'PRIVE:'+(m&&/Ce compte est privé|This account is private|Ce compte est priv/.test(m.innerText)?1:0)})()"""


def is_private(session):
    """Vrai si le profil courant est un compte prive."""
    return "PRIVE:1" in run(["browser", session, "eval", PRIVATE_JS], timeout=30)


def bio_link(session):
    """Lien en bio du profil courant -> (type, lien). type: 'of' (OnlyFans direct), 'autre', ou ''."""
    out = run(["browser", session, "eval", BIO_LINK_JS], timeout=30)
    m = re.search(r"LINK:(\S*)", out)
    link = m.group(1).strip() if m else ""
    if not link or re.search(r"(^|\.|//)(instagram|threads)\.(com|net)", link.lower()):
        return "", ""
    l = link.lower()
    if "onlyfans.com" in l:
        return "of", link
    if any(d in l for d in LINK_PRIORITAIRES):
        return "agreg", link  # hoo.be, link.me, linkscale, linktree : prioritaires
    return "autre", link


def avatar_datauri(session, user, shots):
    """Photo de profil du profil courant, en petite vignette ronde-ready (data URI jpeg ~10 Ko)."""
    out = run(["browser", session, "eval",
               "(document.querySelector('header img')||{}).src||''"], timeout=30)
    m = re.search(r"https://\S+", out)
    if not m:
        return ""
    raw = shots / f"{user}.avatar"
    jpg = shots / f"{user}.avatar.jpg"
    try:
        import urllib.request
        req = urllib.request.Request(m.group(0), headers={"User-Agent": "Mozilla/5.0"})
        raw.write_bytes(urllib.request.urlopen(req, timeout=20).read())
        subprocess.run(["sips", "-Z", "200", "-s", "format", "jpeg", "-s", "formatOptions", "70",
                        str(raw), "--out", str(jpg)], capture_output=True, timeout=30)
        return "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()
    except Exception:
        return ""
    finally:
        raw.unlink(missing_ok=True)


# 1er post de la grille qui n'est PAS epingle (un post epingle peut dater de 3 ans)
LATEST_POST_JS = r"""(()=>{const as=[...document.querySelectorAll('main a[href*="/p/"],main a[href*="/reel/"]')];
const pinned=a=>a.querySelector('svg[aria-label*="pingl" i],svg[aria-label*="pinned" i]');
const ok=as.filter(x=>!pinned(x));return 'POSTS:'+(ok.length?ok:as).slice(0,3).map(a=>a.getAttribute('href')).join(' ')})()"""

# date du post ouvert : <time datetime> (visible) ou "taken_at" du JSON de la page
POST_DATE_JS = r"""(()=>{const t=document.querySelector('main time[datetime],article time[datetime],time[datetime]');
if(t)return 'DATE:'+Math.floor(Date.parse(t.getAttribute('datetime'))/1000);
const m=document.body.textContent.match(/taken_at\\?"\s*:\s*(\d{10})/);return 'DATE:'+(m?m[1]:'')})()"""


def latest_post(session, user):
    """Ouvre le dernier post (hors epingles) : renvoie (likes ou None, timestamp du post ou None)."""
    open_profile(session, user)
    out = run(["browser", session, "eval", LATEST_POST_JS], timeout=40)
    posts = re.findall(r"/(p|reel)/([A-Za-z0-9_-]+)", out.split("POSTS:", 1)[-1])
    if not posts:
        return None, None
    likes = posted = None
    for i, (kind, code) in enumerate(posts[:2]):
        run(["browser", session, "open",
             f"https://www.instagram.com/{kind}/{code}/"], timeout=40)
        pause(2.5, 5)
        d = re.search(r"DATE:(\d{10})", run(["browser", session, "eval", POST_DATE_JS], timeout=30))
        ts = int(d.group(1)) if d else None
        if i == 0:
            posted = ts
        likes = _likes_on_post_page(session)
        # un post de moins de 2 jours n'a pas encore ses likes -> on mesure sur le precedent
        if not ts or time.time() - ts > 2 * 86400:
            break
    return likes, posted


# sur le profil : clic sur un post de la grille -> il s'ouvre dans sa fenetre ; on lit likes + date
OUVRIR_POST_JS = r"""(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
const a=document.querySelector('main a[href=%s]');if(!a)return 'NON';a.click();
for(let i=0;i<40;i++){await w(250);const d=document.querySelector('div[role="dialog"]')||(/\/(p|reel)\//.test(location.pathname)?document.querySelector('main'):null);
 if(d&&/\d/.test(d.innerText)&&d.querySelector('time[datetime]')){
  const t=d.innerText;const m=t.match(/([\d.,]+\s*[KkMm]?)\s+likes?\b/);
  const tm=d.querySelector('time[datetime]');
  return 'LIKES:'+(m?m[1].replace(/\s/g,''):'')+' DATE:'+Math.floor(Date.parse(tm.getAttribute('datetime'))/1000)}}
return 'NON'})()"""

RETOUR_JS = r"""(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));const avant=location.pathname;history.back();
for(let i=0;i<40;i++){await w(250);if(location.pathname!==avant)return 'OK'}return 'NON'})()"""


def _nombre_likes(txt):
    """'1,234' -> 1234 ; '12.5K' -> 12500."""
    t = txt.strip().upper()
    mult = 1000 if t.endswith("K") else 1000000 if t.endswith("M") else 1
    t = t.rstrip("KM")
    try:
        return int(float(t.replace(",", "")) * mult) if mult > 1 else int(re.sub(r"[.,]", "", t))
    except ValueError:
        return None


def recent_likes_clic(user, n=3):
    """Sur le profil (arrive par un clic) : clique ses n derniers posts un par un, lit les likes dans la
    fenetre du post, puis revient au profil. Aucun chargement de page."""
    o = navigateur.onglet()
    out = o.js(LATEST_POST_JS) or ""   # posts de la grille, epingles (icone) deja retires
    hrefs = out.split("POSTS:", 1)[-1].split()
    vus = []  # (date, likes) dans l'ordre de la grille
    for href in hrefs[:n + 1]:  # 1 post de plus : filet de securite si un epingle n'a pas d'icone
        r = o.js(OUVRIR_POST_JS % json.dumps(href), timeout=30) or "NON"
        if r == "NON":
            continue
        m = re.search(r"LIKES:(\S*)", r)
        d = re.search(r"DATE:(\d{10})", r)
        vus.append((int(d.group(1)) if d else 0, _nombre_likes(m.group(1)) if m and m.group(1) else None))
        pause(1.5, 3.5)  # regarde le post
        o.js(RETOUR_JS, timeout=20)  # ferme le post : retour au profil
        pause(0.8, 1.8)
    # un post plus ancien que celui qui le suit dans la grille = post epingle -> on l'ignore
    normaux = [v for i, v in enumerate(vus) if all(v[0] >= w[0] for w in vus[i + 1:])]
    normaux = normaux[:n]
    likes = [l for _, l in normaux]
    posted = normaux[0][0] if normaux and normaux[0][0] else None
    return likes, posted


def recent_likes(session, user, n=3):
    """Ouvre les n derniers posts (hors epingles) : renvoie ([likes du plus recent au plus ancien], date du dernier)."""
    if _sur_profil(user):  # arrive par un clic depuis le feed : on clique les posts, sans recharger
        return recent_likes_clic(user, n)
    open_profile(session, user)
    out = run(["browser", session, "eval", LATEST_POST_JS], timeout=40)
    posts = re.findall(r"/(p|reel)/([A-Za-z0-9_-]+)", out.split("POSTS:", 1)[-1])
    likes, posted = [], None
    for i, (kind, code) in enumerate(posts[:n]):
        run(["browser", session, "open", f"https://www.instagram.com/{kind}/{code}/"], timeout=40)
        pause(2.5, 5)
        if i == 0:
            d = re.search(r"DATE:(\d{10})", run(["browser", session, "eval", POST_DATE_JS], timeout=30))
            posted = int(d.group(1)) if d else None
        likes.append(_likes_on_post_page(session))
    return likes, posted


def engagement(likes, posted, feed_like=None):
    """Regle des likes (ok, raison, likes typiques) :
    - le dernier post peut etre sous 600 (normal s'il est recent) mais pas sous 100 ;
    - les autres posts regardes (et le post vu dans le feed) doivent tous faire >= 600."""
    connus = [x for x in likes if x is not None]
    typique = sorted(connus)[len(connus) // 2] if connus else feed_like
    if not connus and not feed_like:
        return True, "ok (likes masques)", None
    dernier = likes[0] if likes else None
    recent = posted and time.time() - posted < 2 * 86400
    if dernier is not None and dernier < MIN_LIKES_DERNIER and not recent:
        return False, f"engagement faible (dernier post {dernier} likes)", typique
    # le post vu dans le feed ne compte qu'en positif (il est souvent tout recent, likes pas encore la)
    autres = [x for x in likes[1:] if x is not None] + \
        ([feed_like] if feed_like and feed_like >= MIN_LIKES_PER_POST else [])
    if not autres:  # un seul post mesurable : il doit faire 600 lui-meme
        autres = [dernier] if dernier is not None else []
    faibles = [x for x in autres if x < MIN_LIKES_PER_POST]
    if faibles:
        return False, f"engagement faible ({'/'.join(str(x) for x in connus)} likes)", typique
    return True, "ok", typique


def first_post_likes(session, user):
    """Nb de likes du dernier post (hors epingles), ou None."""
    return latest_post(session, user)[0]


def _likes_on_post_page(session):
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
    """'6 406' -> 6406, '4,084' -> 4084, '10,8 k' / '10.8K' -> 10800, '1,5 m' -> 1500000."""
    s = s.strip().lower().replace("\u202f", " ").replace("\xa0", " ")
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1000000, s[:-1]
    s = s.replace(" ", "")
    if mult == 1:   # nombre entier : virgules/points = separateurs de milliers (FR ou EN)
        s = re.sub(r"[.,]", "", s)
    else:           # 10,8 k (FR) ou 10.8K (EN) : decimale
        s = s.replace(",", ".")
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def parse_header(text):
    """Extrait posts, followers, following, bio, statut suivi, verifie (Instagram en FR ou en EN)."""
    info = {"raw": text}
    m = re.search(
        r"([\d.,\s]+?)\s*(?:publications?|posts?)\s*"
        r"([\d.,\s]*[km]?)\s*followers?\s*"
        r"([\d.,\s]*[km]?)\s*(?:suivi|following)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    info["posts"] = to_number(m.group(1))
    info["followers"] = to_number(m.group(2))
    info["following"] = to_number(m.group(3))
    info["verified"] = any(w in text for w in ("Vérifié", "Verifie", "Verified"))
    # ligne "Suivi(e) par X, Y et N autres" / "Followed by X, Y and N others"
    # (= comptes suivis par le compte scouting)
    suivis = ""
    for lab in ("Suivi(e) par", "Followed by"):
        if lab in text:
            suivis = text.split(lab, 1)[1][:200]
            break
    info["followed_by_you"] = bool(YOUR_HANDLE) and YOUR_HANDLE.lower() in suivis.lower()
    # suivie par creatorsinc (plus grosse agence) : bon signal, souvent de petits profils prometteurs
    info["creatorsinc"] = "creatorsinc" in suivis.lower()
    # ce qui suit le compteur d'abonnements : bio puis boutons
    tail = text[m.end():]
    # bouton "Suivi(e)" / "Following" (= le compte scouting suit deja) vs "Suivre" / "Follow"
    info["followed_by_account"] = bool(
        re.search(r"Suivi\(e\)(Icône|Contacter|Comptes)|Following(Message|Similar|Link)|Requested", tail)
    )
    bio = tail
    for cut in ["Suivre", "Suivi(e)", "Contacter", "Comptes similaires", "Icône de lien",
                "Followed by", "Following", "Follow", "Message", "Similar accounts", "Link icon", "Threads"]:
        bio = bio.split(cut)[0]
    info["bio"] = bio.strip()
    return info


def passes(info, of_exception=False):
    """Retourne (ok: bool, raison: str)."""
    if not info or info.get("followers") is None:
        return False, "stats illisibles"
    f = info["followers"]
    if f < FOLLOWERS_MIN:
        return False, f"trop petit ({f})"
    if f > FOLLOWERS_MAX and not (of_exception and f <= OF_EXCEPTION_MAX):
        return False, f"trop gros ({f})"
    if info.get("following") and f and info["following"] / max(f, 1) > MAX_FOLLOWING_RATIO:
        return False, "ratio perso/college"
    if info.get("followed_by_account") or info.get("followed_by_you"):
        return False, "deja suivi (deja contacte)"
    raw = info.get("raw", "")
    for cat in CATEGORY_BLACKLIST:  # mot entier : "Marque" ne doit pas matcher "Remarque"
        if re.search(r"(?<![A-Za-zÀ-ÿ])" + re.escape(cat) + r"(?![A-Za-zÀ-ÿ])", raw):
            return False, f"compte business ({cat})"
    if is_french(info.get("bio", ""), places=True):
        return False, "contenu francais"
    mentions = re.findall(r"@[A-Za-z0-9_.]+", info.get("bio", ""))
    if len(mentions) > MAX_MENTIONS_BIO:  # trop de @ en bio (marques, ecole, agences...)
        return False, f"trop de @ en bio ({len(mentions)})"
    # appris de ton tri : comme tes jetees, jamais comme tes gardees
    if APPRIS.get("abonnes_min") and f < APPRIS["abonnes_min"] and not of_exception:
        return False, f"appris : plus petit que tes gardees ({f})"
    bio_mots = set(re.findall(r"[a-zà-ÿ]{3,}", info.get("bio", "").lower()))
    mot = next((m for m in APPRIS.get("mots", []) if m in bio_mots), None)
    if mot:
        return False, f"appris : bio comme tes jetees ({mot})"
    raw_l = info.get("raw", "").lower()
    dom = next((d for d in APPRIS.get("domaines", []) if d and d in raw_l), None)
    if dom:
        return False, f"appris : lien comme tes jetees ({dom})"
    bio_l = info.get("bio", "").lower()
    for pat in MINOR_PATTERNS:
        if re.search(pat, bio_l):
            return False, "possiblement mineure (bio)"
    for kw in BIO_BLACKLIST:
        if kw in bio_l:
            return False, f"bio: {kw}"
    if info.get("posts") is not None and info["posts"] < MIN_POSTS:
        return False, f"pas assez de posts ({info['posts']})"
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
    ap.add_argument("--suggestions", action="store_true",
                    help="ancienne methode : suggestions des profils types (par defaut : feed uniquement)")
    ap.add_argument("--out", type=str, default="resultats", help="dossier de sortie")
    ap.add_argument("--max-visites", type=int, default=0,
                    help="arrete apres N profils visites (quota Instagram du jour, 0 = pas de limite)")
    args = ap.parse_args()
    # mode par defaut : FEED uniquement (le compte scouting suit les profils types -> son feed
    # en est rempli ; chaque auteur du feed est verifie, puis retour au feed, en boucle)
    args.similaires = args.suggestions
    args.fyp = not args.suggestions
    started = time.time()
    statut = "termine"

    FOLLOWERS_MIN, FOLLOWERS_MAX = args.min, args.max
    out = Path(__file__).parent / args.out
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    ok_net, msg_net = connexion_ok()  # jamais sur l'internet normal : ca ferait reperer le compte
    if not ok_net:
        print("BLOQUE : " + msg_net)
        log_search(out / "recherches.json", {
            "debut": time.strftime("%Y-%m-%dT%H:%M:%S"), "duree_min": 0, "demandes": args.target,
            "gardes": 0, "visites": 0, "min": FOLLOWERS_MIN, "max": FOLLOWERS_MAX,
            "statut": "reseau", "credits_claude": 0})
        sys.exit(2)
    session = get_session()
    CONNECTED_ACCOUNT = (whoami(session) or "").lower()
    if not CONNECTED_ACCOUNT and MOTEUR != "opencli":
        sys.exit("ERREUR : pas connecte a Instagram dans le Chrome Scout.\n"
                 "Connecte-toi au compte scouting dans la fenetre Chrome Scout, puis relance.")
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
    # graines = profils types (suivis par jeannechartre, profils_types.txt) + config
    types_file = Path(__file__).parent / "profils_types.txt"
    types = [l.strip().lstrip("@").lower() for l in types_file.read_text().splitlines()
             if l.strip() and not l.startswith("#")] if types_file.exists() else []
    # Vivier : tes "♥ Gardees" deviennent des profils types ; toute fille deja dans Vivier
    # (nouvelle, vue, jetee, gardee) ne sera plus jamais reproposee
    deja_vivier = set()
    try:
        import vivier_live
        choix = vivier_live.lire_choix() or []
        deja_vivier = {h for h, _, _ in choix}
        nouveaux_types = [h for h, _, fav in choix if fav and h not in types]
        if nouveaux_types:
            with open(types_file, "a", encoding="utf-8") as f:
                f.write("\n".join(nouveaux_types) + "\n")
            types += nouveaux_types
            print(f"{len(nouveaux_types)} favoris de Vivier ajoutes aux profils types : "
                  + ", ".join("@" + h for h in nouveaux_types))
        if deja_vivier:
            print(f"{len(deja_vivier)} profils deja dans Vivier : jamais reproposes.")
        # apprentissage : ton tri (♥ / jetes) -> regles recalculees a chaque recherche
        import apprentissage
        APPRIS.update(apprentissage.apprendre(choix))
        print(f"Appris de ton tri ({APPRIS['gardes']} gardees, {APPRIS['jetes']} jetees) : "
              f"{len(APPRIS['mots'])} mots de bio et {len(APPRIS['domaines'])} liens a eviter"
              + (f", minimum {APPRIS['abonnes_min']} abonnes" if APPRIS.get("abonnes_min") else "") + ".")
    except Exception as e:
        print(f"(lecture de Vivier impossible : {e})")
    base = [s.strip() for s in args.seeds.split(",") if s.strip()] or (types + list(DEFAULT_SEEDS))
    pool = list(dict.fromkeys(base + sorted(known)))
    random.shuffle(pool)
    seeds = [h for h in pool if not recently_mined(h)] if args.similaires else []
    if args.similaires:
        print(f"Mode suggestions : {len(seeds)} profils de depart frais (sur {len(pool)}), "
              "puis suggestions des profils gardes.\n")
    else:
        print("Mode FYP : recolte sur le feed 'Pour toi' du compte scouting.\n")
    queue = list(seeds)
    seen = set(s.lower() for s in pool)
    seed_set = set(h.lower() for h in seeds)  # profils de depart : on deroule leurs suggestions
    kept = []          # leads retenus (dicts)
    examined = 0
    next_break = random.randint(10, 16)  # prochaine grande pause (en profils visites)
    rapide = False  # le profil precedent a ete ecarte tout de suite -> on enchaine vite

    rot = {"list": random.sample(types, len(types)), "i": 0, "vides": 0}  # rotation des profils types
    prof = {"scrolls": 15}  # profondeur de defilement du feed (augmente si rien de nouveau)
    nuls = 0  # profils pas interessants d'affilee (mode feed -> reactualiser le feed a 3)

    def harvest_more():
        """File vide (ou branche trop petite) : on repart d'un profil type, en boucle.
        Renvoie False quand plus rien de nouveau nulle part (tous les profils types + explore)."""
        if args.fyp:  # mode feed : le feed "For you" de l'accueil + ses suggestions. JAMAIS les Reels.
            def ajoute(handles):
                n = 0
                for h in handles:
                    if h.lower() not in seen and h.lower() not in done:
                        seen.add(h.lower())
                        queue.append(h)
                        n += 1
                return n
            # on descend un peu dans le feed (onglet dedie, jamais recharge), puis on va verifier les
            # profils trouves, puis on revient au feed la ou on s'etait arrete -> en boucle
            if MOTEUR != "opencli":
                new = ajoute(feed_prochaine(done | seen))  # un post a la fois, clic sur le profil
            else:
                new = ajoute(feed_authors(session, scrolls=prof["scrolls"]))
            print(f"  ... feed : {new} nouveaux profils a verifier")
            return True  # on y retourne toujours
        for _ in range(min(3, len(rot["list"]))):
            t = rot["list"][rot["i"] % len(rot["list"])]
            rot["i"] += 1
            new = 0
            for h in similar_accounts(session, t):
                if h.lower() not in seen and h.lower() not in done:
                    seen.add(h.lower())
                    queue.append(h)
                    new += 1
            print(f"  ... reprise depuis le profil type @{t} : {new} nouvelles suggestions")
            if new:
                rot["vides"] = 0
                return True
            rot["vides"] += 1
        if rot["vides"] < len(rot["list"]):
            return True  # il reste des profils types pas encore retentes dans ce tour
        sources = ([("FYP", feed_authors)] if args.fyp else []) + [("explore/people", explore_people)]
        for name, fn in sources:
            new = 0
            for h in fn(session):
                if h.lower() not in seen and h.lower() not in done:
                    seen.add(h.lower())
                    queue.append(h)
                    new += 1
            print(f"  ... {name} : {new} nouveaux profils a verifier")
            if new:
                return True
        return False
    empty_harvests = 0
    petits = 0  # profils trop petits d'affilee : la branche de suggestions s'epuise

    def save_outputs():
        """Ecrit toutes les sorties + known.txt (appele a chaque lead et en fin de run)."""
        (out / "candidates.json").write_text(
            json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv(out / "leads.csv", kept)
        write_review(out / "review.html", kept)              # version ordi (photos = dossier shots/)
        write_mobile(out / "mobile.html", kept, shots)       # version autonome pour le tel
        write_cloud_export(out / "export-vivier.json", kept, shots)  # a importer dans l'artifact Vivier
        add_to_library(out / "bibliotheque.json", kept)      # tous les gardes, tous runs confondus (app.py)
        # memorise tous les @ connus pour que le prochain run ne ramene que du nouveau
        all_known = known | set(k["handle"].lower() for k in kept)
        known_file.write_text("\n".join(sorted(all_known)), encoding="utf-8")

    idx = 0
    done = set(known) | rejected  # connus + deja qualifies : recoltes mais jamais re-exportes
    done |= set(types)  # profils types = deja contactes (suivis par jeannechartre) : graines seulement
    done |= deja_vivier  # deja dans Vivier : jamais la meme fille deux fois
    dry = 0            # rejets consecutifs (detection de tarissement/throttle)
    # tes profils types / favoris : deja vus et aimes -> reference seulement ; croises dans le feed,
    # ils sont ignores (ni like, ni visite)
    AIMES_FEED.update(types)

    # bouton Arreter (app / Vivier) : SIGTERM -> arret propre, on garde ce qui a ete trouve
    signal.signal(signal.SIGTERM, _stop_requested)
    try:
        while len(kept) < args.target:
            if not connexion_ok()[0]:  # le Mac a quitte le partage de connexion en cours de route
                print("  Connexion normale detectee : arret immediat pour proteger le compte.")
                statut = "reseau"
                break
            if args.max_visites and examined >= args.max_visites:
                print(f"  Quota Instagram du jour atteint ({examined} profils visites). "
                      "Arret pour proteger le compte, relance demain.")
                statut = "quota"
                break
            if idx >= len(queue):
                before = len(queue)
                encore = harvest_more()
                if len(queue) == before and args.fyp:
                    # feed sans rien de nouveau : on laisse Instagram le renouveler, puis on y retourne
                    empty_harvests += 1
                    # rien de neuf sur ces quelques posts : on continue simplement a descendre.
                    # Seulement si le feed est vraiment a sec (rouvert plusieurs fois), petite pause
                    # pour laisser Instagram le renouveler. Jamais d'arret avant l'objectif.
                    if empty_harvests >= 8:
                        b = random.uniform(60, 150)
                        print(f"  ... le feed ne donne plus rien de neuf, pause de {int(b)} s puis on reprend")
                        time.sleep(b)
                        empty_harvests = 0
                    continue
                if len(queue) == before:
                    if not encore:  # tous les profils types + explore ne donnent plus rien de nouveau
                        empty_harvests += 1
                        if empty_harvests >= 2:
                            print("  Plus aucune suggestion nouvelle : ajoute des profils types et relance.")
                            statut = "epuise"
                            break
                    continue
                empty_harvests = 0
            user = queue[idx]
            idx += 1
            if examined:  # rythme humain entre deux profils + grande pause reguliere
                # profil precedent ecarte sur ses chiffres -> on enchaine vite (comme un humain qui zappe)
                pause(*((1, 2.5) if rapide else PAUSE_ENTRE_PROFILS))
                if examined >= next_break:
                    b = random.uniform(*GRANDE_PAUSE)
                    print(f"  ... pause de {int(b)} s (rythme humain)")
                    time.sleep(b)
                    next_break = examined + random.randint(10, 16)

            # 1) recolte les comptes similaires de ce profil (BFS) -> nouveaux candidats
            if args.similaires and user.lower() in seed_set and not recently_mined(user):
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
            # exception : 25K-60K abonnes avec un lien OnlyFans direct -> on la garde quand meme
            of_exc = bool(info and info.get("followers") and FOLLOWERS_MAX < info["followers"] <= OF_EXCEPTION_MAX
                          and bio_link(session)[0] == "of")
            ok, why = passes(info, of_exception=of_exc)
            if ok and is_private(session):  # compte prive : on ne voit pas son contenu -> pas interessant
                ok, why = False, "compte prive"
            rapide = not ok  # ecartee des le haut du profil (abonnes, ratio, bio...) : retour feed sans attendre
            if ok and of_exc:
                why = f"ok (exception : {info['followers']} abonnes mais lien OF direct)"
            likes = posted = None
            if ok:  # ne verifie l'engagement que si les stats passent (economie)
                likes_posts, posted = recent_likes(session, user)
                age_j = int((time.time() - posted) / 86400) if posted else None
                if age_j is not None and age_j > MAX_DAYS_SINCE_POST:
                    ok, why = False, f"inactive (dernier post il y a {age_j // 30} mois)"
                    likes = None
                else:
                    ok, why, likes = engagement(likes_posts, posted, FEED_LIKES.get(user.lower()))
            tag = "GARDE" if ok else "jette"
            fol = info.get("followers") if info else "?"
            lk = f"{likes} likes" if likes is not None else "likes ?"
            print(f"[{len(kept)}/{args.target}] {tag:5} @{user}  ({fol} abo, {lk})  {why}")
            if ok:
                shot = shots / f"{user}.png"
                open_profile(session, user)  # revient sur le profil pour la capture
                screenshot(session, user, shot)
                lien_type, lien = bio_link(session)
                if lien_type == "of":
                    print("        ⭐ lien OnlyFans direct en bio : prioritaire")
                kept.append({
                    "lien_type": lien_type,  # 'of' = OnlyFans direct (priorite max), 'autre' = linktree/link.me...
                    "lien": lien,
                    "priorite": PRIORITE_LIEN.get(lien_type, 1) + (1 if info.get("creatorsinc") else 0),
                    "creatorsinc": bool(info.get("creatorsinc")),
                    "dernier_post": time.strftime("%Y-%m-%d", time.localtime(posted)) if posted else None,
                    "handle": user,
                    "followers": info["followers"],
                    "following": info.get("following"),
                    "posts": info.get("posts"),
                    "likes": likes,
                    "verified": info.get("verified"),
                    "bio": info.get("bio", ""),
                    "url": f"https://www.instagram.com/{user}/",
                    "shot": f"shots/{user}.png",
                    "avatar": avatar_datauri(session, user, shots),
                })
                dry = petits = nuls = 0
                save_outputs()  # sauvegarde a chaque lead : un arret ne perd rien
                vivier_direct(kept, en_cours=True)  # le profil apparait tout de suite dans Vivier
                # boule de neige : les suggestions d'une fille gardee passent en tete de file
                if args.similaires and not recently_mined(user):
                    new = 0
                    for h in similar_accounts(session, user):
                        if h.lower() not in seen and h.lower() not in done:
                            seen.add(h.lower())
                            queue.insert(idx + new, h)
                            new += 1
                    mined[user.lower()] = time.time()
                    mined_file.write_text(json.dumps(mined), encoding="utf-8")
                    print(f"        + {new} suggestions de @{user} ajoutees")
            else:
                # tout profil deja ouvert est memorise : Scout n'y retourne jamais
                rejected.add(user.lower())
                with open(rejected_file, "a", encoding="utf-8") as f:
                    f.write(user.lower() + "\n")
                dry += 1
                # branche qui descend sous la fourchette (comptes trop petits) -> on la laisse tomber
                # et on repart d'un profil type
                petits = petits + 1 if why.startswith("trop petit") else 0
                # (ancien mode : 3 profils pas interessants -> on rechargeait le feed. Plus maintenant :
                #  le feed reste ouvert et on continue a descendre, sinon Instagram bloque sur 4 posts)
                nuls += 1
                if args.fyp and nuls >= 3 and idx < len(queue) and MOTEUR == "opencli":
                    # on garde quand meme les profils vus avec un gros post dans le feed (>= 1000 likes)
                    gros = [h for h in queue[idx:] if FEED_LIKES.get(h.lower(), 0) >= FEED_LIKES_PRIORITE]
                    print("  3 profils pas interessants d'affilee : on reactualise le feed"
                          + (f" (on garde {len(gros)} profils a gros likes)" if gros else "") + ".")
                    for h in queue[idx:]:  # pas vus : ils pourront revenir plus tard dans le feed
                        if h not in gros:
                            seen.discard(h.lower())
                    queue[idx:] = gros
                    nuls = 0
                if petits >= 5 and args.similaires:
                    print("  Suggestions trop petites (< "
                          f"{FOLLOWERS_MIN} abonnes) : on abandonne cette branche, reprise depuis un profil type.")
                    del queue[idx:]
                    petits = 0

    except KeyboardInterrupt:
        print("  Recherche arretee a ta demande.")
        statut = "arrete"
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # laisse finir la sauvegarde

    save_outputs()
    log_search(out / "recherches.json", {
        "debut": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "duree_min": round((time.time() - started) / 60, 1),
        "demandes": args.target, "gardes": len(kept), "visites": examined,
        "min": FOLLOWERS_MIN, "max": FOLLOWERS_MAX, "statut": statut,
        "credits_claude": 0,  # Scout tourne en local : aucun token consomme
    })
    vivier_direct([], en_cours=False)  # panneau Vivier a jour (recherche terminee + historique)

    print(f"\nTermine. {len(kept)} leads retenus sur {examined} profils examines.")
    print(f"  -> {out/'leads.csv'}")
    print(f"  -> ORDI : ouvre {out/'review.html'} pour swiper")
    print(f"  -> TEL  : envoie {out/'mobile.html'} sur ton telephone (AirDrop), autonome")
    print(f"  -> CLOUD: importe {out/'export-vivier.json'} dans ton artifact Vivier (synchro partout)")


def _stop_requested(*_):
    raise KeyboardInterrupt


def vivier_direct(kept, en_cours):
    """Envoie le dernier profil garde + la progression dans la page Vivier (onglet du Chrome Scout).
    Sans effet si la page n'est pas accessible (pas connecte a claude.ai) : on continue sans."""
    try:
        import preparer_vivier
        import vivier_live
        st = preparer_vivier.stats()
        if en_cours:
            st["gardes_jour"] += len(kept)  # la recherche en cours n'est pas encore dans l'historique
        st.update(en_cours=en_cours, maj=time.strftime("%Y-%m-%dT%H:%M:%S"))
        docs = [preparer_vivier.build_doc(kept[-1])] if kept else []
        n = vivier_live.push(docs, st)
        if docs and n:
            print("        → ajoute dans Vivier")
        elif docs and n is None:
            print("        (Vivier pas accessible : connecte-toi a claude.ai dans Chrome Scout)")
    except Exception as e:
        print(f"        (envoi Vivier impossible : {e})")


def log_search(path, entry):
    """Ajoute une recherche a l'historique (affiche dans l'app et sur Vivier)."""
    try:
        hist = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        hist = []
    hist.append(entry)
    path.write_text(json.dumps(hist[-200:], ensure_ascii=False, indent=1), encoding="utf-8")


def add_to_library(path, rows):
    """Ajoute les leads gardes a la bibliotheque permanente (jamais videe d'un run a l'autre)."""
    lib = {}
    if path.exists():
        try:
            lib = {d["handle"].lower(): d for d in json.loads(path.read_text(encoding="utf-8"))}
        except Exception:
            pass
    today = time.strftime("%Y-%m-%d")
    for r in rows:
        h = r["handle"].lower()
        d = dict(lib.get(h, {}))
        d.update(r)
        d.setdefault("added", today)
        lib[h] = d
    path.write_text(json.dumps(list(lib.values()), ensure_ascii=False, indent=2), encoding="utf-8")


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
