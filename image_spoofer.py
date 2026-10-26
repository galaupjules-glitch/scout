#!/usr/bin/env python3
"""
Spoofer de photo : même principe que video_spoofer.py (recadrage, couleurs,
bruit, métadonnées...) mais pour une image fixe au lieu d'une vidéo.

Deux modes :
  - leger    : modifications discrètes, rendu quasi identique à l'oeil
  - agressif : modifications plus marquées

Usage :
    python3 image_spoofer.py photo.jpg --mode leger --locale us
"""

import argparse
import random
import re
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np
import piexif
from PIL import Image

from video_spoofer import _matrice_couleur, LOCALES, creation_time_for

# Mêmes caractéristiques que la vidéo (luminosité, contraste, saturation,
# teinte, recadrage/zoom, rotation, grain), sans les réglages propres à la
# vidéo (vitesse, volume, découpe) qui n'ont pas de sens sur une image fixe.
PARAM_SPECS = {
    "leger": {
        "crop_pct":   {"floor": 0.010, "ceil": 0.030, "signed": False},
        "zoom":       {"floor": 0.010, "ceil": 0.035, "signed": False},
        "brightness": {"floor": 0.020, "ceil": 0.050, "signed": True},
        "contrast":   {"floor": 0.020, "ceil": 0.050, "signed": True},
        "saturation": {"floor": 0.030, "ceil": 0.070, "signed": True},
        "hue_deg":    {"floor": 1.5,   "ceil": 4.0,   "signed": True},
        "gamma":      {"floor": 0.01,  "ceil": 0.04,  "signed": True},
        "vignette":   {"floor": 0.03,  "ceil": 0.10,  "signed": False},
        "sharpen":    {"floor": 0.10,  "ceil": 0.35,  "signed": True},
        "rotate_deg": {"floor": 0.3,   "ceil": 1.0,   "signed": True},
        "noise_std":  {"floor": 1.0,   "ceil": 2.5,   "signed": False},
    },
    "agressif": {
        "crop_pct":   {"floor": 0.04, "ceil": 0.10, "signed": False},
        "zoom":       {"floor": 0.04, "ceil": 0.12, "signed": False},
        "brightness": {"floor": 0.06, "ceil": 0.14, "signed": True},
        "contrast":   {"floor": 0.07, "ceil": 0.16, "signed": True},
        "saturation": {"floor": 0.08, "ceil": 0.20, "signed": True},
        "hue_deg":    {"floor": 6.0,  "ceil": 18.0, "signed": True},
        "gamma":      {"floor": 0.05, "ceil": 0.12, "signed": True},
        "vignette":   {"floor": 0.10, "ceil": 0.22, "signed": False},
        "sharpen":    {"floor": 0.30, "ceil": 0.70, "signed": True},
        "rotate_deg": {"floor": 1.2,  "ceil": 3.0,  "signed": True},
        "noise_std":  {"floor": 3.0,  "ceil": 8.0,  "signed": False},
    },
}

NB_CARACTERISTIQUES = len(PARAM_SPECS["leger"])  # 8, bien au-delà du minimum de 7
DISTANCE_MIN_LOT = 0.55

FAKE_MAKES = ["Apple", "Samsung", "Google", "Xiaomi", "OnePlus"]
FAKE_SOFTWARES = ["17.4.1", "14.0", "Photos 9.0", "Lightroom 7.2", "Snapseed 2.21"]


def sample_params(mode: str) -> dict:
    """Tire un jeu de paramètres où chaque caractéristique a une amplitude
    garantie (jamais nulle, jamais négligeable) — voir video_spoofer.py."""
    specs = PARAM_SPECS[mode]
    params = {}
    for name, spec in specs.items():
        mag = random.uniform(spec["floor"], spec["ceil"])
        if spec["signed"]:
            mag *= random.choice([-1, 1])
        params[name] = mag
    return params


def _params_vector(mode: str, params: dict) -> list:
    specs = PARAM_SPECS[mode]
    return [params[name] / spec["ceil"] for name, spec in specs.items()]


