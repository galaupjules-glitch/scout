#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Petite appli web locale pour utiliser video_spoofer.py sans ligne de commande.

Lancement :
    python3 video_spoofer_web.py
    -> ouvre http://127.0.0.1:8766/
"""

import uuid
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, flash, abort,
)

from video_spoofer import build_variant, RANGES

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "spoofer_uploads"
OUTPUT_DIR = BASE_DIR / "spoofer_sorties"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_COUNT = 5

app = Flask(__name__)
app.secret_key = "scout-video-spoofer-local"
app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024  # 600 Mo


def safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/spoof", methods=["POST"])
def spoof():
    fichier = request.files.get("video")
    mode = request.form.get("mode", "leger")
    try:
        count = int(request.form.get("count", 1))
    except ValueError:
        count = 1
    count = max(1, min(MAX_COUNT, count))

    if mode not in RANGES:
        flash("Mode invalide.")
        return redirect(url_for("index"))

    if not fichier or fichier.filename == "":
        flash("Aucune vidéo sélectionnée.")
        return redirect(url_for("index"))

    ext = safe_ext(fichier.filename)
    if ext not in ALLOWED_EXT:
        flash(f"Format non supporté ({ext}). Formats acceptés : {', '.join(sorted(ALLOWED_EXT))}")
        return redirect(url_for("index"))

    job_id = uuid.uuid4().hex[:10]
    src_path = UPLOAD_DIR / f"{job_id}{ext}"
    fichier.save(src_path)

    resultats = []
    erreurs = []
    for i in range(count):
        out_name = f"{job_id}_{mode}_{i+1:02d}{ext}"
        out_path = OUTPUT_DIR / out_name
        try:
            build_variant(src_path, out_path, mode)
            resultats.append(out_name)
        except Exception as exc:  # noqa: BLE001 - on veut afficher l'erreur à l'utilisateur
            erreurs.append(str(exc))

    # on n'a plus besoin de l'original une fois les variantes générées
    src_path.unlink(missing_ok=True)

    return render_template(
        "resultats.html",
        mode=mode,
        resultats=resultats,
        erreurs=erreurs,
        nom_original=fichier.filename,
    )


@app.route("/fichier/<path:nom>")
def telecharger(nom):
    chemin = (OUTPUT_DIR / nom).resolve()
    if OUTPUT_DIR.resolve() not in chemin.parents or not chemin.exists():
        abort(404)
    return send_from_directory(OUTPUT_DIR, nom, as_attachment=True)


@app.route("/apercu/<path:nom>")
def apercu(nom):
    chemin = (OUTPUT_DIR / nom).resolve()
    if OUTPUT_DIR.resolve() not in chemin.parents or not chemin.exists():
        abort(404)
    return send_from_directory(OUTPUT_DIR, nom, as_attachment=False)


if __name__ == "__main__":
    print("Spoofer vidéo sur http://127.0.0.1:8766/")
    app.run(host="127.0.0.1", port=8766, debug=False)
