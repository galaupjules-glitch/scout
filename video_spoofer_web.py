#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Petite appli web locale pour utiliser video_spoofer.py sans ligne de commande.

Lancement :
    python3 video_spoofer_web.py
    -> ouvre http://127.0.0.1:8766/
"""

import io
import threading
import uuid
import zipfile
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, send_file, abort,
)

from video_spoofer import build_variant, batch_params, PARAM_SPECS, LOCALES

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "spoofer_uploads"
OUTPUT_DIR = BASE_DIR / "spoofer_sorties"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_COUNT = 20

app = Flask(__name__)
app.secret_key = "scout-video-spoofer-local"
app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024  # 600 Mo

# Suivi des traitements en cours (local, en mémoire : usage mono-utilisateur)
JOBS = {}
JOBS_LOCK = threading.Lock()


def safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _traiter_job(job_id: str, src_path: Path, mode: str, locale: str, count: int, ext: str):
    lot = batch_params(mode, count)
    for i, params in enumerate(lot):
        out_name = f"{job_id}_{mode}_{i+1:02d}{ext}"
        out_path = OUTPUT_DIR / out_name
        try:
            build_variant(src_path, out_path, mode, locale, params)
            with JOBS_LOCK:
                JOBS[job_id]["resultats"].append(out_name)
        except Exception as exc:  # noqa: BLE001 - affichée à l'utilisateur
            with JOBS_LOCK:
                JOBS[job_id]["erreurs"].append(str(exc))
        with JOBS_LOCK:
            JOBS[job_id]["done"] += 1

    src_path.unlink(missing_ok=True)
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "termine"


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", locales=LOCALES, max_count=MAX_COUNT)


@app.route("/api/spoof", methods=["POST"])
def api_spoof():
    fichier = request.files.get("video")
    mode = request.form.get("mode", "leger")
    locale = request.form.get("locale") or None
    try:
        count = int(request.form.get("count", 1))
    except ValueError:
        count = 1
    count = max(1, min(MAX_COUNT, count))

    if mode not in PARAM_SPECS:
        return jsonify(erreur="Mode invalide."), 400
    if locale is not None and locale not in LOCALES:
        return jsonify(erreur="Pays invalide."), 400
    if not fichier or fichier.filename == "":
        return jsonify(erreur="Aucune vidéo sélectionnée."), 400

    ext = safe_ext(fichier.filename)
    if ext not in ALLOWED_EXT:
        return jsonify(erreur=f"Format non supporté ({ext}). Formats acceptés : "
                               f"{', '.join(sorted(ALLOWED_EXT))}"), 400

    job_id = uuid.uuid4().hex[:10]
    src_path = UPLOAD_DIR / f"{job_id}{ext}"
    fichier.save(src_path)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "traitement",
            "total": count,
            "done": 0,
            "mode": mode,
            "locale_code": locale,
            "locale_label": LOCALES[locale]["label"] if locale else None,
            "nom_original": fichier.filename,
            "resultats": [],
            "erreurs": [],
        }

    threading.Thread(
        target=_traiter_job,
        args=(job_id, src_path, mode, locale, count, ext),
        daemon=True,
    ).start()

    return jsonify(
        job_id=job_id,
        total=count,
        locale_code=locale,
        locale_label=LOCALES[locale]["label"] if locale else None,
    )


@app.route("/api/etat/<job_id>")
def api_etat(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(erreur="job inconnu"), 404
        return jsonify(
            status=job["status"],
            total=job["total"],
            done=job["done"],
            resultats=list(job["resultats"]),
            erreurs=list(job["erreurs"]),
            locale_code=job["locale_code"],
            locale_label=job["locale_label"],
        )


@app.route("/resultats/<job_id>")
def resultats(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404)
    return render_template(
        "resultats.html",
        mode=job["mode"],
        locale_label=job["locale_label"],
        resultats=job["resultats"],
        erreurs=job["erreurs"],
        nom_original=job["nom_original"],
    )


@app.route("/telecharger-tout/<job_id>")
def telecharger_tout(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        resultats = list(job["resultats"]) if job else []

    if not resultats:
        abort(404)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nom in resultats:
            chemin = OUTPUT_DIR / nom
            if chemin.exists():
                zf.write(chemin, arcname=nom)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"vexa_{job_id}.zip",
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
    print("Vexa sur http://127.0.0.1:8766/")
    app.run(host="127.0.0.1", port=8766, debug=False, threaded=True)
