#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Site de candidature chatters (zéro dépendance : Python stdlib).

  python3 serveur.py

- Page candidat :  http://127.0.0.1:8790/          (le lien que tu envoies)
- Rapport (toi) :  http://127.0.0.1:8790/admin     (protégé par la clé affichée au lancement)

Les réponses sont dans  donnees/candidats.db  et les captures dans  donnees/fichiers/.
"""
import base64
import csv
import io
import json
import os
import random
import re
import secrets
import sqlite3
import time
import uuid
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ICI = Path(__file__).parent
PAGES = ICI / "page"
DONNEES = ICI / "donnees"
FICHIERS = DONNEES / "fichiers"
DB = DONNEES / "candidats.db"
PORT = int(os.environ.get("PORT", 8790))

# ---------------------------------------------------------------- réglages
NOM_AGENCE = os.environ.get("NOM_AGENCE", "Aurivon")
WPM_MIN = int(os.environ.get("WPM_MIN", 40))          # mots / minute minimum
PRECISION_MIN = int(os.environ.get("PRECISION_MIN", 90))
ESSAIS_FRAPPE = 3
WPM_SUSPECT = 150
MAX_FICHIERS = 10
MAX_OCTETS = 8 * 1024 * 1024

SHIFTS = [
    {"id": "matin", "nom": "Matin", "heures": "06h – 14h"},
    {"id": "aprem", "nom": "Après-midi", "heures": "14h – 22h"},
    {"id": "nuit", "nom": "Nuit", "heures": "22h – 06h"},
]

TEXTES_FRAPPE = [
    "Hey babe, I just got back from the gym and I am so tired but I could not stop "
    "thinking about you. Tell me what you have been up to today, I want to hear "
    "everything. I might have something special to show you later if you are good.",
    "Good morning handsome, did you sleep well? I had the craziest dream last night "
    "and you were in it. I am making coffee right now and planning a little photo "
    "shoot this afternoon. What kind of outfit should I wear for you?",
    "You always know how to make me smile, it is honestly my favorite part of the "
    "day. I am lying in bed with my phone and I have a few new pictures that nobody "
    "else has seen yet. Want me to send you a sneak peek right now?",
]


# ---------------------------------------------------------------- clé admin
def cle_admin():
    if os.environ.get("ADMIN_CLE"):
        return os.environ["ADMIN_CLE"]
    f = DONNEES / "cle_admin.txt"
    if not f.exists():
        DONNEES.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(9))
    return f.read_text().strip()


# ---------------------------------------------------------------- base
def db():
    DONNEES.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS candidats (
        id TEXT PRIMARY KEY, etape TEXT, statut TEXT, decision TEXT,
        data TEXT, ip TEXT, cree_le REAL, maj_le REAL)""")
    return con


