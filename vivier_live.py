#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Envoi EN DIRECT des profils gardes dans la page "Vivier Scout" (0 credit Claude).

La page Vivier est ouverte dans un onglet du Chrome Scout (ou tu es connecte a claude.ai).
Scout ecrit dans la base de la page en executant, dans cet onglet, le meme code que la
page utilise elle-meme (window.claude.use('db')). Rien ne passe par Claude.

    python3 vivier_live.py test     -> verifie que l'envoi direct est possible
"""
import json
import time
import urllib.request

import navigateur

ARTIFACT_URL = "https://claude.ai/artifact/NFCPbTJxwyqGZ4XBDodzBv"
PORT = navigateur.PORT
_CONN = None

PROBE_JS = "(async()=>{try{const db=await window.claude?.use?.('db');return db?'DB':'NODB'}catch(e){return 'ERR'}})()"

PUSH_JS = r"""(async()=>{const db=await window.claude.use('db');if(!db)return 'NODB';
const DOCS=__DOCS__, STATS=__STATS__;let n=0;
for(const d of DOCS){const ref=db.collection('profils').doc(d.handle);
  if((await ref.get()).exists)continue;await ref.set(d);n++}
if(STATS)await db.doc('stats/scout').set(STATS);return 'OK:'+n})()"""


def _targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
        return json.loads(r.read().decode())


def _try_frame(t):
    """Connexion CDP a un frame ; garde-le s'il donne acces a la base de la page Vivier."""
    try:
        ws = navigateur.WebSocket(t["webSocketDebuggerUrl"], timeout=20)
        o = navigateur.Onglet.__new__(navigateur.Onglet)
        o.ws, o.n = ws, 0
        if o.js(PROBE_JS, timeout=20) == "DB":
            return o
        ws.close()
    except Exception:
        pass
    return None


def _frame(ouvrir=True):
    """Trouve le frame de la page Vivier dans le Chrome Scout (l'ouvre si besoin)."""
    global _CONN
    if _CONN is not None:
        return _CONN
    for essai in range(2):
        ts = _targets()
        for t in ts:
            if t.get("type") == "iframe" and not any(
                    x in t.get("url", "") for x in ("instagram", "facebook", "hcaptcha", "google", "youtube")):
                o = _try_frame(t)
                if o:
                    _CONN = o
                    return o
        if essai == 0 and ouvrir and not any("claude.ai/artifact" in t.get("url", "") for t in ts):
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?{ARTIFACT_URL}", method="PUT")
            urllib.request.urlopen(req, timeout=5).read()
        time.sleep(15)  # le temps que la page Vivier se charge
    return None


CHOIX_JS = r"""(async()=>{const db=await window.claude.use('db');if(!db)return 'NODB';
const s=await db.collection('profils').limit(1000).get();
return 'CHOIX:'+JSON.stringify(s.docs.map(d=>{const x=d.data()||{};return [d.id,x.etat||'',!!x.favori]}))})()"""


def lire_choix():
    """Lit dans Vivier tous les profils deja presents : [(pseudo, etat, favori), ...] ou None."""
    o = _frame()
    if not o:
        return None
    try:
        r = o.js(CHOIX_JS, timeout=120) or ""
    except Exception:
        return None
    if not r.startswith("CHOIX:"):
        return None
    return [(h.lower(), e, bool(f)) for h, e, f in json.loads(r[6:])]


_ECHEC = 0.0  # dernier echec d'acces a Vivier : on ne reessaie pas avant 5 min (pas de ralentissement)


def push(docs, stats=None):
    """Ajoute les profils absents de Vivier (+ met a jour le panneau). Renvoie le nb ajoute, ou None."""
    global _CONN, _ECHEC
    if time.time() - _ECHEC < 300:
        return None
    o = _frame()
    if not o:
        _ECHEC = time.time()
        return None
    js = PUSH_JS.replace("__DOCS__", json.dumps(docs, ensure_ascii=False)) \
                .replace("__STATS__", json.dumps(stats, ensure_ascii=False) if stats else "null")
    try:
        r = o.js(js, timeout=120) or ""
    except Exception:
        _CONN = None  # onglet recharge ou ferme : on se reconnectera la prochaine fois
        return None
    return int(r.split(":")[1]) if r.startswith("OK:") else None


if __name__ == "__main__":
    o = _frame()
    print("Envoi direct dans Vivier : OK" if o else
          "Pas possible : connecte-toi a claude.ai dans la fenetre Chrome Scout (onglet Vivier), puis relance.")
