#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pilote — publication automatique Drive -> Publer + super analyse (cible USA).

Lance :  python3 pilote.py        (ouvre http://127.0.0.1:8790)

- Lit les videos d'un dossier Google Drive (partage "toute personne disposant du lien").
- Programme N publications / jour / compte, une semaine a l'avance, sur Publer
  (c'est Publer qui publie : ton ordi peut etre eteint une fois la semaine envoyee).
- Choisit les heures automatiquement a partir de TES stats (heure de New York)
  + les "best times" Publer + un profil d'audience US par defaut.
- Analyse : videos gagnantes/perdantes, heatmap jour x heure (ET), hashtags,
  longueur de legende, frequence ideale, recommandations.
- Autopilote : remplit la semaine en continu, recycle les gagnantes si le stock est vide.

Zero dependance (Python 3.9+ stdlib).
"""

import json
import os
import random
import statistics
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, parse_qs, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
CONFIG_F = DATA / "config.json"
STATE_F = DATA / "state.json"
INSIGHTS_F = DATA / "insights.json"
PORT = int(os.environ.get("PILOTE_PORT", "8790"))
HOST = os.environ.get("PILOTE_HOST", "127.0.0.1")
PUBLER = "https://app.publer.com/api/v1"
DRIVE = "https://www.googleapis.com/drive/v3"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DEFAULT_CONFIG = {
    "demo": True,                     # donnees fictives tant que les cles ne sont pas mises
    "publer_api_key": "",
    "publer_workspace_id": "",
    "publer_tz": "Europe/Paris",      # fuseau du workspace Publer (pour convertir ses best times)
    "drive_api_key": "",
    "drive_folder_id": "",
    "account_folders": {},            # {account_id: folder_id} (optionnel, sinon dossier global)
    "accounts": [],                   # comptes Publer selectionnes
    "posts_per_day": 3,
    "horizon_days": 7,
    "target_tz": "America/New_York",
    "window_start": 7,                # plage horaire autorisee (heure cible)
    "window_end": 23,
    "min_gap_hours": 2,
    "jitter_min": 12,                 # +- minutes aleatoires (rythme humain)
    "slot_mode": "auto",              # auto | manual
    "manual_hours": [9, 13, 19],
    "distribution": "split",          # split = videos differentes par compte, mirror = meme video partout
    "order": "oldest",                # oldest | newest | random
    "captions": "",                   # banque de legendes (1 par ligne), utilisee si pas de .txt / description
    "hashtags": "",
    "ig_reel": True,
    "yt_short": True,
    "recycle_winners": True,
    "recycle_after_days": 21,
    "autopilot": False,
    "analysis_days": 60,
}

LOCK = threading.RLock()
JOB = {"running": False, "progress": 0, "total": 0, "label": "", "errors": []}
LOGS = []


# ----------------------------------------------------------------------------- utils

def log(msg):
    line = time.strftime("%d/%m %H:%M:%S ") + str(msg)
    print(line, flush=True)
    with LOCK:
        LOGS.append(line)
        del LOGS[:-300]


def rjson(f, default):
    try:
        return json.loads(Path(f).read_text("utf-8"))
    except Exception:
        return default


def wjson(f, data):
    tmp = Path(str(f) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(f)


def cfg():
    c = dict(DEFAULT_CONFIG)
    c.update(rjson(CONFIG_F, {}))
    return c


def save_cfg(c):
    wjson(CONFIG_F, c)


def state():
    s = rjson(STATE_F, {})
    s.setdefault("posts", [])        # tout ce que Pilote a programme
    s.setdefault("last_autopilot", None)
    return s


def save_state(s):
    wjson(STATE_F, s)


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# ----------------------------------------------------------------------------- HTTP

def http(method, url, headers=None, body=None, timeout=60, raw=False):
    data = None
    headers = dict(headers or {})
    if body is not None and not isinstance(body, (bytes, bytearray)):
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body
    for attempt in range(5):
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=timeout) as r:
                content = r.read()
                if raw:
                    return content
                return json.loads(content or b"null")
        except HTTPError as e:
            if e.code == 429 and attempt < 4:
                wait = int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
                log(f"Limite d'API atteinte, pause {wait}s...")
                time.sleep(wait)
                continue
            detail = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {e.code} sur {urlparse(url).path} : {detail}")
        except (TimeoutError, OSError) as e:
            if attempt < 2:
                time.sleep(3)
                continue
            raise RuntimeError(f"Reseau : {e}")


def publer(method, path, body=None, params=None, workspace=True):
    c = cfg()
    h = {"Authorization": f"Bearer-API {c['publer_api_key']}", "Accept": "application/json"}
    if workspace:
        h["Publer-Workspace-Id"] = c["publer_workspace_id"]
    url = PUBLER + path + ("?" + urlencode(params) if params else "")
    return http(method, url, h, body)


def publer_job(job_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = publer("GET", f"/job_status/{job_id}") or {}
        d = r.get("data", r)
        st = d.get("status") or (d.get("result") or {}).get("status")
        if st in ("complete", "completed"):
            return d
        if st == "failed":
            raise RuntimeError(f"Job Publer echoue : {json.dumps(d)[:300]}")
        time.sleep(3)
    raise RuntimeError("Job Publer trop long (timeout)")


# ----------------------------------------------------------------------------- DEMO

def _demo_accounts():
    return [
        {"id": "demo_ig1", "name": "@lea.daily", "provider": "instagram"},
        {"id": "demo_tt1", "name": "@lea.tok", "provider": "tiktok"},
        {"id": "demo_ig2", "name": "@mia.vibes", "provider": "instagram"},
    ]


def _demo_files():
    rnd = random.Random(7)
    base = utcnow() - timedelta(days=40)
    out = []
    for i in range(48):
        out.append({"id": f"demofile{i}", "name": f"video_{i+1:03d}_{rnd.choice(['grwm','dance','pov','trend','outfit','vlog'])}.mp4",
                    "mimeType": "video/mp4", "size": str(rnd.randint(4, 60) * 1_000_000),
                    "createdTime": iso(base + timedelta(hours=i * 18)), "description": "",
                    "thumbnailLink": ""})
    return out


def _demo_insights(days):
    rnd = random.Random(42)
    et = ZoneInfo("America/New_York")
    hour_boost = {6: .5, 7: .8, 8: .9, 9: .8, 11: .9, 12: 1.2, 13: 1.1, 17: 1.1, 18: 1.3, 19: 1.6,
                  20: 1.8, 21: 1.7, 22: 1.3, 23: .9, 0: .6, 2: .3, 3: .2}
    day_boost = [0.9, 1.0, 1.05, 1.15, 1.0, 1.1, 1.25]
    tags = ["#fyp", "#grwm", "#ootd", "#viral", "#explore", "#reels", "#pov", "#usa", "#trend", "#nyc"]
    posts = []
    for acc in _demo_accounts():
        size = {"demo_ig1": 9000, "demo_tt1": 22000, "demo_ig2": 5000}[acc["id"]]
        for d in range(days):
            for _ in range(rnd.choice([2, 3, 3, 4, 5])):
                h = rnd.choice([7, 8, 9, 11, 12, 13, 15, 17, 18, 19, 20, 21, 22, 23, 0, 2])
                local = (datetime.now(et) - timedelta(days=d)).replace(hour=h, minute=rnd.randint(0, 59))
                ntags = rnd.randint(0, 9)
                cap = rnd.choice(["pov: ", "wait for it ", "", "GRWM ", "rate this 1-10 ", "", "she said what?? "]) \
                    + "x" * rnd.choice([10, 30, 80, 160, 260]) + " " + " ".join(rnd.sample(tags, ntags))
                f = hour_boost.get(h, 0.7) * day_boost[local.weekday()] * rnd.lognormvariate(0, 0.7)
                f *= 1.25 if "#grwm" in cap else 1
                f *= 0.8 if ntags > 6 else 1
                views = int(size * f)
                likes = int(views * rnd.uniform(0.03, 0.11))
                posts.append({
                    "id": rnd.randint(10**9, 10**10), "account_id": acc["id"], "post_type": "video",
                    "text": cap, "scheduled_at": local.astimezone(timezone.utc).isoformat(),
                    "analytics": {"video_views": views, "reach": int(views * .8), "likes": likes,
                                  "comments": likes // 20, "shares": likes // 12, "saves": likes // 9,
                                  "engagement": likes + likes // 20 + likes // 12 + likes // 9,
                                  "engagement_rate": round(100 * likes * 1.25 / max(1, views * .8), 2)}})
    return posts


def _demo_best_times():
    rnd = random.Random(3)
    return {d: [round(max(0, (1.4 if 18 <= h <= 22 else 1 if 11 <= h <= 14 else .5) + rnd.uniform(-.3, .3)), 2)
                for h in range(24)] for d in DAYS}


# ----------------------------------------------------------------------------- connecteurs

def list_accounts():
    if cfg()["demo"]:
        return _demo_accounts()
    r = publer("GET", "/accounts")
    if isinstance(r, dict):
        r = r.get("accounts") or r.get("data") or []
    return [{"id": str(a.get("id")), "name": a.get("name") or a.get("username") or str(a.get("id")),
             "provider": a.get("provider") or a.get("type") or "", "picture": a.get("picture") or a.get("avatar") or ""}
            for a in r]


def list_workspaces():
    r = publer("GET", "/workspaces", workspace=False)
    if isinstance(r, dict):
        r = r.get("workspaces") or r.get("data") or []
    return [{"id": str(w.get("id")), "name": w.get("name")} for w in r]


def drive_files(folder_id=None):
    c = cfg()
    if c["demo"]:
        return _demo_files()
    folder_id = folder_id or c["drive_folder_id"]
    if not folder_id:
        return []
    files, token = [], None
    while True:
        p = {"q": f"'{folder_id}' in parents and trashed=false",
             "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,description,thumbnailLink,videoMediaMetadata)",
             "pageSize": 1000, "key": c["drive_api_key"], "supportsAllDrives": "true",
             "includeItemsFromAllDrives": "true", "orderBy": "createdTime"}
        if token:
            p["pageToken"] = token
        r = http("GET", DRIVE + "/files?" + urlencode(p))
        files += r.get("files", [])
        token = r.get("nextPageToken")
        if not token:
            break
    return files


def drive_download_url(file_id):
    return f"{DRIVE}/files/{file_id}?alt=media&key={cfg()['drive_api_key']}&supportsAllDrives=true"


def drive_videos(folder_id=None):
    """Videos du dossier + legende liee (fichier .txt du meme nom, sinon description Drive)."""
    files = drive_files(folder_id)
    txt = {f["name"].rsplit(".", 1)[0]: f for f in files if f.get("name", "").lower().endswith(".txt")}
    vids = [f for f in files if (f.get("mimeType", "").startswith("video/")
                                 or f.get("name", "").lower().endswith((".mp4", ".mov", ".m4v", ".webm")))]
    for v in vids:
        v["caption_file"] = txt.get(v["name"].rsplit(".", 1)[0], {}).get("id")
    return vids


def drive_caption(v):
    if v.get("caption_file") and not cfg()["demo"]:
        try:
            return http("GET", drive_download_url(v["caption_file"]), raw=True).decode("utf-8", "replace").strip()
        except Exception as e:
            log(f"Legende .txt illisible pour {v['name']} : {e}")
    return (v.get("description") or "").strip()


def import_media(v):
    """Importe une video Drive dans Publer -> objet media utilisable dans un post."""
    if cfg()["demo"]:
        time.sleep(0.05)
        return {"id": "m_" + v["id"], "path": "", "type": "video"}
    r = publer("POST", "/media/from-url", {"type": "single", "direct_upload": False, "in_library": False,
                                            "media": [{"url": drive_download_url(v["id"]), "name": v["name"]}]})
    d = publer_job(r["job_id"])
    res = d.get("result") or d
    payload = res.get("payload", res)
    cand = payload if isinstance(payload, list) else (payload.get("media") or payload.get("medias") or [payload])
    for m in (cand if isinstance(cand, list) else [cand]):
        if isinstance(m, dict) and m.get("id"):
            out = {"id": m["id"], "path": m.get("path") or m.get("url") or "", "type": "video"}
            if m.get("thumbnails"):
                out["thumbnails"] = m["thumbnails"]
                out["default_thumbnail"] = 0
            return out
    fails = payload.get("failures") if isinstance(payload, dict) else None
    raise RuntimeError(f"Import Drive->Publer impossible pour {v['name']} : {json.dumps(fails or payload)[:300]}"
                       " (le dossier Drive est-il partage 'toute personne disposant du lien' ?)")


def network_block(provider, caption, media, c, title):
    b = {"type": "video", "text": caption, "media": [media]}
    if provider == "instagram" and c["ig_reel"]:
        b["details"] = {"type": "reel"}
    elif provider == "youtube":
        b["title"] = title[:95]
        b["details"] = {"type": "short" if c["yt_short"] else "video", "privacy": "public"}
    elif provider == "facebook":
        b["details"] = {"type": "reel"}
    return b


def schedule_publer(items_same_media, media, caption, title):
    """items_same_media : [{account_id, provider, at}] partageant la meme video."""
    c = cfg()
    if c["demo"]:
        return
    networks = {}
    for it in items_same_media:
        networks.setdefault(it["provider"] or "default", network_block(it["provider"], caption, media, c, title))
    body = {"bulk": {"state": "scheduled", "posts": [{
        "networks": networks,
        "accounts": [{"id": it["account_id"], "scheduled_at": it["at"]} for it in items_same_media]}]}}
    r = publer("POST", "/posts/schedule", body)
    if r and r.get("job_id"):
        d = publer_job(r["job_id"], timeout=180)
        fails = ((d.get("result") or {}).get("payload") or {}).get("failures") if isinstance(
            (d.get("result") or {}).get("payload"), dict) else None
        if fails:
            raise RuntimeError(f"Publer a refuse : {json.dumps(fails)[:400]}")


# ----------------------------------------------------------------------------- ANALYSE

US_PRIOR = [0.35, 0.25, 0.15, 0.1, 0.1, 0.15, 0.35, 0.55, 0.65, 0.6, 0.55, 0.6,
            0.75, 0.7, 0.6, 0.6, 0.65, 0.75, 0.85, 0.95, 1.0, 0.95, 0.8, 0.55]


def fetch_insights(days):
    c = cfg()
    to = utcnow().date()
    frm = to - timedelta(days=days)
    accounts = c["accounts"] or [a["id"] for a in list_accounts()]
    if c["demo"]:
        posts = [p for p in _demo_insights(days) if p["account_id"] in accounts]
        best = {a: _demo_best_times() for a in accounts}
    else:
        posts, best = [], {}
        for acc in accounts:
            page = 0
            while page < 60:
                r = publer("GET", f"/analytics/{acc}/post_insights",
                           params={"from": frm.isoformat(), "to": to.isoformat(), "page": page,
                                   "sort_by": "scheduled_at", "sort_type": "DESC"})
                batch = (r or {}).get("posts", [])
                for p in batch:
                    p.setdefault("account_id", acc)
                posts += batch
                if len(batch) < 10 or (page + 1) * 10 >= (r or {}).get("total", 0):
                    break
                page += 1
                time.sleep(0.4)
            try:
                best[acc] = publer("GET", f"/analytics/{acc}/best_times",
                                   params={"from": frm.isoformat(), "to": to.isoformat()}) or {}
            except Exception as e:
                log(f"Best times indisponibles pour {acc} : {e}")
    data = {"fetched_at": iso(utcnow()), "days": days, "posts": posts, "best_times": best}
    wjson(INSIGHTS_F, data)
    log(f"Analyse : {len(posts)} publications recuperees sur {days} jours")
    return data


def _metric(a):
    a = a or {}
    return float(a.get("video_views") or a.get("reach") or a.get("engagement") or 0)


def _hashtags(text):
    return [w.lower().strip(".,!?;:") for w in (text or "").split() if w.startswith("#") and len(w) > 1]


def analyse(data=None):
    c = cfg()
    data = data or rjson(INSIGHTS_F, None)
    if not data:
        return {"empty": True}
    tz = ZoneInfo(c["target_tz"])
    accounts = {a["id"]: a for a in list_accounts()}
    st = state()
    sent = [p for p in st["posts"] if p.get("status") == "scheduled"]

    posts = []
    for p in data["posts"]:
        dt = parse_dt(p.get("scheduled_at"))
        m = _metric(p.get("analytics"))
        if not dt or dt > utcnow():
            continue
        posts.append({**p, "_dt": dt, "_m": m})
    if not posts:
        return {"empty": True, "fetched_at": data.get("fetched_at")}

    # indice de performance = vues / mediane du compte (comparable entre petits et gros comptes)
    by_acc = {}
    for p in posts:
        by_acc.setdefault(p["account_id"], []).append(p)
    med = {a: (statistics.median([x["_m"] for x in ps]) or 1) for a, ps in by_acc.items()}
    for p in posts:
        p["_i"] = min(10.0, p["_m"] / med[p["account_id"]]) if med[p["account_id"]] else 1.0
        loc = p["_dt"].astimezone(tz)
        p["_wd"], p["_h"] = loc.weekday(), loc.hour
        p["_loc"] = loc
        # retrouve la video Drive d'origine (meme compte, meme heure a 5 min pres)
        for s in sent:
            if s["account_id"] == str(p["account_id"]):
                sd = parse_dt(s["at"])
                if sd and abs((sd - p["_dt"]).total_seconds()) <= 300:
                    p["_file"] = s["file_name"]
                    p["_file_id"] = s["file_id"]
                    break

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    # KPI + tendance (moitie recente vs moitie ancienne)
    cut = utcnow() - timedelta(days=data["days"] / 2)
    recent = [p for p in posts if p["_dt"] >= cut]
    old = [p for p in posts if p["_dt"] < cut]

    def kpi(ps):
        views = sum(p["_m"] for p in ps)
        ers = [float((p.get("analytics") or {}).get("engagement_rate") or 0) for p in ps]
        return {"posts": len(ps), "views": int(views), "avg": int(views / len(ps)) if ps else 0,
                "er": round(avg(ers), 2)}

    k_all, k_new, k_old = kpi(posts), kpi(recent), kpi(old)

    def delta(a, b):
        return round(100 * (a - b) / b, 1) if b else None

    kpis = {**k_all, "avg_delta": delta(k_new["avg"], k_old["avg"]), "er_delta": delta(k_new["er"], k_old["er"]),
            "posts_per_day": round(len(posts) / max(1, data["days"]), 1)}

    # heatmap jour x heure (fuseau cible)
    cells = [[[] for _ in range(24)] for _ in range(7)]
    for p in posts:
        cells[p["_wd"]][p["_h"]].append(p["_i"])
    heat = [[{"v": round(avg(x), 2) if x else None, "n": len(x)} for x in row] for row in cells]

    # best times Publer -> fuseau cible (moyenne normalisee de tous les comptes)
    pz = ZoneInfo(c["publer_tz"])
    pub = [[0.0] * 24 for _ in range(7)]
    nb = 0
    for acc, bt in (data.get("best_times") or {}).items():
        if not isinstance(bt, dict) or not bt:
            continue
        mx = max([max(v) for v in bt.values() if v] or [1]) or 1
        nb += 1
        ref = datetime(2026, 1, 5, tzinfo=pz)  # un lundi
        for di, dname in enumerate(DAYS):
            for h, sc in enumerate(bt.get(dname) or []):
                t = (ref + timedelta(days=di, hours=h)).astimezone(tz)
                pub[t.weekday()][t.hour] += sc / mx
    if nb:
        pub = [[v / nb for v in row] for row in pub]

    # score par heure (lissage bayesien : peu de donnees -> profil US par defaut)
    K = 6
    by_hour = [[] for _ in range(24)]
    for p in posts:
        by_hour[p["_h"]].append(p["_i"])
    gmean = avg([p["_i"] for p in posts]) or 1
    hour_score = []
    for h in range(24):
        prior = US_PRIOR[h] / (sum(US_PRIOR) / 24) * gmean
        n = len(by_hour[h])
        own = (prior * K + sum(by_hour[h])) / (K + n)
        pb = avg([pub[d][h] for d in range(7)]) if nb else None
        s = own if pb is None else own * (0.8 + 0.4 * pb)
        hour_score.append({"h": h, "score": round(s, 3), "n": n, "raw": round(avg(by_hour[h]), 2) if n else None})
    ranked = sorted(hour_score, key=lambda x: -x["score"])
    best_hours = [{**x, "uplift": round(100 * (x["score"] / gmean - 1))} for x in ranked[:6]]

    day_stats = []
    for d in range(7):
        xs = [p["_i"] for p in posts if p["_wd"] == d]
        day_stats.append({"d": d, "v": round(avg(xs), 2), "n": len(xs)})

    # legendes / hashtags / formats
    def bucket(ps, keyf, labels):
        out = []
        for lab, f in labels:
            xs = [p["_i"] for p in ps if f(keyf(p))]
            out.append({"label": lab, "v": round(avg(xs), 2), "n": len(xs)})
        return out

    cap_len = bucket(posts, lambda p: len(p.get("text") or ""),
                     [("< 40 car.", lambda n: n < 40), ("40-120", lambda n: 40 <= n < 120),
                      ("120-250", lambda n: 120 <= n < 250), ("250+", lambda n: n >= 250)])
    tag_cnt = bucket(posts, lambda p: len(_hashtags(p.get("text"))),
                     [("0", lambda n: n == 0), ("1-3", lambda n: 1 <= n <= 3),
                      ("4-6", lambda n: 4 <= n <= 6), ("7+", lambda n: n >= 7)])
    tags = {}
    for p in posts:
        for t in set(_hashtags(p.get("text"))):
            tags.setdefault(t, []).append(p["_i"])
    top_tags = sorted([{"tag": t, "v": round(avg(x), 2), "n": len(x)} for t, x in tags.items() if len(x) >= 3],
                      key=lambda x: -x["v"])
    types = {}
    for p in posts:
        types.setdefault(p.get("post_type") or "?", []).append(p["_i"])
    type_stats = sorted([{"label": t, "v": round(avg(x), 2), "n": len(x)} for t, x in types.items()],
                        key=lambda x: -x["v"])

    # frequence : nb de posts dans la journee vs perf moyenne
    daymap = {}
    for p in posts:
        daymap.setdefault((p["account_id"], p["_loc"].date()), []).append(p["_i"])
    freq = {}
    for xs in daymap.values():
        k = min(len(xs), 6)
        freq.setdefault(k, []).append(avg(xs))
    freq_stats = [{"label": f"{k}{'+' if k == 6 else ''}/jour", "v": round(avg(v), 2), "n": len(v)}
                  for k, v in sorted(freq.items())]

    # par compte
    acc_stats = []
    for a, ps in by_acc.items():
        r_ = [p for p in ps if p["_dt"] >= cut]
        o_ = [p for p in ps if p["_dt"] < cut]
        ka, kr, ko = kpi(ps), kpi(r_), kpi(o_)
        acc_stats.append({"id": a, "name": accounts.get(str(a), {}).get("name", str(a)),
                          "provider": accounts.get(str(a), {}).get("provider", ""),
                          **ka, "median": int(med[a]), "trend": delta(kr["avg"], ko["avg"])})
    acc_stats.sort(key=lambda x: -x["views"])

    def card(p):
        a = p.get("analytics") or {}
        return {"text": (p.get("text") or "")[:160], "account": accounts.get(str(p["account_id"]), {}).get("name", ""),
                "views": int(p["_m"]), "likes": a.get("likes") or 0, "comments": a.get("comments") or 0,
                "shares": a.get("shares") or 0, "saves": a.get("saves") or 0,
                "er": a.get("engagement_rate") or 0, "index": round(p["_i"], 2),
                "when": p["_loc"].strftime("%a %d/%m %Hh%M"), "file": p.get("_file"), "file_id": p.get("_file_id"),
                "url": p.get("url") or p.get("link") or p.get("post_link") or ""}

    srt = sorted(posts, key=lambda p: -p["_i"])
    top = [card(p) for p in srt[:15]]
    flop = [card(p) for p in srt[::-1][:10] if p["_m"] > 0 or True][:10]

    # Video Drive gagnantes (agrege si postee plusieurs fois)
    winners = {}
    for p in posts:
        if p.get("_file_id"):
            winners.setdefault(p["_file_id"], {"file_id": p["_file_id"], "file": p["_file"], "idx": []})["idx"].append(p["_i"])
    win_files = sorted([{**w, "v": round(avg(w["idx"]), 2), "n": len(w["idx"])} for w in winners.values()],
                       key=lambda x: -x["v"])
    for w in win_files:
        w.pop("idx")

    # recommandations en clair
    rec = []
    hb = ", ".join(f"{x['h']}h" for x in best_hours[:3])
    rec.append(f"Tes meilleurs creneaux (heure de New York) : {hb} — jusqu'a +{max(0, best_hours[0]['uplift'])}% vs ta moyenne.")
    worst = sorted([x for x in hour_score if x["n"] >= 3], key=lambda x: x["score"])[:2]
    if worst:
        rec.append("Evite " + " et ".join(f"{x['h']}h" for x in worst) + " ET : tes videos y font nettement moins.")
    bd = sorted([d for d in day_stats if d["n"] >= 3], key=lambda d: -d["v"])
    if bd:
        jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        rec.append(f"Meilleur jour : {jours[bd[0]['d']]} ({bd[0]['v']}x ta mediane). "
                   f"Plus faible : {jours[bd[-1]['d']]} ({bd[-1]['v']}x).")
    okf = [f for f in freq_stats if f["n"] >= 4]
    if okf:
        bf = max(okf, key=lambda f: f["v"])
        rec.append(f"Frequence la plus efficace observee : {bf['label']} par compte.")
    bl = max([b for b in cap_len if b["n"] >= 4] or [None], key=lambda b: b["v"] if b else 0)
    if bl:
        rec.append(f"Legendes {bl['label']} = les plus performantes ({bl['v']}x).")
    bt = max([b for b in tag_cnt if b["n"] >= 4] or [None], key=lambda b: b["v"] if b else 0)
    if bt:
        rec.append(f"Nombre de hashtags ideal : {bt['label']}.")
    if top_tags[:3]:
        rec.append("Hashtags qui tirent vers le haut : " + " ".join(t["tag"] for t in top_tags[:4]) + ".")
    if top:
        rec.append(f"Ta meilleure video a fait {top[0]['index']}x ta mediane — reproduis son accroche : « {top[0]['text'][:70]} »")
    if kpis["avg_delta"] is not None:
        rec.append(("En progression" if kpis["avg_delta"] >= 0 else "En baisse") +
                   f" : {kpis['avg_delta']:+}% de vues moyennes par post sur la periode recente.")

    return {"empty": False, "fetched_at": data.get("fetched_at"), "days": data["days"], "tz": c["target_tz"],
            "kpis": kpis, "heat": heat, "publer_heat": [[round(v, 2) for v in row] for row in pub] if nb else None,
            "hour_score": hour_score, "best_hours": best_hours, "days_stats": day_stats,
            "cap_len": cap_len, "tag_cnt": tag_cnt, "top_tags": top_tags[:12], "types": type_stats,
            "freq": freq_stats, "accounts": acc_stats, "top": top, "flop": flop, "win_files": win_files[:15],
            "recommendations": rec}


# ----------------------------------------------------------------------------- PLANIFICATION

def slot_hours(n, c, an=None, weekday=None):
    """Choisit n heures (fuseau cible) en respectant la plage et l'ecart minimum."""
    lo, hi, gap = int(c["window_start"]), int(c["window_end"]), float(c["min_gap_hours"])
    allowed = [h for h in range(24) if (lo <= h <= hi if lo <= hi else (h >= lo or h <= hi))]
    if c["slot_mode"] == "manual":
        hrs = sorted(int(h) for h in c["manual_hours"])
        if len(hrs) >= n:
            return hrs[:n]
        allowed = [h for h in allowed if h not in hrs]
        pref = hrs
    else:
        pref = []
    if an and not an.get("empty"):
        score = {x["h"]: x["score"] for x in an["hour_score"]}
        if weekday is not None:  # petit bonus si la heatmap du jour est bonne
            for h in range(24):
                cell = an["heat"][weekday][h]
                if cell["n"] >= 3:
                    score[h] = 0.7 * score[h] + 0.3 * cell["v"]
    else:
        score = {h: US_PRIOR[h] for h in range(24)}
    chosen = list(pref)
    for h in sorted(allowed, key=lambda h: -score[h]):
        if len(chosen) >= n:
            break
        if all(min(abs(h - x), 24 - abs(h - x)) >= gap for x in chosen):
            chosen.append(h)
    # si l'ecart est trop strict pour n posts, on complete quand meme
    for h in sorted(allowed, key=lambda h: -score[h]):
        if len(chosen) >= n:
            break
        if h not in chosen:
            chosen.append(h)
    return sorted(chosen)


def build_plan(c=None, from_autopilot=False):
    c = c or cfg()
    tz = ZoneInfo(c["target_tz"])
    accounts = {a["id"]: a for a in list_accounts()}
    sel = [a for a in (c["accounts"] or list(accounts)) if a in accounts]
    if not sel:
        raise RuntimeError("Aucun compte Publer selectionne (onglet Reglages).")
    an = analyse() if INSIGHTS_F.exists() else None
    st = state()
    now = utcnow()
    n = int(c["posts_per_day"])
    rnd = random.Random()

    scheduled = [p for p in st["posts"] if p.get("status") in ("scheduled", "pending")]
    used_by_acc, used_any = {}, set()
    for p in st["posts"]:
        if p.get("status") in ("scheduled", "pending"):
            used_by_acc.setdefault(p["account_id"], set()).add(p["file_id"])
            used_any.add(p["file_id"])

    pools = {}

    def pool_for(acc):
        folder = (c.get("account_folders") or {}).get(acc) or c["drive_folder_id"]
        if folder not in pools:
            vids = drive_videos(folder)
            if c["order"] == "newest":
                vids.sort(key=lambda v: v.get("createdTime", ""), reverse=True)
            elif c["order"] == "random":
                rnd.shuffle(vids)
            else:
                vids.sort(key=lambda v: v.get("createdTime", ""))
            pools[folder] = vids
        return pools[folder]

    # gagnantes recyclables
    winners = []
    if c["recycle_winners"] and an and not an.get("empty"):
        winners = [w["file_id"] for w in an["win_files"] if w["v"] >= 1.3]

    def last_used(acc, fid):
        ds = [parse_dt(p["at"]) for p in st["posts"] if p["account_id"] == acc and p["file_id"] == fid]
        ds = [d for d in ds if d]
        return max(ds) if ds else None

    plan, taken = [], set()

    def pick(acc, day_dt):
        vids = pool_for(acc)
        for v in vids:
            key = v["id"]
            if c["distribution"] == "split":
                if key in used_any or key in taken:
                    continue
            else:
                if key in used_by_acc.get(acc, set()) or (key, acc) in taken:
                    continue
            return v, False
        for fid in winners:   # stock vide -> on recycle une gagnante
            lu = last_used(acc, fid)
            if lu and (day_dt - lu).days < int(c["recycle_after_days"]):
                continue
            if (fid, acc, "r") in taken:
                continue
            v = next((x for x in vids if x["id"] == fid), None)
            if v:
                return v, True
        return None, False

    bank = [l.strip() for l in (c["captions"] or "").splitlines() if l.strip()]
    missing = 0
    today = now.astimezone(tz).date()
    for d in range(int(c["horizon_days"])):
        day = today + timedelta(days=d)
        hours = slot_hours(n, c, an, day.weekday())
        for acc in sel:
            existing = [parse_dt(p["at"]) for p in scheduled if p["account_id"] == acc]
            existing = [e.astimezone(tz) for e in existing if e and e.astimezone(tz).date() == day]
            need = n - len(existing)
            if need <= 0:
                continue
            free = [h for h in hours if all(abs(e.hour - h) >= 1 for e in existing)]
            for h in free[:need]:
                minute = max(0, min(59, 30 + rnd.randint(-int(c["jitter_min"]), int(c["jitter_min"]))))
                local = datetime(day.year, day.month, day.day, h, 0, tzinfo=tz) + timedelta(minutes=minute - 30)
                if local.astimezone(timezone.utc) < now + timedelta(minutes=20):
                    continue
                v, recycled = pick(acc, local.astimezone(timezone.utc))
                if not v:
                    missing += 1
                    continue
                if c["distribution"] == "split":
                    taken.add(v["id"])
                else:
                    taken.add((v["id"], acc))
                if recycled:
                    taken.add((v["id"], acc, "r"))
                plan.append({"uid": uuid.uuid4().hex[:10], "account_id": acc,
                             "account": accounts[acc]["name"], "provider": accounts[acc]["provider"],
                             "file_id": v["id"], "file_name": v["name"], "caption_file": v.get("caption_file"),
                             "description": v.get("description", ""), "recycled": recycled,
                             "at": iso(local), "local": local.strftime("%a %d/%m %H:%M"),
                             "caption": None, "bank_pick": rnd.choice(bank) if bank else ""})

    # mode miroir : meme video au meme moment sur tous les comptes -> on aligne
    if c["distribution"] == "mirror":
        groups = {}
        for it in plan:
            groups.setdefault(it["at"][:13], []).append(it)
        for g in groups.values():
            first = g[0]
            for it in g[1:]:
                if it["file_id"] not in used_by_acc.get(it["account_id"], set()):
                    it.update(file_id=first["file_id"], file_name=first["file_name"],
                              caption_file=first["caption_file"], description=first["description"],
                              bank_pick=first["bank_pick"])
    plan.sort(key=lambda x: x["at"])
    return {"items": plan, "missing": missing, "hours": slot_hours(n, c, an)}


def caption_for(it, c):
    if it.get("caption"):
        return it["caption"]
    cap = drive_caption({"caption_file": it.get("caption_file"), "description": it.get("description"),
                         "name": it["file_name"]}) or it.get("bank_pick") or ""
    tags = (c.get("hashtags") or "").strip()
    return (cap + ("\n\n" + tags if tags else "")).strip()


def commit_plan(items, origin="manuel"):
    c = cfg()
    with LOCK:
        if JOB["running"]:
            raise RuntimeError("Une programmation est deja en cours.")
        JOB.update(running=True, progress=0, total=len(items), label=f"Programmation ({origin})", errors=[])

    def run():
        try:
            groups = {}
            for it in items:   # meme video + meme heure -> un seul post multi-comptes
                groups.setdefault((it["file_id"], it["at"]), []).append(it)
            for (fid, at), g in groups.items():
                try:
                    cap = caption_for(g[0], c)
                    media = import_media({"id": fid, "name": g[0]["file_name"]})
                    schedule_publer([{"account_id": x["account_id"], "provider": x["provider"], "at": at} for x in g],
                                    media, cap, g[0]["file_name"].rsplit(".", 1)[0])
                    with LOCK:
                        st = state()
                        for x in g:
                            st["posts"].append({"uid": x["uid"], "account_id": x["account_id"], "account": x["account"],
                                                "file_id": fid, "file_name": x["file_name"], "at": at,
                                                "caption": cap, "status": "scheduled", "recycled": x.get("recycled"),
                                                "origin": origin, "created": iso(utcnow())})
                        save_state(st)
                    log(f"OK {g[0]['file_name']} -> {', '.join(x['account'] for x in g)} @ {g[0]['local']} ET")
                except Exception as e:
                    msg = f"{g[0]['file_name']} : {e}"
                    JOB["errors"].append(msg)
                    log("ERREUR " + msg)
                JOB["progress"] += len(g)
            log(f"Programmation terminee : {JOB['progress'] - len(JOB['errors'])} ok, {len(JOB['errors'])} erreurs")
        finally:
            JOB["running"] = False

    threading.Thread(target=run, daemon=True).start()


def autopilot_loop():
    while True:
        try:
            c = cfg()
            if c["autopilot"] and not JOB["running"]:
                st = state()
                last = parse_dt(st.get("last_autopilot"))
                if not last or (utcnow() - last) > timedelta(hours=3):
                    ins = rjson(INSIGHTS_F, {})
                    if not ins or (utcnow() - (parse_dt(ins.get("fetched_at")) or utcnow() - timedelta(days=9))) > timedelta(hours=24):
                        try:
                            fetch_insights(int(c["analysis_days"]))   # apprentissage continu
                        except Exception as e:
                            log(f"Autopilote : analyse impossible ({e})")
                    plan = build_plan(c, True)
                    st = state()
                    st["last_autopilot"] = iso(utcnow())
                    save_state(st)
                    if plan["items"]:
                        log(f"Autopilote : {len(plan['items'])} creneaux a remplir")
                        commit_plan(plan["items"], "autopilote")
                    if plan["missing"]:
                        log(f"Autopilote : {plan['missing']} creneaux sans video — ajoute du contenu dans le Drive !")
        except Exception as e:
            log(f"Autopilote : {e}")
        time.sleep(600)


def mark_past():
    """Les posts dont l'heure est passee passent en 'publie'."""
    with LOCK:
        st = state()
        ch = False
        for p in st["posts"]:
            if p.get("status") == "scheduled" and (parse_dt(p["at"]) or utcnow()) < utcnow():
                p["status"] = "published"
                ch = True
        if ch:
            save_state(st)
        return st


# ----------------------------------------------------------------------------- serveur

def public_cfg():
    c = cfg()
    out = dict(c)
    for k in ("publer_api_key", "drive_api_key"):
        v = c.get(k) or ""
        out[k] = ("•" * 8 + v[-4:]) if v else ""
        out[k + "_set"] = bool(v)
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def route(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if method == "GET" and p in ("/", "/index.html"):
            return self.send(200, (BASE / "index.html").read_bytes(), "text/html")
        if p == "/api/status":
            st = mark_past()
            now = utcnow()
            up = [x for x in st["posts"] if x["status"] == "scheduled"]
            return self.send(200, {"config": public_cfg(), "job": JOB, "logs": LOGS[-80:],
                                   "scheduled": len(up), "published": sum(1 for x in st["posts"] if x["status"] == "published"),
                                   "last_autopilot": st.get("last_autopilot"),
                                   "until": max([x["at"] for x in up], default=None),
                                   "insights_at": rjson(INSIGHTS_F, {}).get("fetched_at"), "now": iso(now)})
        if p == "/api/config" and method == "POST":
            c = cfg()
            b = self.body()
            for k, v in b.items():
                if k in DEFAULT_CONFIG or k == "account_folders":
                    if k in ("publer_api_key", "drive_api_key") and (not v or "•" in str(v)):
                        continue
                    c[k] = v
            save_cfg(c)
            log("Reglages enregistres")
            return self.send(200, {"ok": True, "config": public_cfg()})
        if p == "/api/workspaces":
            return self.send(200, {"workspaces": list_workspaces()})
        if p == "/api/accounts":
            return self.send(200, {"accounts": list_accounts()})
        if p == "/api/test":
            res = {}
            c = cfg()
            try:
                res["accounts"] = len(list_accounts())
                res["publer"] = "ok"
            except Exception as e:
                res["publer"] = str(e)
            try:
                res["videos"] = len(drive_videos())
                res["drive"] = "ok"
            except Exception as e:
                res["drive"] = str(e)
            res["demo"] = c["demo"]
            return self.send(200, res)
        if p == "/api/drive":
            st = state()
            vids = drive_videos(q.get("folder"))
            used = {}
            for x in st["posts"]:
                used.setdefault(x["file_id"], []).append({"account": x["account"], "at": x["at"], "status": x["status"]})
            for v in vids:
                v["uses"] = used.get(v["id"], [])
            return self.send(200, {"videos": vids})
        if p == "/api/analysis":
            if q.get("refresh") or not INSIGHTS_F.exists():
                c = cfg()
                if q.get("days"):
                    c["analysis_days"] = int(q["days"])
                    save_cfg(c)
                fetch_insights(int(c["analysis_days"]))
            return self.send(200, analyse())
        if p == "/api/plan" and method == "POST":
            c = cfg()
            c.update(self.body() or {})
            return self.send(200, build_plan(c))
        if p == "/api/commit" and method == "POST":
            items = self.body().get("items") or []
            commit_plan(items)
            return self.send(200, {"ok": True})
        if p == "/api/schedule":
            st = mark_past()
            return self.send(200, {"posts": sorted(st["posts"], key=lambda x: x["at"])[-600:]})
        if p == "/api/forget" and method == "POST":
            uid = self.body().get("uid")
            with LOCK:
                st = state()
                st["posts"] = [x for x in st["posts"] if x["uid"] != uid]
                save_state(st)
            return self.send(200, {"ok": True})
        if p == "/api/autopilot" and method == "POST":
            c = cfg()
            c["autopilot"] = bool(self.body().get("on"))
            save_cfg(c)
            st = state()
            st["last_autopilot"] = None
            save_state(st)
            log("Autopilote " + ("ACTIVE" if c["autopilot"] else "coupe"))
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "introuvable"})

    def handle_any(self, m):
        try:
            self.route(m)
        except Exception as e:
            traceback.print_exc()
            self.send(500, {"error": str(e)})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


def main():
    if not CONFIG_F.exists():
        save_cfg(dict(DEFAULT_CONFIG))
    threading.Thread(target=autopilot_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    url = f"http://127.0.0.1:{PORT}/"
    log(f"Pilote en route sur {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
