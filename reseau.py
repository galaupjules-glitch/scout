#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Garde-fou reseau : Scout et le dressage ne tournent QUE sur le partage de connexion iPhone.

Sur l'internet normal (box, WiFi d'un lieu), l'activite du compte scouting le fait reperer.
Un iPhone en partage de connexion donne toujours la passerelle 172.20.10.1 (WiFi, USB ou
Bluetooth) : c'est ce qu'on verifie. (macOS masque le nom du reseau WiFi, d'ou cette methode.)

    python3 reseau.py      -> affiche si la connexion actuelle est autorisee
"""
import re
import subprocess

HOTSPOT_GATEWAY = "172.20.10.1"
NOM = "partage de connexion iPhone (iPhone de Jules 2)"


def passerelle():
    try:
        out = subprocess.run(["route", "-n", "get", "default"], capture_output=True,
                             text=True, timeout=5).stdout
        m = re.search(r"gateway:\s*(\S+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def connexion_ok():
    """(True, message) si on est sur le partage de connexion iPhone, sinon (False, explication)."""
    gw = passerelle()
    if gw == HOTSPOT_GATEWAY:
        return True, f"Connecte au {NOM}"
    if gw is None:
        return False, "Pas de connexion internet"
    return False, (f"Pas sur le {NOM} : connexion normale detectee. "
                   "Active le partage de connexion de l'iPhone et connecte le Mac dessus, puis relance.")


if __name__ == "__main__":
    ok, msg = connexion_ok()
    print(("OK : " if ok else "BLOQUE : ") + msg)
