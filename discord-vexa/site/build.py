#!/usr/bin/env python3
"""Fabrique le site statique (index.html) qui envoie les candidatures dans Discord.

    WEBHOOK=https://discord.com/api/webhooks/... ADMIN=motdepasse python3 site/build.py sortie/

Le webhook n'est jamais écrit dans le dépôt : seulement dans le dossier de sortie.
"""
import importlib.util, json, os, sys
from pathlib import Path

ICI = Path(__file__).parent
RACINE = ICI.parent.parent / "candidature"
spec = importlib.util.spec_from_file_location("s", RACINE / "serveur.py")
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)

cfg = {"agence": s.NOM_AGENCE, "wpm_min": s.WPM_MIN, "precision_min": s.PRECISION_MIN,
       "essais": s.ESSAIS_FRAPPE, "shifts": s.SHIFTS, "max_fichiers": s.MAX_FICHIERS}
js = (ICI / "backend-discord.js").read_text("utf-8")
js = (js.replace("__WEBHOOK__", os.environ["WEBHOOK"]).replace("__ADMIN__", os.environ["ADMIN"])
        .replace("__CFG__", json.dumps(cfg, ensure_ascii=False)).replace("__TEXTES__", json.dumps(s.TEXTES_FRAPPE)))
html = (RACINE / "page" / "candidat.html").read_text("utf-8")
marque = "<script>\nconst $ = s"
assert html.count(marque) == 1
html = html.replace(marque, "<script>\n" + js + "\n</script>\n" + marque)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
(out / "index.html").write_text(html, "utf-8")
print(out / "index.html")