def lire(cid):
    with db() as con:
        r = con.execute("SELECT * FROM candidats WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    c = dict(r)
    c["data"] = json.loads(c["data"] or "{}")
    return c


def ecrire(c):
    c["maj_le"] = time.time()
    with db() as con:
        con.execute("""INSERT OR REPLACE INTO candidats
            (id, etape, statut, decision, data, ip, cree_le, maj_le) VALUES (?,?,?,?,?,?,?,?)""",
                    (c["id"], c["etape"], c["statut"], c.get("decision") or "",
                     json.dumps(c["data"], ensure_ascii=False), c.get("ip"), c["cree_le"], c["maj_le"]))


def tous():
    with db() as con:
        rows = con.execute("SELECT * FROM candidats ORDER BY maj_le DESC").fetchall()
    out = []
    for r in rows:
        c = dict(r)
        c["data"] = json.loads(c["data"] or "{}")
        out.append(c)
    return out


# ---------------------------------------------------------------- test de frappe
def _norm(t):
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


def noter_frappe(ref, tape, secondes):
    ref, tape = _norm(ref), _norm(tape)
    minutes = max(float(secondes), 1.0) / 60
    brut = (len(tape) / 5) / minutes                      # 1 mot = 5 caractères (norme)
    precision = SequenceMatcher(None, ref, tape).ratio() * 100
    net = brut * precision / 100
    return {"wpm": round(net, 1), "wpm_brut": round(brut, 1), "precision": round(precision, 1),
            "secondes": round(float(secondes), 1),
            "reussi": net >= WPM_MIN and precision >= PRECISION_MIN,
            "suspect": brut >= WPM_SUSPECT}


# ---------------------------------------------------------------- étapes
ORDRE = ["identite", "frappe", "experience", "agence", "shifts", "resultats", "termine"]


def nettoyer(d, cles, n=600):
    return {k: str(d.get(k, ""))[:n].strip() for k in cles}


def enregistrer_etape(c, etape, d):
    """Valide et enregistre une étape. Renvoie (ok, message_erreur)."""
    if c["statut"] != "en_cours":
        return False, "Ta candidature est déjà terminée."
    if etape != c["etape"]:
        return False, "Étape inattendue, recharge la page."

    if etape == "identite":
        v = nettoyer(d, ["prenom", "nom", "age", "pays", "ville", "contact", "langues"])
        if not all(v[k] for k in ("prenom", "nom", "age", "pays", "ville", "contact")):
            return False, "Remplis tous les champs obligatoires."
        geo = d.get("geo") or {}
        v["geo"] = {k: geo.get(k) for k in ("lat", "lon", "precision", "statut")} if isinstance(geo, dict) else {}
        v["fuseau"] = str(d.get("fuseau", ""))[:60]
        c["data"]["identite"] = v

    elif etape == "frappe":
        f = c["data"].setdefault("frappe", {"essais": []})
        if len(f["essais"]) >= ESSAIS_FRAPPE:
            return False, "Tu as utilisé tous tes essais."
        idx = int(d.get("texte", 0)) % len(TEXTES_FRAPPE)
        res = noter_frappe(TEXTES_FRAPPE[idx], str(d.get("tape", ""))[:3000], d.get("secondes", 1))
        res["colle"] = bool(d.get("colle"))
        f["essais"].append(res)
        f["meilleur"] = max(f["essais"], key=lambda r: (r["reussi"], r["wpm"]))
        reste = ESSAIS_FRAPPE - len(f["essais"])
        if not res["reussi"] and reste > 0:
            ecrire(c)
            return True, {"resultat": res, "reste": reste, "avance": False}
        if not f["meilleur"]["reussi"]:
            c["statut"] = "stop_frappe"
            c["etape"] = "termine"
            ecrire(c)
            return True, {"resultat": res, "reste": 0, "avance": False, "stop": True}
        c["etape"] = "experience"
        ecrire(c)
        return True, {"resultat": res, "reste": reste, "avance": True}

    elif etape == "experience":
        exp = d.get("experience")
        if exp not in ("debutant", "experimente"):
            return False, "Choisis une réponse."
        c["data"]["experience"] = exp
        if exp == "debutant":
            c["statut"], c["etape"] = "stop_debutant", "termine"
            ecrire(c)
            return True, {"stop": True}

    elif etape == "agence":
        v = nettoyer(d, ["agence", "duree", "creatrices", "ca_shift", "ca_mois", "meilleur", "depart"])
        if not all(v[k] for k in ("agence", "duree", "creatrices", "ca_shift")):
            return False, "Remplis tous les champs obligatoires."
        c["data"]["agence"] = v

    elif etape == "shifts":
        ids = {s["id"] for s in SHIFTS}
        choix = [s for s in d.get("shifts", []) if s in ids]
        if not choix:
            return False, "Choisis au moins un shift."
        c["data"]["shifts"] = choix
        c["data"]["shifts_semaine"] = str(d.get("par_semaine", ""))[:10]
        c["data"]["dispo"] = str(d.get("dispo", ""))[:200]

    elif etape == "resultats":
        c["data"]["resultats_note"] = str(d.get("note", ""))[:1000]
        c["statut"] = "complet"

    c["etape"] = ORDRE[ORDRE.index(etape) + 1]
    ecrire(c)
    return True, {}


def public(c):
    """Ce que le candidat a le droit de revoir (pour reprendre où il en était)."""
    f = c["data"].get("frappe", {})
    return {"id": c["id"], "etape": c["etape"], "statut": c["statut"],
            "prenom": c["data"].get("identite", {}).get("prenom", ""),
            "essais": len(f.get("essais", [])), "fichiers": c["data"].get("fichiers", [])}


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ip(self):
        return (self.headers.get("CF-Connecting-IP") or
                (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or
                self.client_address[0])

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_OCTETS * 1.4:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def _admin_ok(self, q):
        return q.get("cle", [""])[0] == cle_admin()

    # ------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            return self._send(200, (PAGES / "candidat.html").read_text("utf-8"), "text/html; charset=utf-8")
        if p == "/admin":
            return self._send(200, (PAGES / "admin.html").read_text("utf-8"), "text/html; charset=utf-8")

        if p == "/api/config":
            return self._send(200, {"agence": NOM_AGENCE, "wpm_min": WPM_MIN, "precision_min": PRECISION_MIN,
                                    "essais": ESSAIS_FRAPPE, "shifts": SHIFTS, "max_fichiers": MAX_FICHIERS})
        if p == "/api/texte":
            i = random.randrange(len(TEXTES_FRAPPE))
            return self._send(200, {"idx": i, "texte": TEXTES_FRAPPE[i]})
        if p == "/api/moi":
            c = lire(q.get("id", [""])[0])
            return self._send(200, public(c)) if c else self._send(404, {"erreur": "inconnu"})

        # --- admin
        if p.startswith("/api/admin/") or p.startswith("/fichiers/"):
            if not self._admin_ok(q):
                return self._send(403, {"erreur": "clé invalide"})
        if p == "/api/admin/candidats":
            return self._send(200, {"candidats": tous(), "shifts": SHIFTS, "wpm_min": WPM_MIN})
        if p == "/api/admin/export.csv":
            return self._send(200, export_csv(), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="candidats.csv"'})
        if p.startswith("/fichiers/"):
            parts = p.split("/")
            if len(parts) == 4 and re.fullmatch(r"[0-9a-f-]{36}", parts[2]) and re.fullmatch(r"[\w.-]+", parts[3]):
                f = FICHIERS / parts[2] / parts[3]
                if f.exists():
                    ct = "image/png" if f.suffix == ".png" else "image/jpeg"
                    return self._send(200, f.read_bytes(), ct)
            return self._send(404, {"erreur": "introuvable"})

        self._send(404, {"erreur": "page introuvable"})

    # ------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        d = self._json()
        if d is None:
            return self._send(400, {"erreur": "requête invalide"})

        if p == "/api/start":
            c = {"id": str(uuid.uuid4()), "etape": "identite", "statut": "en_cours", "data": {},
                 "ip": self._ip(), "cree_le": time.time()}
            ecrire(c)
            return self._send(200, public(c))

        if p == "/api/etape":
            c = lire(str(d.get("id", "")))
            if not c:
                return self._send(404, {"erreur": "Candidature introuvable, recharge la page."})
            ok, info = enregistrer_etape(c, str(d.get("etape", "")), d.get("data") or {})
            if not ok:
                return self._send(400, {"erreur": info})
            return self._send(200, {**public(lire(c["id"])), **info})

        if p == "/api/fichier":
            c = lire(str(d.get("id", "")))
            if not c or c["etape"] != "resultats" or c["statut"] != "en_cours":
                return self._send(400, {"erreur": "Envoi impossible pour le moment."})
            fichiers = c["data"].setdefault("fichiers", [])
            if len(fichiers) >= MAX_FICHIERS:
                return self._send(400, {"erreur": f"{MAX_FICHIERS} captures maximum."})
            m = re.match(r"data:image/(png|jpeg);base64,(.+)", str(d.get("data", "")), re.S)
            if not m:
                return self._send(400, {"erreur": "Format d'image non reconnu."})
            brut = base64.b64decode(m.group(2))
            if len(brut) > MAX_OCTETS:
                return self._send(400, {"erreur": "Image trop lourde."})
            dossier = FICHIERS / c["id"]
            dossier.mkdir(parents=True, exist_ok=True)
            nom = f"{len(fichiers) + 1:02d}_{secrets.token_hex(3)}.{'png' if m.group(1) == 'png' else 'jpg'}"
            (dossier / nom).write_bytes(brut)
            fichiers.append(nom)
            ecrire(c)
            return self._send(200, public(c))

        if p == "/api/fichier/supprimer":
            c = lire(str(d.get("id", "")))
            nom = str(d.get("nom", ""))
            if c and c["etape"] == "resultats" and nom in c["data"].get("fichiers", []):
                c["data"]["fichiers"].remove(nom)
                (FICHIERS / c["id"] / nom).unlink(missing_ok=True)
                ecrire(c)
                return self._send(200, public(c))
            return self._send(400, {"erreur": "Impossible."})

        if p == "/api/admin/decision":
            if not self._admin_ok(q):
                return self._send(403, {"erreur": "clé invalide"})
            c = lire(str(d.get("id", "")))
            if not c:
                return self._send(404, {"erreur": "inconnu"})
            c["decision"] = str(d.get("decision", ""))[:20]
            c["data"]["note_staff"] = str(d.get("note", c["data"].get("note_staff", "")))[:2000]
            ecrire(c)
            return self._send(200, {"ok": True})

        self._send(404, {"erreur": "introuvable"})


def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["date", "statut", "decision", "prenom", "nom", "age", "pays", "ville", "contact", "langues",
                "gps", "fuseau", "ip", "wpm", "precision", "frappe_ok", "experience", "agence", "duree",
                "creatrices", "ca_shift", "ca_mois", "shifts", "shifts_semaine", "nb_captures"])
    for c in tous():
        d = c["data"]
        i, a = d.get("identite", {}), d.get("agence", {})
        f = d.get("frappe", {}).get("meilleur", {})
        g = i.get("geo") or {}
        w.writerow([time.strftime("%d/%m/%Y %H:%M", time.localtime(c["cree_le"])), c["statut"], c["decision"],
                    i.get("prenom"), i.get("nom"), i.get("age"), i.get("pays"), i.get("ville"), i.get("contact"),
                    i.get("langues"), f"{g.get('lat')},{g.get('lon')}" if g.get("lat") else "", i.get("fuseau"),
                    c["ip"], f.get("wpm"), f.get("precision"), f.get("reussi"), d.get("experience"),
                    a.get("agence"), a.get("duree"), a.get("creatrices"), a.get("ca_shift"), a.get("ca_mois"),
                    " | ".join(d.get("shifts", [])), d.get("shifts_semaine"), len(d.get("fichiers", []))])
    return "﻿" + buf.getvalue()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"\n  ✅ Site de candidature en route")
    print(f"  Page candidat : http://127.0.0.1:{PORT}/")
    print(f"  Ton rapport   : http://127.0.0.1:{PORT}/admin#{cle_admin()}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