def _distance(a: list, b: list) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def batch_params(mode: str, count: int, min_dist: float = DISTANCE_MIN_LOT, max_tries: int = 80) -> list:
    """Génère `count` jeux de paramètres pour qu'aucune variante du lot ne
    ressemble aux autres (même logique que video_spoofer.batch_params)."""
    vecteurs, resultat = [], []
    for _ in range(count):
        meilleur, meilleur_score = None, -1.0
        for _ in range(max_tries):
            candidat = sample_params(mode)
            v = _params_vector(mode, candidat)
            score = min((_distance(v, autre) for autre in vecteurs), default=float("inf"))
            if score >= min_dist:
                meilleur = candidat
                break
            if score > meilleur_score:
                meilleur_score, meilleur = score, candidat
        resultat.append(meilleur)
        vecteurs.append(_params_vector(mode, meilleur))
    return resultat


def _parse_iso6709(loc: str):
    m = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", loc)
    return float(m.group(1)), float(m.group(2))


def _deg_vers_dms(deg: float):
    deg_abs = abs(deg)
    d = int(deg_abs)
    m_float = (deg_abs - d) * 60
    m = int(m_float)
    s = round((m_float - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def build_image_variant(src_path: Path, out_path: Path, mode: str, locale: str = None,
                         params: dict = None, miroir: bool = False):
    """Génère une variante d'image. Si `params` n'est pas fourni, un jeu de
    paramètres est tiré au sort (voir sample_params/batch_params)."""
    if mode not in PARAM_SPECS:
        raise ValueError("mode doit être 'leger' ou 'agressif'")
    if locale is not None and locale not in LOCALES:
        raise ValueError(f"locale doit être parmi {list(LOCALES)}")
    if params is None:
        params = sample_params(mode)

    img = Image.open(src_path)
    img = img.convert("RGB")
    frame = np.array(img)
    h, w = frame.shape[:2]

    # 1) rotation + zoom + recadrage des bords, en une seule passe OpenCV
    rotate_deg = params["rotate_deg"]
    crop_pct = params["crop_pct"] + params["zoom"] + abs(rotate_deg) / 90.0
    crop_w = min(w // 2 - 1, int(w * crop_pct / 2))
    crop_h = min(h // 2 - 1, int(h * crop_pct / 2))

    if abs(rotate_deg) > 0.01:
        rot_matrix = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, 1.0)
        frame = cv2.warpAffine(frame, rot_matrix, (w, h), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)

    if crop_w or crop_h:
        frame = frame[crop_h:h - crop_h, crop_w:w - crop_w]
        frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)

    if miroir:
        frame = frame[:, ::-1]

    # 2) étalonnage couleur (luminosité/contraste/saturation/teinte)
    matrice_couleur = _matrice_couleur(
        params["brightness"], params["contrast"], params["saturation"], params["hue_deg"]
    )
    frame = cv2.transform(frame, matrice_couleur)

    # 2bis) gamma (courbe tonale)
    gamma = max(0.3, 1.0 + params["gamma"])
    inv_gamma = 1.0 / gamma
    lut_gamma = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)], dtype=np.uint8)
    frame = cv2.LUT(frame, lut_gamma)

    # 2ter) netteté / flou (>0 renforce la netteté, <0 flou léger)
    sharpen_val = params["sharpen"]
    if sharpen_val > 0:
        flou = cv2.GaussianBlur(frame, (0, 0), 1.0)
        frame = cv2.addWeighted(frame, 1 + sharpen_val, flou, -sharpen_val, 0)
    elif sharpen_val < 0:
        frame = cv2.GaussianBlur(frame, (0, 0), min(2.5, -sharpen_val * 2))

    # 2quater) vignette (assombrissement progressif des coins)
    vignette_force = params["vignette"]
    if vignette_force > 0:
        yy, xx = np.mgrid[0:h, 0:w]
        rayon = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        vignette_mask = np.clip(1.0 - vignette_force * (rayon ** 2), 1.0 - vignette_force, 1.0)
        frame = (frame.astype(np.float32) * vignette_mask[:, :, None]).astype(np.uint8)

    # 3) grain
    noise_std = params["noise_std"]
    if noise_std > 0:
        bruit = np.empty((h, w, 3), dtype=np.int16)
        cv2.randn(bruit, 0, noise_std)
        frame = cv2.add(frame, bruit, dtype=cv2.CV_8U)

    resultat = Image.fromarray(frame)
    qualite = random.randint(90, 97)
    _enregistrer_avec_exif(resultat, out_path, locale, qualite)


