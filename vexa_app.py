#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vexa — application locale.

Contrairement à video_spoofer_web.py (pensé pour un serveur partagé avec
code d'accès + tunnel), ce point d'entrée lance Vexa comme une vraie
application : tout tourne sur TA machine, en 127.0.0.1 uniquement, sans
code d'accès, sans dépendance à la connexion de qui que ce soit d'autre.
Chaque personne qui installe l'app a sa propre vitesse, basée sur son
propre ordinateur.
"""

import os
import threading
import time
import webbrowser

os.environ["VEXA_APP_LOCAL"] = "1"  # doit être mis AVANT l'import ci-dessous

from video_spoofer_web import app  # noqa: E402

PORT = 8766


def _ouvrir_navigateur():
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


if __name__ == "__main__":
    threading.Thread(target=_ouvrir_navigateur, daemon=True).start()
    print(f"Vexa sur http://127.0.0.1:{PORT}/ (application locale, rien n'est partagé sur le réseau)")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
