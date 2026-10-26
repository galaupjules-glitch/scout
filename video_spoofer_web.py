#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Petite appli web locale pour utiliser video_spoofer.py sans ligne de commande.

Lancement :
    python3 video_spoofer_web.py
    -> ouvre http://127.0.0.1:8766/
"""

import io
import json
import os
import secrets
import sys
import threading
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for,
    send_from_directory, send_file, abort,
)

from video_spoofer import build_variant, batch_params, PARAM_SPECS, LOCALES
from image_spoofer import (
    build_image_variant, batch_params as batch_params_photo, PARAM_SPECS as PARAM_SPECS_PHOTO,
)

# Mode application locale (vexa_app.py, ou .app empaquetée) : pas de code
# d'accès, 127.0.0.1 uniquement — à détecter avant tout le reste, car ça
# change où les fichiers sont stockés (voir BASE_DIR plus bas).
MODE_APP_LOCALE = os.environ.get("VEXA_APP_LOCAL") == "1"
EST_EMPAQUETE = getattr(sys, "frozen", False)  # True une fois packagé avec PyInstaller
# Identifiant de build (date/heure de compilation, écrit par
# build-vexa-native.sh dans Resources/build_info.txt) : affiché en petit
# dans l'interface pour vérifier à coup sûr qu'on teste la bonne version,
# plutôt qu'une ancienne copie de l'app restée sur la machine.
INFOS_BUILD = ""
if EST_EMPAQUETE:
    _chemin_build_info = Path(sys.executable).resolve().parent.parent / "build_info.txt"
    if _chemin_build_info.exists():
        INFOS_BUILD = _chemin_build_info.read_text(encoding="utf-8").strip()

# une variante = tout un coeur CPU occupé (ffmpeg + calcul image par image) ;
# on en lance plusieurs en parallèle (processus séparés) pour un lot de
# plusieurs variantes, en gardant un coeur libre pour le serveur web.
WORKERS_PARALLELES = max(1, (os.cpu_count() or 2) - 1)

if MODE_APP_LOCALE or EST_EMPAQUETE:
    # dans une .app empaquetée, le dossier de l'exécutable est en lecture
    # seule : les fichiers générés vont dans le dossier Documents de la
    # personne à la place.
    BASE_DIR = Path.home() / "Documents" / "Vexa"
else:
    BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "spoofer_uploads"
OUTPUT_DIR = BASE_DIR / "spoofer_sorties"
BASE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXT_VIDEO = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm",
    ".3gp", ".3g2", ".wmv", ".flv", ".mpeg", ".mpg", ".ts", ".m2ts",
}
ALLOWED_EXT_PHOTO = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_COUNT = 20
# traitement par lots : plusieurs fichiers source d'un coup (chacun généra
# son propre lot de `count` variantes, indépendamment des autres)
MAX_FICHIERS_LOT = 10

# Code d'accès + clé de session : seulement pour le mode "serveur partagé"
# (video_spoofer_web.py lancé tel quel + tunnel). En mode application locale,
# rien n'est partagé sur le réseau donc pas besoin de code ni de fichier.
if MODE_APP_LOCALE:
    CODE_ACCES = None
    flask_secret = secrets.token_hex(32)
else:
    SECRET_FILE = BASE_DIR / ".vexa_secret.json"

    def _charger_secrets():
        data = json.loads(SECRET_FILE.read_text()) if SECRET_FILE.exists() else {}
        changed = False
        if "code" not in data:
            data["code"] = secrets.token_hex(3).upper()  # ex: A1B2C3
            changed = True
        if "flask_secret" not in data:
            data["flask_secret"] = secrets.token_hex(32)
            changed = True
        if changed:
            SECRET_FILE.write_text(json.dumps(data, indent=2))
            SECRET_FILE.chmod(0o600)
        return data

    _secrets = _charger_secrets()
    CODE_ACCES = _secrets["code"]
    flask_secret = _secrets["flask_secret"]

app = Flask(__name__)
app.secret_key = flask_secret
app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024  # 600 Mo
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

ROUTES_LIBRES = {"connexion", "static"}


@app.before_request
def verifier_acces():
    if MODE_APP_LOCALE:
        return
    if request.endpoint in ROUTES_LIBRES or request.endpoint is None:
        return
    if session.get("autorise"):
        return
    if request.path.startswith("/api/"):
        return jsonify(erreur="Non autorisé, connecte-toi d'abord."), 401
    return redirect(url_for("connexion", suivant=request.path))


@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    erreur = None
    suivant = request.values.get("suivant") or url_for("index")
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        if code == CODE_ACCES:
            session.permanent = True
            session["autorise"] = True
            return redirect(suivant)
        erreur = "Code invalide."
    return render_template("connexion.html", erreur=erreur, suivant=suivant)

# Suivi des traitements en cours (local, en mémoire : usage mono-utilisateur)
JOBS = {}
JOBS_LOCK = threading.Lock()


def safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _generer_une_variante(out_path: Path, src_path: Path, mode: str, locale: str, params: dict,
                           threads_encodage: int, miroir: bool) -> str:
    try:
        build_variant(src_path, out_path, mode, locale, params, threads_encodage=threads_encodage, miroir=miroir)
    except Exception:
        # traceback complet dans les logs serveur (console / spoofer_web.log)
        # pour pouvoir diagnostiquer une vidéo précise qui a échoué, en plus
        # du message résumé renvoyé au navigateur
        print(f"--- échec génération {out_path.name} (source : {src_path.name}) ---")
        traceback.print_exc()
        raise
    return out_path.name


def _generer_une_variante_photo(out_path: Path, src_path: Path, mode: str, locale: str, params: dict,
                                 miroir: bool) -> str:
    try:
        build_image_variant(src_path, out_path, mode, locale, params, miroir=miroir)
    except Exception:
        print(f"--- échec génération {out_path.name} (source : {src_path.name}) ---")
        traceback.print_exc()
        raise
    return out_path.name


def _traiter_job(job_id: str, src_path: Path, mode: str, locale: str, count: int, ext: str,
                  miroir: bool, type_media: str):
    if type_media == "photo":
        lot = batch_params_photo(mode, count)
    else:
        lot = batch_params(mode, count)

    taches = []
    for i, params in enumerate(lot):
        out_name = f"{job_id}_{mode}_{i+1:02d}{ext}"
        taches.append(OUTPUT_DIR / out_name)

    # plusieurs variantes = plusieurs threads en parallèle (chaque variante
    # prend ~1 coeur plein pour le calcul image par image + ffmpeg ; OpenCV,
    # numpy et le sous-processus ffmpeg libèrent le GIL pendant le gros du
    # calcul, donc ça se parallélise vraiment), au lieu de les traiter une
    # par une : le gain est quasi proportionnel au nombre de coeurs dispo,
    # sans rien changer aux paramètres générés
    nb_workers = min(len(taches), WORKERS_PARALLELES)

    if type_media == "photo":
        with ThreadPoolExecutor(max_workers=nb_workers) as executor:
            futures = {
                executor.submit(_generer_une_variante_photo, out_path, src_path, mode, locale, params, miroir): out_path
                for out_path, params in zip(taches, lot)
            }
            for future in as_completed(futures):
                try:
                    nom = future.result()
                    with JOBS_LOCK:
                        JOBS[job_id]["resultats"].append(nom)
                except Exception as exc:  # noqa: BLE001 - affichée à l'utilisateur
                    with JOBS_LOCK:
                        JOBS[job_id]["erreurs"].append(f"{type(exc).__name__}: {exc}")
                with JOBS_LOCK:
                    JOBS[job_id]["done"] += 1
    else:
        # chaque ffmpeg ne doit pas réclamer tous les coeurs quand plusieurs
        # tournent déjà en parallèle, sinon elles se marchent dessus au lieu
        # d'aller plus vite
        threads_par_tache = max(1, (os.cpu_count() or 2) // nb_workers)
        with ThreadPoolExecutor(max_workers=nb_workers) as executor:
            futures = {
                executor.submit(_generer_une_variante, out_path, src_path, mode, locale, params, threads_par_tache, miroir): out_path
                for out_path, params in zip(taches, lot)
            }
            for future in as_completed(futures):
                try:
                    nom = future.result()
                    with JOBS_LOCK:
                        JOBS[job_id]["resultats"].append(nom)
                except Exception as exc:  # noqa: BLE001 - affichée à l'utilisateur
                    with JOBS_LOCK:
                        JOBS[job_id]["erreurs"].append(f"{type(exc).__name__}: {exc}")
                with JOBS_LOCK:
                    JOBS[job_id]["done"] += 1

    src_path.unlink(missing_ok=True)
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "termine"


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", locales=LOCALES, max_count=MAX_COUNT, infos_build=INFOS_BUILD)


@app.route("/api/spoof", methods=["POST"])
def api_spoof():
    type_media = request.form.get("type_media", "video")
    if type_media not in ("video", "photo"):
        type_media = "video"

    # traitement par lots : "video" peut contenir plusieurs fichiers d'un
    # coup (sélection multiple ou glisser-déposer groupé) — chacun devient
    # son propre job indépendant, avec les mêmes réglages (mode/locale/...).
    fichiers = request.files.getlist("video")
    mode = request.form.get("mode", "leger")
    locale = request.form.get("locale") or None
    miroir = (request.form.get("miroir") or "").lower() in ("1", "true", "on", "oui")
    try:
        count = int(request.form.get("count", 1))
    except ValueError:
        count = 1
    count = max(1, min(MAX_COUNT, count))

    specs = PARAM_SPECS_PHOTO if type_media == "photo" else PARAM_SPECS
    extensions_acceptees = ALLOWED_EXT_PHOTO if type_media == "photo" else ALLOWED_EXT_VIDEO

    if mode not in specs:
        print(f"[400] mode invalide reçu : {mode!r}")
        return jsonify(erreur="Mode invalide."), 400
    if locale is not None and locale not in LOCALES:
        print(f"[400] locale invalide reçue : {locale!r}")
        return jsonify(erreur="Pays invalide."), 400

    fichiers = [f for f in fichiers if f and f.filename]
    if not fichiers:
        print(f"[400] aucun fichier dans la requête (type={type_media}, champs reçus : "
              f"{list(request.form.keys())}, fichiers reçus : {list(request.files.keys())})")
        return jsonify(erreur="Aucun fichier sélectionné."), 400
    if len(fichiers) > MAX_FICHIERS_LOT:
        return jsonify(erreur=f"Trop de fichiers à la fois (max {MAX_FICHIERS_LOT})."), 400

    for fichier in fichiers:
        ext = safe_ext(fichier.filename)
        if ext not in extensions_acceptees:
            print(f"[400] format non supporté : nom={fichier.filename!r} ext={ext!r} type={type_media}")
            return jsonify(erreur=f"Format non supporté ({fichier.filename} : {ext}). Formats acceptés : "
                                   f"{', '.join(sorted(extensions_acceptees))}"), 400

    jobs_crees = []
    for fichier in fichiers:
        ext = safe_ext(fichier.filename)
        job_id = uuid.uuid4().hex[:10]
        src_path = UPLOAD_DIR / f"{job_id}{ext}"
        fichier.save(src_path)

        # pour une photo, le format de sortie est toujours .jpg (métadonnées EXIF)
        ext_sortie = ".jpg" if type_media == "photo" else ext

        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "traitement",
                "total": count,
                "done": 0,
                "mode": mode,
                "type_media": type_media,
                "locale_code": locale,
                "locale_label": LOCALES[locale]["label"] if locale else None,
                "nom_original": fichier.filename,
                "resultats": [],
                "erreurs": [],
            }

        threading.Thread(
            target=_traiter_job,
            args=(job_id, src_path, mode, locale, count, ext_sortie, miroir, type_media),
            daemon=True,
        ).start()

        jobs_crees.append({
            "job_id": job_id,
            "total": count,
            "type_media": type_media,
            "locale_code": locale,
            "locale_label": LOCALES[locale]["label"] if locale else None,
            "nom_original": fichier.filename,
        })

    # compat : les champs job_id/total au premier niveau restent présents
    # pour un envoi à un seul fichier (ancien format, toujours utilisé par
    # le pont natif Mac qui envoie un fichier à la fois)
    reponse = {"jobs": jobs_crees}
    reponse.update(jobs_crees[0])
    return jsonify(**reponse)


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
            type_media=job.get("type_media", "video"),
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


@app.route("/telecharger-lot")
def telecharger_lot():
    job_ids = [j for j in (request.args.get("jobs") or "").split(",") if j]
    resultats = []
    with JOBS_LOCK:
        for job_id in job_ids:
            job = JOBS.get(job_id)
            if job:
                resultats.extend(job["resultats"])

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
        download_name=f"vexa_lot_{uuid.uuid4().hex[:8]}.zip",
    )


@app.route("/supprimer/<path:nom>", methods=["POST"])
def supprimer(nom):
    chemin = (OUTPUT_DIR / nom).resolve()
    if OUTPUT_DIR.resolve() not in chemin.parents:
        abort(404)
    chemin.unlink(missing_ok=True)
    with JOBS_LOCK:
        for job in JOBS.values():
            if nom in job["resultats"]:
                job["resultats"].remove(nom)
    return jsonify(ok=True)


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
    print(f"Code d'accès (à utiliser sur chaque PC) : {CODE_ACCES}")
    app.run(host="0.0.0.0", port=8766, debug=False, threaded=True)
