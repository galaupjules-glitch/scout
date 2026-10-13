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

BASE = Path(__file__).parent
RES = BASE / "resultats"
PORT = 8765
COOLDOWN = 2 * 3600  # cooldown conseille si Instagram throttle (2h)

STATE = {"running": False, "done": False, "log": [], "count": 0, "target": 0,
         "throttled": False, "cooldown_until": 0}


def run_scout(n, min_f, max_f):
    STATE.update(running=True, done=False, log=[], count=0, target=n)
    env = dict(os.environ)
    env["PATH"] = (os.path.expanduser("~/.local/bin") + ":" +
                   os.path.expanduser("~/.local/node-v20.18.0-darwin-x64/bin") +
                   ":" + env.get("PATH", ""))
    args = [sys.executable, "-u", "scout.py", "--target", str(n),
            "--min", str(min_f), "--max", str(max_f)]
    try:
        proc = subprocess.Popen(args, cwd=str(BASE), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env)
        for line in proc.stdout:
            line = line.rstrip()
            STATE["log"].append(line)
            if line.strip().startswith("[") and "GARDE" in line:
                STATE["count"] += 1
        proc.wait()
    except Exception as e:
        STATE["log"].append(f"ERREUR: {e}")
    # detection du throttle : arret avant l'objectif + message d'epuisement
    log_txt = "\n".join(STATE["log"]).lower()
    hit_target = STATE["count"] >= STATE["target"]
    throttled = (not hit_target) and (
        "throttle" in log_txt or "epuisee" in log_txt or "taries" in log_txt
        or STATE["count"] < max(1, int(STATE["target"] * 0.25))
    )
    STATE["throttled"] = throttled
    STATE["cooldown_until"] = (time.time() + COOLDOWN) if throttled else 0
    STATE.update(running=False, done=True)


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
            self._send(200, "text/html; charset=utf-8", INDEX.encode("utf-8"))
        elif p.path == "/status":
            self._send(200, "application/json",
                       json.dumps(STATE, ensure_ascii=False).encode("utf-8"))
        elif p.path == "/results":
            f = RES / "candidates.json"
            body = f.read_bytes() if f.exists() else b"[]"
            self._send(200, "application/json", body)
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
            n = int(q.get("n", ["100"])[0])
            mn = int(q.get("min", ["1500"])[0])
            mx = int(q.get("max", ["25000"])[0])
            threading.Thread(target=run_scout, args=(n, mn, mx), daemon=True).start()
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
</style></head><body>
<header><h1>🔎 Scout</h1><div class="sub">Recherche de leads Instagram — 100% local <span id="lan"></span></div></header>
<div class="panel">
  <div><label>Nombre de leads</label><input id="n" type="number" value="100" min="1" max="500"></div>
  <div><label>Abonnés min</label><input id="min" type="number" value="1500"></div>
  <div><label>Abonnés max</label><input id="max" type="number" value="25000"></div>
  <button class="go" id="go" onclick="launch()">🔍 Lancer la recherche</button>
  <button class="exp" id="exp" style="display:none" onclick="exportKeep()">Exporter les gardés ⬇️</button>
  <span class="sub" id="cc" style="margin-left:auto"></span>
</div>
<div id="cool" style="display:none;padding:14px 20px;background:#2a1e0a;color:#ffcf7a;
  border-bottom:1px solid #3a2c12;font-size:14px"></div>
<div id="prog"><div class="bar"><i id="fill"></i></div><div id="log"></div></div>
<div class="grid" id="grid"></div>
<script>
let DATA=[], state={}, poll=null;
async function launch(){
  const n=+document.getElementById('n').value||100;
  const mn=+document.getElementById('min').value||1500;
  const mx=+document.getElementById('max').value||25000;
  document.getElementById('go').disabled=true;
  document.getElementById('prog').classList.add('on');
  document.getElementById('grid').innerHTML='';
  document.getElementById('exp').style.display='none';
  const r=await fetch(`/run?n=${n}&min=${mn}&max=${mx}`,{method:'POST'});
  if(!r.ok){alert('Une recherche est déjà en cours.');return;}
  poll=setInterval(tick,1500); tick();
}
async function tick(){
  const s=await (await fetch('/status')).json();
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
  const seen=new Set(); DATA=DATA.filter(d=>seen.has(d.handle)?false:seen.add(d.handle));
  render(); if(DATA.length)document.getElementById('exp').style.display='';
}
function render(){
  const g=document.getElementById('grid'); g.innerHTML='';
  DATA.forEach(d=>{const st=state[d.handle]||'';
    const c=document.createElement('div');c.className='card '+st;
    c.innerHTML=`<img src="/${d.shot}" loading="lazy">
      <div class="meta"><div class="h">@${d.handle} ${d.verified?'✔️':''}</div>
      <div class="s">${(d.followers||0).toLocaleString()} abonnés · ${d.posts} posts${d.likes!=null?' · '+d.likes.toLocaleString()+' likes':''}</div>
      <div class="bio">${(d.bio||'').replace(/</g,'&lt;')}</div>
      <a href="${d.url}" target="_blank">ouvrir le profil ↗</a></div>
      <div class="row"><button class="k" onclick="set('${d.handle}','keep')">Garder</button>
      <button class="d" onclick="set('${d.handle}','drop')">Jeter</button></div>`;
    g.appendChild(c);});
}
function set(h,v){state[h]=(state[h]===v?'':v);render();}
function exportKeep(){
  const keep=DATA.filter(d=>state[d.handle]==='keep');
  const csv='handle,followers,posts,url\n'+keep.map(d=>`${d.handle},${d.followers},${d.posts},${d.url}`).join('\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download='leads_gardes.csv';a.click();
}
// adresse pour le telephone + resultats deja presents
fetch('/status').then(r=>r.json()).then(s=>{if(s.lan_url)
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
    threading.Timer(0.8, lambda: webbrowser.open(local)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nFerme.")


if __name__ == "__main__":
    main()
