#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Navigateur Scout - remplacant maison d'OpenCLI (zero dependance, Python stdlib).

Pilote un Chrome dedie via le protocole DevTools integre a Chrome (CDP) :
pas d'extension, pas de daemon, pas de npm. Le Chrome Scout a son propre
profil (~/.scout-chrome) : tu t'y connectes UNE fois au compte scouting,
la session reste memorisee.

Usage :
    python3 navigateur.py ouvrir      # lance le Chrome Scout (1re fois : connecte-toi a Instagram)
    python3 navigateur.py doctor      # verifie que tout marche + compte connecte

Dans scout.py, run([...]) appelle navigateur.run([...]) qui comprend les memes
commandes qu'OpenCLI et renvoie le meme format de texte.
"""

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

PORT = int(os.environ.get("SCOUT_CHROME_PORT", "9333"))
PROFIL = os.path.expanduser(os.environ.get("SCOUT_CHROME_PROFIL", "~/.scout-chrome"))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SESSION_ID = "scoutnav"  # id fixe, au meme format que les profils OpenCLI


# ----------------------------------------------------------------------------
# Mini client WebSocket (juste ce qu'il faut pour parler a Chrome)
# ----------------------------------------------------------------------------

class WebSocket:
    def __init__(self, url, timeout=60):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake websocket refuse")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError("handshake websocket refuse: " + resp[:200].decode(errors="replace"))
        self.buf = resp.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("connexion Chrome fermee")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if b2 & 0x80:
                mask = self._read(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                payload = self._read(n)
            if op == 0x8:
                raise ConnectionError("connexion Chrome fermee")
            if op == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if op in (0x0, 0x1, 0x2):
                parts.append(payload)
                if b1 & 0x80:
                    return b"".join(parts).decode()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Chrome Scout : lancement + connexion a l'onglet
# ----------------------------------------------------------------------------

def _http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def chrome_actif():
    try:
        _http("/json/version")
        return True
    except Exception:
        return False


def lancer_chrome(url="https://www.instagram.com/"):
    """Lance le Chrome Scout (profil dedie) s'il ne tourne pas deja."""
    if chrome_actif():
        return True
    if not os.path.exists(CHROME):
        sys.exit("ERREUR : Google Chrome introuvable dans /Applications.")
    os.makedirs(PROFIL, exist_ok=True)
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFIL}",
         "--no-first-run", "--no-default-browser-check", "--window-size=1280,900",
         "--lang=en-US", "-AppleLanguages", "(en-US)", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        time.sleep(0.5)
        if chrome_actif():
            return True
    return False


