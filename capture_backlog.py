#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Re-capture les photos des leads deja qualifies (backlog).

Lit resultats/export-backlog.json (handles sans photo), ouvre chaque profil,
capture un screenshot + vignette, rafraichit les stats, et re-ecrit
export-backlog.json AVEC les photos (status "keep" conserve).

Usage : python3 capture_backlog.py
(A lancer QUAND aucun autre run n'utilise Chrome.)
"""
import json
from pathlib import Path

import scout

RES = Path(__file__).parent / "resultats"


def main():
    f = RES / "export-backlog.json"
    if not f.exists():
        print("export-backlog.json introuvable.")
        return
    data = json.loads(f.read_text())
    shots = RES / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    session = scout.get_session()
    print(f"Bridge {session} — {len(data)} leads a re-capturer\n")

    out = []
    for i, l in enumerate(data, 1):
        h = l["handle"]
        scout.open_profile(session, h)
        info = scout.parse_header(scout.header_of(session)) or {}
        png = shots / f"{h}.png"
        scout.screenshot(session, h, png)
        l2 = dict(l)
        if info.get("followers"):
            l2["followers"] = info["followers"]
        if "verified" in info:
            l2["verified"] = info["verified"]
        if info.get("bio"):
            l2["bio"] = info["bio"]
        l2["thumb"] = scout.thumb_datauri(png) if png.exists() else ""
        l2["status"] = "keep"
        out.append(l2)
        got = "photo OK" if l2["thumb"] else "pas de photo"
        print(f"[{i}/{len(data)}] @{h} — {got}")

    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    n = sum(1 for x in out if x["thumb"])
    print(f"\nTermine. {n}/{len(out)} leads avec photo.")
    print(f"  -> importe {f} dans ton Vivier")


if __name__ == "__main__":
    main()
