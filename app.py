#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout App - interface graphique locale (bouton + champ nombre).

Lance :  python3 app.py
Ca ouvre une page dans ton navigateur avec :
  - un champ "nombre de leads" + un bouton "Lancer la recherche"
  - la progression en direct
  - la galerie de swipe des resultats (Garder / Jeter / Exporter)

Zero token, zero dependance (Python stdlib + OpenCLI deja installe).
"""

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from reseau import connexion_ok

BASE = Path(__file__).parent
RES = BASE / "resultats"
PORT = 8765
COOLDOWN = 2 * 3600  # cooldown conseille si Instagram throttle (2h)

STATE = {"running": False, "done": False, "log": [], "count": 0, "target": 0,
         "throttled": False, "cooldown_until": 0}


QUOTA_JOUR = 120     # profils GARDES max par jour (objectif de la journee)
# (plus de plafond de visites : demande explicite, la recherche continue jusqu'aux 120 gardes ;
#  la protection du compte repose sur le rythme humain et le partage de connexion iPhone)


def history():
    return _read_json(RES / "recherches.json", [])


def gardes_du_jour():
    today = time.strftime("%Y-%m-%d")
    done = sum(h.get("gardes", 0) for h in history() if str(h.get("debut", "")).startswith(today))
    return done + (STATE.get("count", 0) if STATE["running"] else 0)


def visites_du_jour():
    today = time.strftime("%Y-%m-%d")
    done = sum(h.get("visites", 0) for h in history() if str(h.get("debut", "")).startswith(today))
    return done + (STATE.get("visites", 0) if STATE["running"] else 0)


PROC = None  # processus scout.py en cours (pour le bouton Arreter)


def stop_search():
    """Arrete proprement la recherche en cours (Scout sauvegarde ce qu'il a deja trouve)."""
    if PROC is not None and PROC.poll() is None:
        PROC.terminate()
        threading.Thread(target=_banner_chrome, daemon=True).start()
        return True
    return False


def _banner_chrome():
    """Affiche l'arret dans la fenetre Chrome Scout (la ou la recherche tournait) et la met devant."""
    try:
        if PROC is not None:
            PROC.wait(timeout=60)
        import navigateur
        navigateur.Onglet().js("""(()=>{const d=document.createElement('div');
          d.textContent='■ Recherche Scout arrêtée — les profils trouvés sont gardés';
          d.style.cssText='position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:2147483647;'
            +'background:#111;color:#fff;font:600 15px -apple-system,sans-serif;padding:12px 20px;border-radius:999px;'
            +'box-shadow:0 8px 30px rgba(0,0,0,.4)';
          document.body.appendChild(d);setTimeout(()=>d.remove(),8000)})()""")
    except Exception:
        pass


def run_scout(n, min_f, max_f, max_visites):
    STATE.update(running=True, done=False, log=[], count=0, target=n, visites=0)
    env = dict(os.environ)
    env["PATH"] = (os.path.expanduser("~/.local/bin") + ":" +
                   os.path.expanduser("~/.local/node-v20.18.0-darwin-x64/bin") +
                   ":" + env.get("PATH", ""))
    args = [sys.executable, "-u", "scout.py", "--target", str(n),
            "--min", str(min_f), "--max", str(max_f), "--max-visites", str(max_visites)]
    try:
        global PROC
        proc = PROC = subprocess.Popen(args, cwd=str(BASE), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env)
        for line in proc.stdout:
            line = line.rstrip()
            STATE["log"].append(line)
            if line.strip().startswith("["):
                STATE["visites"] += 1
                if "GARDE" in line:
                    STATE["count"] += 1
        proc.wait()
        # fichier a glisser dans le bouton "Importer" de la page Vivier sur Claude
        r = subprocess.run([sys.executable, "preparer_vivier.py", "--fichier"], cwd=str(BASE),
                           capture_output=True, text=True, timeout=300)
        STATE["log"].append("Pour Vivier : glisse le fichier « A-IMPORTER-dans-Vivier.json » "
                            "(dossier scout) dans le bouton Importer. " + r.stdout.strip())
    except Exception as e:
        STATE["log"].append(f"ERREUR: {e}")
    # detection du throttle : arret avant l'objectif + message d'epuisement
    log_txt = "\n".join(STATE["log"]).lower()
    hit_target = STATE["count"] >= STATE["target"]
    throttled = (not hit_target) and "arretee a ta demande" not in log_txt and (
        "throttle" in log_txt or "epuisee" in log_txt or "taries" in log_txt
        or STATE["count"] < max(1, int(STATE["target"] * 0.25))
    )
    STATE["throttled"] = throttled
    STATE["cooldown_until"] = (time.time() + COOLDOWN) if throttled else 0
    STATE.update(running=False, done=True)


LIB = RES / "bibliotheque.json"   # tous les leads gardes par Scout, tous runs confondus
ETATS = RES / "etats.json"        # tes choix : handle -> keep | drop | vu
LOCK = threading.Lock()


def _read_json(f, default):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_library():
    """Bibliotheque + dernier run (au cas ou) + backlog, dedoublonnes, plus recents d'abord."""
    lib = {}
    for f in (RES / "export-backlog.json", LIB, RES / "candidates.json"):
        for d in _read_json(f, []):
            if isinstance(d, dict) and d.get("handle"):
                h = d["handle"].lower()
                lib[h] = {**lib.get(h, {}), **d}
    etats = _read_json(ETATS, {})
    out = []
    for h, d in lib.items():
        d.setdefault("url", f"https://www.instagram.com/{d['handle']}/")
        d["etat"] = etats.get(h, "")
        out.append(d)
    out.sort(key=lambda d: d.get("added", ""), reverse=True)
    return out


def set_state(handle, value):
    h = handle.lower().strip()
    if not h:
        return
    with LOCK:
        etats = _read_json(ETATS, {})
        if value in ("keep", "drop", "vu"):
            etats[h] = value
        else:
            etats.pop(h, None)
        RES.mkdir(parents=True, exist_ok=True)
        ETATS.write_text(json.dumps(etats, ensure_ascii=False, indent=1), encoding="utf-8")


def open_chrome(url):
    """Toujours Chrome, jamais Safari (Scout et Instagram sont pilotes dans Chrome)."""
    try:
        subprocess.Popen(["open", "-a", "Google Chrome", url])
    except Exception:
        webbrowser.open(url)


def start_search(n, min_f="1500", max_f="25000"):
    """Demarre une recherche si possible. 200 = lancee (ou deja en cours), 403 = pas sur l'iPhone, 429 = quota."""
    with LOCK:
        if STATE["running"]:
            return 200
        if not connexion_ok()[0]:  # jamais sur l'internet normal : ca ferait reperer le compte
            return 403
        # objectif fixe : 120 profils gardes par jour. Une recherche vise toujours ce qu'il reste
        # pour aujourd'hui et ne s'arrete pas avant (sauf bouton Arreter ou sortie du partage iPhone).
        reste = QUOTA_JOUR - gardes_du_jour()
        if reste <= 0:
            return 429
        try:
            mn, mx = int(min_f), int(max_f)
        except ValueError:
            mn, mx = 1500, 25000
        STATE["running"] = True  # reserve tout de suite : un 2e clic ne relance pas
        threading.Thread(target=run_scout, args=(reste, mn, mx, 0), daemon=True).start()  # 0 = pas de limite de visites
    return 200


_MINI = """<!doctype html><meta charset="utf-8"><title>Scout</title>
<body style="font-family:-apple-system,sans-serif;background:#0f0f11;color:#f2f2f3;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2>%s</h2><p style="color:#9a9aa2">%s</p></div>%s"""
LANCE_PAGE = _MINI % ("🔎 Recherche lancée",
                      "Scout travaille sur Instagram dans la fenêtre Chrome Scout.<br>Cet onglet se ferme tout seul.",
                      "<script>setTimeout(()=>window.close(),1200)</script>")
_CLOSE = "<script>setTimeout(()=>window.close(),1200)</script>"
STOP_PAGE = _MINI % ("■ Recherche arrêtée",
                     "Les profils déjà trouvés sont gardés.<br>Cet onglet se ferme tout seul.", _CLOSE)
NOTHING_PAGE = _MINI % ("Aucune recherche en cours", "Rien à arrêter.<br>Cet onglet se ferme tout seul.", _CLOSE)
RESEAU_PAGE = _MINI % ("📵 Pas sur le partage de connexion iPhone",
                       "Scout ne lance rien sur l'internet normal (ça ferait repérer le compte).<br>"
                       "Connecte le Mac au partage de connexion de « iPhone de Jules 2 », puis relance.", "")
QUOTA_PAGE = _MINI % ("⏳ Quota Instagram du jour atteint",
                      "120 profils déjà visités aujourd'hui. Relance demain pour protéger le compte.", "")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            q = parse_qs(p.query)
            if "lancer" in q:  # bouton "Lancer la recherche" de la page Vivier sur Claude
                # demarre direct sur Instagram (Chrome Scout), sans ouvrir l'app ; l'onglet se referme seul
                code = start_search(q["lancer"][0])
                page = LANCE_PAGE if code == 200 else RESEAU_PAGE if code == 403 else QUOTA_PAGE
                self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
                return
            self._send(200, "text/html; charset=utf-8", INDEX.encode("utf-8"))
        elif p.path == "/arreter":  # bouton "Arreter" de la page Vivier sur Claude
            page = STOP_PAGE if stop_search() else NOTHING_PAGE
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        elif p.path == "/status":
            net_ok, net_msg = connexion_ok()
            s = dict(STATE, quota=QUOTA_JOUR, gardes_jour=gardes_du_jour(), visites_jour=visites_du_jour(), reseau_ok=net_ok, reseau_msg=net_msg,
                     historique=history()[-30:][::-1])
            self._send(200, "application/json", json.dumps(s, ensure_ascii=False).encode("utf-8"))
        elif p.path == "/results":
            self._send(200, "application/json",
                       json.dumps(load_library(), ensure_ascii=False).encode("utf-8"))
        elif p.path.startswith("/shots/"):
            img = RES / "shots" / os.path.basename(p.path)
            if img.exists():
                self._send(200, "image/png", img.read_bytes())
            else:
                self._send(404, "text/plain", b"no")
        else:
            self._send(404, "text/plain", b"no")

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/run":
            if STATE["running"]:
                self._send(409, "application/json", b'{"error":"deja en cours"}')
                return
            q = parse_qs(p.query)
            code = start_search(q.get("n", ["100"])[0], q.get("min", ["1500"])[0],
                                q.get("max", ["25000"])[0])
            if code in (403, 429):
                self._send(code, "application/json", b'{"error":"bloque"}')
                return
            self._send(200, "application/json", b'{"ok":true}')
        elif p.path == "/stop":
            stop_search()
            self._send(200, "application/json", b'{"ok":true}')
        elif p.path == "/etat":
            q = parse_qs(p.query)
            set_state(q.get("h", [""])[0], q.get("v", [""])[0])
            self._send(200, "application/json", b'{"ok":true}')
        else:
            self._send(404, "text/plain", b"no")


INDEX = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scout</title>
<style>
  :root{--bg:#0f0f11;--card:#1b1b1f;--txt:#f2f2f3;--mut:#9a9aa2;--acc:#3b82f6;
    --keep:#2ecc71;--drop:#e74c3c}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  header{padding:20px;border-bottom:1px solid #26262b}
  h1{margin:0 0 4px;font-size:20px} .sub{color:var(--mut);font-size:13px}
  .panel{padding:20px;display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;
    border-bottom:1px solid #26262b}
  label{display:block;font-size:12px;color:var(--mut);margin-bottom:5px}
  input{background:#121216;border:1px solid #2c2c33;color:var(--txt);border-radius:8px;
    padding:10px 12px;font-size:15px;width:110px}
  button{cursor:pointer;border:0;border-radius:8px;padding:11px 18px;font-weight:700;font-size:14px}
  .go{background:var(--acc);color:#fff} .go:disabled{opacity:.5;cursor:default}
  #prog{padding:0 20px;max-height:0;overflow:hidden;transition:max-height .3s}
  #prog.on{max-height:260px;padding:14px 20px}
  .bar{height:8px;background:#26262b;border-radius:6px;overflow:hidden;margin-bottom:10px}
  .bar > i{display:block;height:100%;background:var(--acc);width:0;transition:width .4s}
  #log{font-family:ui-monospace,monospace;font-size:11px;color:var(--mut);
    max-height:180px;overflow:auto;white-space:pre-wrap}
  .exp{background:var(--acc);color:#fff}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;padding:18px}
  .card{background:var(--card);border-radius:12px;overflow:hidden;border:2px solid transparent}
  .card.keep{border-color:var(--keep)} .card.drop{opacity:.4;border-color:var(--drop)}
  .card img{width:100%;display:block;aspect-ratio:.82;object-fit:cover;background:#000}
  .meta{padding:10px 12px} .h{font-weight:700} .s{color:var(--mut);font-size:12px;margin-top:2px}
  .bio{color:#c7c7cf;font-size:11px;margin-top:6px;max-height:30px;overflow:hidden}
  .row{display:flex;gap:8px;padding:0 12px 12px} .row button{flex:1}
  .k{background:#123d24;color:var(--keep)} .d{background:#3d1717;color:var(--drop)}
  .card.keep .k{background:var(--keep);color:#00220f} .card.drop .d{background:var(--drop);color:#2b0606}
  a{color:#7ab7ff;font-size:12px;text-decoration:none}
  .card .pic{display:block;position:relative} .card .h a{color:var(--txt);font-size:inherit}
  .badge{position:absolute;top:8px;left:8px;background:#000a;color:#ddd;font-size:11px;
    padding:3px 8px;border-radius:20px}
  .badge.ci{top:auto;bottom:8px;background:#8a3ab9;color:#fff;font-weight:700}
  .badge.of,.badge.lk{left:auto;right:8px} .badge.of{background:#e1306c;color:#fff;font-weight:700}
  .tabs{display:flex;gap:8px;padding:14px 18px 0;flex-wrap:wrap}
  .tab{background:#1b1b1f;color:var(--mut);padding:8px 14px;font-size:13px}
  .tab.on{background:var(--txt);color:var(--bg)}
  .empty{padding:40px 20px;color:var(--mut);text-align:center}
  .layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;align-items:start}
  .layout main{min-width:0;border-right:1px solid #26262b}
  aside{position:sticky;top:0;padding:18px;display:grid;gap:18px;max-height:100vh;overflow:auto}
  aside h2{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut)}
  .quota b{font-size:22px} .quota .bar{margin:8px 0 6px} .quota.full .bar>i{background:var(--drop)}
  .quota.warn .bar>i{background:#f0a83a}
  .credit{display:flex;justify-content:space-between;align-items:baseline;background:var(--card);border-radius:10px;padding:12px}
  .credit b{font-size:20px;color:var(--keep)}
  .hist{display:grid;gap:8px} .hrow{background:var(--card);border-radius:10px;padding:10px 12px;font-size:12px}
  .hrow .t{display:flex;justify-content:space-between;font-weight:700;font-size:13px;margin-bottom:3px}
  .hrow .m{color:var(--mut)} .pill{font-size:10px;font-weight:700;padding:2px 7px;border-radius:20px;background:#26262b;color:var(--mut)}
  .pill.quota{background:#3d1717;color:var(--drop)} .pill.termine{background:#123d24;color:var(--keep)}
  @media (max-width:820px){.layout{grid-template-columns:1fr}.layout main{border-right:0}aside{position:static;max-height:none;border-top:1px solid #26262b}}
</style></head><body>
<header><h1>🔎 Scout</h1><div class="sub">Recherche de leads Instagram — 100% local <span id="lan"></span></div></header>
<div class="layout"><main>
<div class="panel">
  <div><label>Nombre de leads (max 120)</label><input id="n" type="number" value="100" min="1" max="120"></div>
  <div><label>Abonnés min</label><input id="min" type="number" value="1500"></div>
  <div><label>Abonnés max</label><input id="max" type="number" value="25000"></div>
  <button class="go" id="go" onclick="launch()">🔍 Lancer la recherche</button>
  <button class="d" id="stop" style="display:none" onclick="stopIt()">■ Arrêter</button>
  <button class="exp" id="exp" style="display:none" onclick="exportKeep()">Exporter les gardés ⬇️</button>
  <span class="sub" id="cc" style="margin-left:auto"></span>
</div>
<div id="cool" style="display:none;padding:14px 20px;background:#2a1e0a;color:#ffcf7a;
  border-bottom:1px solid #3a2c12;font-size:14px"></div>
<div id="prog"><div class="bar"><i id="fill"></i></div><div id="log"></div></div>
<div class="tabs" id="tabs"></div>
<div class="grid" id="grid"></div>
</main>
<aside>
  <section><h2>Connexion</h2><div class="credit"><span class="sub" id="net-txt">…</span><b id="net-ok">…</b></div></section>
  <section class="quota" id="quota"><h2>Quota Instagram du jour</h2>
    <div><b id="q-n">0</b> / <span id="q-max">120</span> profils visités</div>
    <div class="bar"><i id="q-fill"></i></div>
    <div class="sub" id="q-txt"></div></section>
  <section><h2>Crédits Claude</h2>
    <div class="credit"><span class="sub">Dépensés par Scout</span><b>0</b></div>
    <div class="sub" style="margin-top:6px">Scout tourne sur ton Mac : les recherches ne coûtent aucun crédit.</div></section>
  <section><h2>Historique des recherches</h2><div class="hist" id="hist"><div class="sub">Aucune recherche pour l'instant.</div></div></section>
</aside></div>
<script>
let DATA=[], state={}, poll=null, tab='tous';
async function launch(){
  const n=+document.getElementById('n').value||100;
  const mn=+document.getElementById('min').value||1500;
  const mx=+document.getElementById('max').value||25000;
  document.getElementById('go').disabled=true;
  document.getElementById('prog').classList.add('on');
  document.getElementById('grid').innerHTML='';
  document.getElementById('exp').style.display='none';
  const r=await fetch(`/run?n=${n}&min=${mn}&max=${mx}`,{method:'POST'});
  if(!r.ok){document.getElementById('go').disabled=false;
    alert(r.status===403?"Pas sur le partage de connexion iPhone : Scout ne lance rien sur l'internet normal. Connecte le Mac à « iPhone de Jules 2 » puis relance."
      :r.status===429?'Quota Instagram du jour atteint (120 profils visités). Relance demain pour protéger le compte.'
      :'Une recherche est déjà en cours.');return;}
  poll=setInterval(tick,1500); tick();
}
async function tick(){
  const s=await (await fetch('/status')).json();
  side(s);
  const st=document.getElementById('stop');st.style.display=s.running?'':'none';
  if(!s.running){st.disabled=false;st.textContent='■ Arrêter';}
  document.getElementById('log').textContent=s.log.slice(-40).join('\n');
  document.getElementById('log').scrollTop=9e9;
  const pct=s.target?Math.min(100,Math.round(100*s.count/s.target)):0;
  document.getElementById('fill').style.width=pct+'%';
  document.getElementById('cc').textContent=`${s.count}/${s.target} trouvés`;
  if(s.done){clearInterval(poll);loadResults();
    document.getElementById('go').disabled=false;
    if(s.throttled && s.cooldown_until){startCooldown(s.cooldown_until*1000);}
    else{document.getElementById('cool').style.display='none';}
  }
}
let coolTimer=null;
function startCooldown(until){
  const box=document.getElementById('cool'); box.style.display='block';
  clearInterval(coolTimer);
  function upd(){
    const left=Math.max(0,Math.round((until-Date.now())/1000));
    if(left<=0){clearInterval(coolTimer);
      box.textContent='✅ Instagram a récupéré, tu peux relancer une recherche.';return;}
    const h=Math.floor(left/3600),m=Math.floor(left%3600/60),sec=left%60;
    box.textContent=`⏳ Instagram limite les requêtes (throttle). Prochaine recherche conseillée dans `
      +`${h}h ${String(m).padStart(2,'0')}m ${String(sec).padStart(2,'0')}s. `
      +`(Tu peux relancer quand même, mais ça risque de ne rien trouver.)`;
  }
  upd(); coolTimer=setInterval(upd,1000);
}
async function loadResults(){
  DATA=await (await fetch('/results')).json();
  state={}; DATA.forEach(d=>{if(d.etat)state[d.handle]=d.etat;});
  render(); if(DATA.length)document.getElementById('exp').style.display='';
}
const TABS={tous:['Tous',d=>state[d.handle]!=='drop'],
            keep:['Gardés',d=>state[d.handle]==='keep'],
            drop:['Jetés',d=>state[d.handle]==='drop']};
function render(){
  document.getElementById('tabs').innerHTML=Object.entries(TABS).map(([k,[name,f]])=>
    `<button class="tab ${k===tab?'on':''}" onclick="tab='${k}';render()">${name} (${DATA.filter(f).length})</button>`).join('');
  const g=document.getElementById('grid'); g.innerHTML='';
  const rows=DATA.filter(TABS[tab][1]).sort((a,b)=>(b.priorite||1)-(a.priorite||1));  // OF direct en tete
  if(!rows.length){g.innerHTML='<div class="empty">Aucun profil ici pour l\'instant.</div>';return;}
  rows.forEach(d=>{const st=state[d.handle]||'';
    const img=d.shot&&!d.shot.startsWith('data:')?'/'+d.shot:(d.thumb||d.shot||'');
    const c=document.createElement('div');c.className='card '+st;
    c.innerHTML=`<a class="pic" href="${d.url}" target="_blank" onclick="vu('${d.handle}')">
        <img src="${img}" loading="lazy">${st==='vu'?'<span class="badge">déjà ouvert</span>':''}
        ${d.lien_type==='of'?'<span class="badge of">★ OF direct</span>':d.lien_type==='autre'?'<span class="badge lk">lien en bio</span>':''}
        ${d.creatorsinc?'<span class="badge ci">creatorsinc</span>':''}</a>
      <div class="meta"><div class="h"><a href="${d.url}" target="_blank" onclick="vu('${d.handle}')">@${d.handle}</a> ${d.verified?'✔️':''}</div>
      <div class="s">${(d.followers||0).toLocaleString()} abonnés · ${d.posts??'?'} posts${d.likes!=null?' · '+d.likes.toLocaleString()+' likes':''}</div>
      <div class="bio">${(d.bio||'').replace(/</g,'&lt;')}</div></div>
      <div class="row"><button class="k" onclick="set('${d.handle}','keep')">Garder</button>
      <button class="d" onclick="set('${d.handle}','drop')">Jeter</button></div>`;
    g.appendChild(c);});
}
function save(h){fetch(`/etat?h=${encodeURIComponent(h)}&v=${state[h]||''}`,{method:'POST'});}
function set(h,v){state[h]=(state[h]===v?'':v);save(h);render();}
function vu(h){if(!state[h]){state[h]='vu';save(h);setTimeout(render,300);}}
function exportKeep(){
  const keep=DATA.filter(d=>state[d.handle]==='keep');
  const csv='handle,followers,posts,url\n'+keep.map(d=>`${d.handle},${d.followers},${d.posts},${d.url}`).join('\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download='leads_gardes.csv';a.click();
}
const STATUTS={termine:'terminée',quota:'quota atteint',epuise:'sources épuisées',arrete:'arrêtée',reseau:'bloquée (réseau)'};
async function stopIt(){const b=document.getElementById('stop');b.disabled=true;b.textContent='Arrêt…';
  await fetch('/stop',{method:'POST'});}
function side(s){
  const nok=document.getElementById('net-ok');nok.textContent=s.reseau_ok?'✓':'✕';
  nok.style.color=s.reseau_ok?'var(--keep)':'var(--drop)';
  document.getElementById('net-txt').textContent=s.reseau_ok?'Partage de connexion iPhone':'Internet normal : recherche bloquée';
  const v=s.gardes_jour||0,q=s.quota||120,pct=Math.min(100,Math.round(100*v/q));
  document.getElementById('q-n').textContent=v;document.getElementById('q-max').textContent=q;
  document.getElementById('q-fill').style.width=pct+'%';
  document.getElementById('quota').className='quota'+(v>=q?' full':v>=q*.75?' warn':'');
  document.getElementById('q-txt').textContent=v>=q?'Quota atteint : relance demain pour protéger le compte.'
    :`Encore ${q-v} profils possibles aujourd'hui. Scout s'arrête tout seul à ${q}.`;
  const h=document.getElementById('hist');
  if(!(s.historique||[]).length)return;
  h.innerHTML='';
  s.historique.forEach(r=>{const d=new Date(r.debut);
    const el=document.createElement('div');el.className='hrow';
    el.innerHTML=`<div class="t"><span></span><span class="pill ${r.statut}"></span></div><div class="m"></div>`;
    el.querySelector('.t span').textContent=d.toLocaleDateString('fr-FR',{day:'numeric',month:'short'})+' · '+d.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
    el.querySelector('.pill').textContent=STATUTS[r.statut]||r.statut;
    el.querySelector('.m').textContent=`${r.gardes}/${r.demandes} gardés · ${r.visites} visités · ${r.duree_min} min · ${r.credits_claude||0} crédit`;
    h.appendChild(el);});
}
// lien "Lancer la recherche" de la page Vivier sur Claude : /?lancer=N demarre direct
const auto=new URLSearchParams(location.search).get('lancer');
if(auto){history.replaceState(null,'','/');  // un rechargement ne relance pas
  document.getElementById('n').value=Math.max(1,Math.min(120,parseInt(auto,10)||100));
  fetch('/status').then(r=>r.json()).then(s=>{if(s.running){document.getElementById('prog').classList.add('on');
    document.getElementById('go').disabled=true;poll=setInterval(tick,1500);}else launch();});}
// adresse pour le telephone + resultats deja presents
fetch('/status').then(r=>r.json()).then(s=>{side(s);if(s.lan_url)
  document.getElementById('lan').innerHTML=' · 📱 tél (même WiFi) : <b>'+s.lan_url+'</b>';});
loadResults();
</script></body></html>"""


def lan_ip():
    """IP locale du Mac sur le WiFi (pour y acceder depuis le telephone)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    global PORT
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # accessible sur le WiFi
            break
        except OSError:
            PORT += 1
    else:
        sys.exit("Aucun port libre trouve.")
    local = f"http://127.0.0.1:{PORT}"
    ip = lan_ip()
    STATE["lan_url"] = f"http://{ip}:{PORT}" if ip else ""
    print(f"Scout (cet ordi)              : {local}", flush=True)
    if ip:
        print(f"Scout (telephone, meme WiFi)  : http://{ip}:{PORT}", flush=True)
    print("(Ctrl+C pour quitter)", flush=True)
    if "--fond" not in sys.argv:  # --fond : demarre en arriere-plan (ouverture de session), sans ouvrir de page
        threading.Timer(0.8, lambda: open_chrome(local)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nFerme.")


if __name__ == "__main__":
    main()