class Onglet:
    """Connexion CDP a un onglet du Chrome Scout."""

    def __init__(self, cible=None):
        if cible is None:
            pages = [t for t in _http("/json/list") if t.get("type") == "page"
                     and t.get("id") != _FEED_ID  # l'onglet du feed est reserve au feed
                     and not t.get("url", "").startswith("devtools://")
                     and not any(x in t.get("url", "") for x in ("claude.ai", "accounts.google"))]  # onglet Vivier : pas touche
            pages.sort(key=lambda t: "instagram.com" not in t.get("url", ""))  # l'onglet Instagram d'abord
            if not pages:
                pages = [_http("/json/new?about:blank", method="PUT")]
            cible = pages[0]
        self.id = cible.get("id")
        self.ws = WebSocket(cible["webSocketDebuggerUrl"])
        self.n = 0
        try:  # met la fenetre Chrome Scout au premier plan : on voit Scout travailler sur Insta
            self.cmd("Page.bringToFront", timeout=10)
        except Exception:
            pass
        # Instagram en anglais US : sinon il traduit automatiquement les bios en francais
        # ("Just for fun" -> "Juste pour le plaisir") et le filtre anti-francais se tromperait
        try:
            ua = self.cmd("Browser.getVersion", timeout=10).get("userAgent", "")
            self.cmd("Emulation.setUserAgentOverride",  # (pas Network.enable : flot de messages)
                     {"userAgent": ua, "acceptLanguage": "en-US,en;q=0.9", "platform": "MacIntel"}, timeout=10)
            self.cmd("Emulation.setLocaleOverride", {"locale": "en-US"}, timeout=10)
        except Exception:
            pass

    def cmd(self, method, params=None, timeout=60):
        self.n += 1
        mid = self.n
        self.ws.sock.settimeout(timeout)
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result", {})

    def js(self, expression, timeout=60):
        r = self.cmd("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                          "returnByValue": True}, timeout=timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(d.get("exception", {}).get("description") or d.get("text", "erreur JS"))
        return r.get("result", {}).get("value")

    def ouvrir(self, url, timeout=40):
        self.cmd("Page.navigate", {"url": url}, timeout=timeout)
        fin = time.time() + timeout
        time.sleep(0.5)
        while time.time() < fin:
            try:
                if self.js("document.readyState", timeout=10) == "complete":
                    return
            except Exception:
                pass  # page en cours de remplacement
            time.sleep(0.4)

    def souris(self, x, y, kind, **extra):
        p = {"type": kind, "x": x, "y": y}
        p.update(extra)
        self.cmd("Input.dispatchMouseEvent", p)


_ONGLET = None   # onglet "profil" : Scout y ouvre les profils et les posts
_FEED = None     # onglet "feed" : le feed reste ouvert ici et n'est jamais recharge
_FEED_ID = None


def onglet():
    global _ONGLET
    if _ONGLET is None:
        if not lancer_chrome():
            raise ConnectionError("Chrome Scout ne demarre pas")
        _ONGLET = Onglet()  # un seul onglet : feed -> clic sur le profil -> retour arriere -> feed
    return _ONGLET


def onglet_feed():
    """Onglet dedie au feed : ouvert une fois, puis on y revient sans jamais le recharger
    (sinon Instagram ne remontre que les 4 premiers posts)."""
    global _FEED, _FEED_ID
    if _FEED is not None:
        return _FEED
    if not lancer_chrome():
        raise ConnectionError("Chrome Scout ne demarre pas")
    cible = _http("/json/new?about:blank", method="PUT")
    _FEED_ID = cible.get("id")
    _FEED = Onglet(cible)
    import atexit  # a la fin de la recherche, on ferme cet onglet (pas d'onglets qui s'accumulent)
    atexit.register(lambda: _fermer(_FEED_ID))
    return _FEED


def _fermer(target_id):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{target_id}", timeout=5).read()
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Commandes (memes noms et meme format de sortie qu'OpenCLI)
# ----------------------------------------------------------------------------

# Cherche des elements, leur colle un numero (data-scout-ref) reutilisable par click.
FIND_JS = r"""(() => {
  const o = %s;
  let els;
  if (o.css) els = [...document.querySelectorAll(o.css)];
  else {
    const sel = o.role === 'button' ? 'button,[role="button"]'
              : o.role === 'link' ? 'a[href],[role="link"]' : '[role="' + o.role + '"]';
    els = [...document.querySelectorAll(sel)];
    if (o.name) {
      const want = o.name.toLowerCase();
      els = els.filter(e => {
        const labels = [e.getAttribute('aria-label'), e.textContent,
          ...[...e.querySelectorAll('[aria-label],title')].map(x => x.getAttribute('aria-label') || x.textContent)];
        return labels.some(l => l && l.trim().toLowerCase().includes(want));
      });
    }
  }
  let max = 0;
  document.querySelectorAll('[data-scout-ref]').forEach(e => { max = Math.max(max, +e.getAttribute('data-scout-ref') || 0); });
  return els.slice(0, o.limit).map(e => {
    let ref = e.getAttribute('data-scout-ref');
    if (!ref) { ref = String(++max); e.setAttribute('data-scout-ref', ref); }
    const t = (e.textContent || '').trim();
    const r = { ref: +ref, tag: e.tagName.toLowerCase(), text: t.length > o.textMax ? t.slice(0, o.textMax) : t };
    const h = e.getAttribute('href'); if (h) r.href = h;
    const a = e.getAttribute('aria-label'); if (a) r.label = a;
    return r;
  });
})()"""

WHOAMI_JS = r"""(async () => {
  if (!/ds_user_id=/.test(document.cookie)) return '';
  const r = await fetch('/api/v1/accounts/edit/web_form_data/', {credentials: 'include',
    headers: {'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest'}});
  const m = (await r.text()).match(/"username":"([^"]+)"/);
  return m ? m[1] : '';
})()"""


def _opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def cmd_browser(args):
    """args = [session, action, ...]"""
    if len(args) < 2:
        return ""
    action, rest = args[1], args[2:]
    o = onglet()

    if action == "open":
        o.ouvrir(rest[0])
        return f"opened {rest[0]}\n"

    if action == "find":
        opts = {"css": _opt(rest, "--css"), "role": _opt(rest, "--role"),
                "name": _opt(rest, "--name"), "limit": int(_opt(rest, "--limit", "50")),
                "textMax": int(_opt(rest, "--text-max", "120"))}
        res = o.js(FIND_JS % json.dumps(opts)) or []
        return json.dumps({"matches": res, "count": len(res)}, ensure_ascii=False, indent=2) + "\n"

    if action == "click":
        ref = rest[0]
        box = o.js("""(() => { const e = document.querySelector('[data-scout-ref="%s"]');
          if (!e) return null; e.scrollIntoView({block: 'center'});
          const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()""" % ref)
        if not box:
            return f"error: ref {ref} introuvable\n"
        x, y = box
        o.souris(x, y, "mouseMoved")
        o.souris(x, y, "mousePressed", button="left", clickCount=1)
        o.souris(x, y, "mouseReleased", button="left", clickCount=1)
        return f"clicked {ref}\n"

    if action == "scroll":
        direction = rest[0] if rest else "down"
        fois = int(_opt(rest, "--repeat", "1"))
        dy = -800 if direction == "up" else 800
        for _ in range(fois):
            o.souris(640, 450, "mouseWheel", deltaX=0, deltaY=dy)
            time.sleep(0.4)
        return "scrolled\n"

    if action == "eval":
        v = o.js(rest[0], timeout=600)
        return (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) + "\n"

    if action == "screenshot":
        data = o.cmd("Page.captureScreenshot", {"format": "png"})["data"]
        with open(rest[0], "wb") as f:
            f.write(base64.b64decode(data))
        return f"saved {rest[0]}\n"

    if action == "get" and rest[:1] == ["text"]:
        sel = rest[1] if len(rest) > 1 else "body"
        v = o.js("(() => { const e = document.querySelector(%s); return e ? e.textContent : ''; })()"
                 % json.dumps(sel))
        return (v or "") + "\n"

    return f"error: commande inconnue {action}\n"


def whoami():
    o = onglet()
    url = o.js("location.href") or ""
    if "instagram.com" not in url:
        o.ouvrir("https://www.instagram.com/")
    try:
        return o.js(WHOAMI_JS) or ""
    except Exception:
        return ""


def run(args):
    """Point d'entree utilise par scout.py (meme contrat que le binaire opencli)."""
    global _ONGLET
    try:
        if args[:2] == ["profile", "list"]:
            return f"{SESSION_ID} — connected\n" if lancer_chrome() else ""
        if args[:1] == ["doctor"]:
            return f"Extension: connected\n  • {SESSION_ID}: connected\n" if lancer_chrome() else ""
        if args[:2] == ["instagram", "whoami"]:
            u = whoami()
            return f"username: {u}\n" if u else ""
        if args[:1] == ["browser"]:
            return cmd_browser(args[1:])
        return ""
    except (ConnectionError, OSError, socket.timeout):
        _ONGLET = None  # Chrome ferme ou onglet perdu : on se reconnectera au prochain appel
        return ""


# ----------------------------------------------------------------------------
# Ligne de commande
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    if action == "ouvrir":
        ok = lancer_chrome()
        print("Chrome Scout ouvert." if ok else "ERREUR : Chrome Scout ne demarre pas.")
        print("1re fois : connecte-toi au compte SCOUTING dans cette fenetre, la session restera memorisee.")
    elif action == "doctor":
        if not lancer_chrome():
            sys.exit("ERREUR : Chrome Scout ne demarre pas.")
        print(f"Chrome Scout : OK (port {PORT}, profil {PROFIL})")
        u = whoami()
        print(f"Compte Instagram connecte : @{u}" if u else
              "Pas connecte a Instagram : connecte-toi au compte scouting dans la fenetre Chrome Scout.")
    else:
        print(run(sys.argv[1:]), end="")