def _enregistrer_avec_exif(img: Image.Image, out_path: Path, locale: str, qualite: int):
    unique_id = uuid.uuid4().hex[:12]
    loc = LOCALES.get(locale)

    appareil = random.choice(loc["appareils"]) if loc and loc.get("appareils") else None

    zeroth = {
        piexif.ImageIFD.Make: (appareil.split()[0] if appareil else random.choice(FAKE_MAKES)).encode(),
        piexif.ImageIFD.Model: appareil.encode() if appareil else b"",
        piexif.ImageIFD.Software: random.choice(FAKE_SOFTWARES).encode(),
    }
    exif_ifd = {
        piexif.ExifIFD.UserComment: ("ASCII\x00\x00\x00" + unique_id).encode(),
    }
    gps_ifd = {}

    if loc:
        lat, lon = _parse_iso6709(loc["location"])
        altitude = random.uniform(5, 180)
        creation = creation_time_for(loc["tz"])
        dt = creation[:19].replace("T", " ")
        exif_ifd[piexif.ExifIFD.DateTimeOriginal] = dt.encode()
        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: "N" if lat >= 0 else "S",
            piexif.GPSIFD.GPSLatitude: _deg_vers_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: "E" if lon >= 0 else "W",
            piexif.GPSIFD.GPSLongitude: _deg_vers_dms(lon),
            piexif.GPSIFD.GPSAltitudeRef: 0,
            piexif.GPSIFD.GPSAltitude: (int(altitude * 100), 100),
        }

    exif_dict = {"0th": zeroth, "Exif": exif_ifd, "GPS": gps_ifd, "1st": {}, "thumbnail": None}
    exif_bytes = piexif.dump(exif_dict)

    img.save(out_path, "JPEG", quality=qualite, exif=exif_bytes)


def main():
    parser = argparse.ArgumentParser(description="Génère des variantes d'une photo.")
    parser.add_argument("input", type=Path, help="Photo source")
    parser.add_argument("--mode", choices=["leger", "agressif"], default="leger")
    parser.add_argument("--locale", choices=list(LOCALES), default=None,
                         help="Pays à utiliser pour localiser les métadonnées EXIF")
    parser.add_argument("--out", type=Path, default=None, help="Fichier de sortie (mode simple)")
    parser.add_argument("--count", type=int, default=1, help="Nombre de variantes à générer")
    parser.add_argument("--outdir", type=Path, default=None, help="Dossier de sortie si --count > 1")
    parser.add_argument("--miroir", action="store_true", help="Inverse la photo horizontalement")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Fichier introuvable : {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.count <= 1:
        out = args.out or args.input.with_name(
            f"{args.input.stem}_{args.mode}_{uuid.uuid4().hex[:6]}.jpg"
        )
        build_image_variant(args.input, out, args.mode, args.locale, miroir=args.miroir)
        print(f"Variante générée : {out}")
    else:
        outdir = args.outdir or args.input.parent / f"{args.input.stem}_variantes"
        outdir.mkdir(parents=True, exist_ok=True)
        lot = batch_params(args.mode, args.count)
        for i, params in enumerate(lot):
            out = outdir / f"{args.input.stem}_{args.mode}_{i+1:02d}_{uuid.uuid4().hex[:6]}.jpg"
            build_image_variant(args.input, out, args.mode, args.locale, params, miroir=args.miroir)
            print(f"[{i+1}/{args.count}] Variante générée : {out}")


if __name__ == "__main__":
    main()
