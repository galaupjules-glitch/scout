#!/usr/bin/env python3
"""
Spoofer de vidéo : génère une variante d'une vidéo (recadrage, couleurs,
vitesse, bruit, métadonnées...) afin qu'elle ne soit plus un fichier
identique (même hash / même empreinte) à l'original.

Deux modes :
  - leger    : modifications discrètes, rendu quasi identique à l'oeil
  - agressif : modifications plus marquées

Usage :
    python3 video_spoofer.py input.mp4 --mode leger
    python3 video_spoofer.py input.mp4 --mode agressif --out variante.mp4
    python3 video_spoofer.py input.mp4 --mode leger --count 5   # 5 variantes
"""

import argparse
import os
import random
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from moviepy import VideoFileClip
import moviepy.video.fx as vfx
import moviepy.audio.fx as afx
from imageio_ffmpeg import get_ffmpeg_exe

# ffmpeg encode en multi-thread. Preset "slow" : nettement meilleure qualité
# par bit que "veryfast" (le gros du temps de traitement reste de toute
# façon le calcul de chaque image, voir build_variant plus bas).
PRESET_ENCODAGE = "slow"
THREADS_ENCODAGE = max(1, os.cpu_count() or 1)

FFMPEG = get_ffmpeg_exe()

# Chaque caractéristique a un plancher (floor) > 0 : elle est donc TOUJOURS
# appliquée avec une amplitude perceptible, jamais à ~0. "signed" = la valeur
# peut être positive ou négative (tirée au sort). Ça garantit qu'une variante
# modifie toujours au moins ces 12 caractéristiques (bien plus que les 7
# minimum demandées), et jamais de façon négligeable.
PARAM_SPECS = {
    "leger": {
        "crop_pct":     {"floor": 0.008, "ceil": 0.025, "signed": False},  # recadrage des bords
        "zoom":         {"floor": 0.008, "ceil": 0.030, "signed": False},  # zoom (1 + valeur)
        "brightness":   {"floor": 0.015, "ceil": 0.040, "signed": True},   # luminosité
        "contrast":     {"floor": 0.020, "ceil": 0.050, "signed": True},   # contraste
        "saturation":   {"floor": 0.020, "ceil": 0.060, "signed": True},   # saturation (≈ opacité des couleurs)
        "hue_deg":      {"floor": 1.0,   "ceil": 3.0,   "signed": True},   # teinte
        "gamma":        {"floor": 0.01,  "ceil": 0.04,  "signed": True},   # gamma (courbe tonale)
        "vignette":     {"floor": 0.03,  "ceil": 0.10,  "signed": False},  # assombrissement des coins
        "sharpen":      {"floor": 0.10,  "ceil": 0.35,  "signed": True},   # <0 flou léger, >0 netteté
        "speed_delta":  {"floor": 0.008, "ceil": 0.020, "signed": True},   # vitesse (1 + valeur)
        "rotate_deg":   {"floor": 0.3,   "ceil": 0.8,   "signed": True},   # rotation
        "noise_std":    {"floor": 0.8,   "ceil": 2.0,   "signed": False},  # grain/bruit
        "volume_delta": {"floor": 0.01,  "ceil": 0.030, "signed": True},   # volume audio (1 + valeur)
        "eq_bass":      {"floor": 0.02,  "ceil": 0.06,  "signed": True},   # égalisation graves
        "eq_treble":    {"floor": 0.02,  "ceil": 0.06,  "signed": True},   # égalisation aigus
        "fade_s":       {"floor": 0.03,  "ceil": 0.12,  "signed": False},  # fondu audio entrée/sortie (s)
        "trim_start":   {"floor": 0.03,  "ceil": 0.15,  "signed": False},  # coupe au début (s)
        "trim_end":     {"floor": 0.03,  "ceil": 0.15,  "signed": False},  # coupe à la fin (s)
    },
    "agressif": {
        "crop_pct":     {"floor": 0.04, "ceil": 0.09, "signed": False},
        "zoom":         {"floor": 0.04, "ceil": 0.12, "signed": False},
        "brightness":   {"floor": 0.05, "ceil": 0.12, "signed": True},
        "contrast":     {"floor": 0.06, "ceil": 0.15, "signed": True},
        "saturation":   {"floor": 0.07, "ceil": 0.18, "signed": True},
        "hue_deg":      {"floor": 5.0,  "ceil": 15.0, "signed": True},
        "gamma":        {"floor": 0.05, "ceil": 0.12, "signed": True},
        "vignette":     {"floor": 0.10, "ceil": 0.22, "signed": False},
        "sharpen":      {"floor": 0.30, "ceil": 0.70, "signed": True},
        "speed_delta":  {"floor": 0.04, "ceil": 0.10, "signed": True},
        "rotate_deg":   {"floor": 1.0,  "ceil": 2.5,  "signed": True},
        "noise_std":    {"floor": 3.0,  "ceil": 9.0,  "signed": False},
        "volume_delta": {"floor": 0.05, "ceil": 0.15, "signed": True},
        "eq_bass":      {"floor": 0.06, "ceil": 0.15, "signed": True},
        "eq_treble":    {"floor": 0.06, "ceil": 0.15, "signed": True},
        "fade_s":       {"floor": 0.1,  "ceil": 0.3,  "signed": False},
        "trim_start":   {"floor": 0.1,  "ceil": 0.5,  "signed": False},
        "trim_end":     {"floor": 0.1,  "ceil": 0.5,  "signed": False},
    },
}

# Le miroir horizontal n'est plus tiré au sort automatiquement : ça inverse
# aussi tout texte présent dans la vidéo (sous-titres, logos, texte à
# l'écran...) et le rend illisible à l'envers. C'est maintenant une option
# explicite (voir build_variant(miroir=...) / le réglage "Inverser" côté web).
MIRROR_CHANCE = {"leger": 0.0, "agressif": 0.0}

# nombre de caractéristiques toujours modifiées (hors miroir, bonus en agressif)
NB_CARACTERISTIQUES = len(PARAM_SPECS["leger"])

# distance minimale (dans l'espace normalisé des paramètres) exigée entre
# deux variantes d'un même lot, pour qu'aucune ne se ressemble trop
DISTANCE_MIN_LOT = 0.55


def sample_params(mode: str) -> dict:
    """Tire un jeu de paramètres où chaque caractéristique a une amplitude
    garantie (jamais nulle, jamais négligeable)."""
    specs = PARAM_SPECS[mode]
    params = {}
    for name, spec in specs.items():
        mag = random.uniform(spec["floor"], spec["ceil"])
        if spec["signed"]:
            mag *= random.choice([-1, 1])
        params[name] = mag
    params["mirror"] = random.random() < MIRROR_CHANCE[mode]
    return params


def _params_vector(mode: str, params: dict) -> list:
    specs = PARAM_SPECS[mode]
    vec = [params[name] / spec["ceil"] for name, spec in specs.items()]
    vec.append(1.0 if params["mirror"] else 0.0)
    return vec


def _distance(a: list, b: list) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def batch_params(mode: str, count: int, min_dist: float = DISTANCE_MIN_LOT, max_tries: int = 80) -> list:
    """Génère `count` jeux de paramètres pour qu'aucune variante du lot ne
    ressemble aux autres (distance minimale imposée entre chaque paire)."""
    vecteurs = []
    resultat = []
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

# Localisation des métadonnées : langue, position (ISO 6709) et fuseau
# horaire d'une grande ville du pays visé, pour que le fichier porte une
# empreinte cohérente avec ce pays plutôt qu'avec le lieu réel de tournage.
LOCALES = {
    "fr": {
        "label": "France",
        "language": "fra",
        "location": "+48.8566+002.3522/",
        "tz": "+01:00",
        "appareils": ["iPhone 15 Pro", "iPhone 14", "Samsung Galaxy S23"],
    },
    "us": {
        "label": "États-Unis",
        "language": "eng",
        "location": "+40.7128-074.0060/",
        "tz": "-05:00",
        "appareils": ["iPhone 15 Pro Max", "iPhone 13", "Google Pixel 8"],
    },
    "ca": {
        "label": "Canada",
        "language": "eng",
        "location": "+45.5017-073.5673/",
        "tz": "-05:00",
        "appareils": ["iPhone 14 Pro", "Samsung Galaxy S22"],
    },
    "au": {
        "label": "Australie",
        "language": "eng",
        "location": "-33.8688+151.2093/",
        "tz": "+11:00",
        "appareils": ["iPhone 13 Pro", "Google Pixel 7"],
    },
    "gb": {
        "label": "Royaume-Uni",
        "language": "eng",
        "location": "+51.5074-000.1278/",
        "tz": "+00:00",
        "appareils": ["iPhone 15", "Samsung Galaxy S21"],
    },
    "de": {
        "label": "Allemagne",
        "language": "deu",
        "location": "+52.5200+013.4050/",
        "tz": "+01:00",
        "appareils": ["iPhone 14 Pro Max", "Samsung Galaxy S23 Ultra"],
    },
    "es": {
        "label": "Espagne",
        "language": "spa",
        "location": "+40.4168-003.7038/",
        "tz": "+01:00",
        "appareils": ["iPhone 13", "Xiaomi 13"],
    },
    "it": {
        "label": "Italie",
        "language": "ita",
        "location": "+41.9028+012.4964/",
        "tz": "+01:00",
        "appareils": ["iPhone 15", "Samsung Galaxy A54"],
    },
    "br": {
        "label": "Brésil",
        "language": "por",
        "location": "-23.5505-046.6333/",
        "tz": "-03:00",
        "appareils": ["Samsung Galaxy S23", "iPhone 12"],
    },
    "mx": {
        "label": "Mexique",
        "language": "spa",
        "location": "+19.4326-099.1332/",
        "tz": "-06:00",
        "appareils": ["iPhone 14", "Motorola Edge 40"],
    },
    "jp": {
        "label": "Japon",
        "language": "jpn",
        "location": "+35.6762+139.6503/",
        "tz": "+09:00",
        "appareils": ["iPhone 15 Pro", "Sony Xperia 1 V"],
    },
}

# logiciels de montage réalistes ajoutés aux métadonnées, en plus de
# l'encodeur ffmpeg/Lavf — rend l'empreinte du fichier plus plausible (un
# vrai smartphone ne mentionne jamais "ffmpeg" dans ses métadonnées).
LOGICIELS_PLAUSIBLES = ["CapCut", "InShot", "VN Editor", "Adobe Premiere Rush", "iMovie"]


def creation_time_for(tz_offset: str) -> str:
    sign = 1 if tz_offset.startswith("+") else -1
    hh, mm = tz_offset[1:].split(":")
    delta = timedelta(hours=int(hh), minutes=int(mm)) * sign
    local = datetime.now(timezone.utc) + delta
    return local.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset


def _matrice_couleur(brightness: float, contrast: float, saturation: float, hue_deg: float):
    """Combine luminosité/contraste/saturation/teinte en UNE SEULE matrice
    3x3 affine (+ biais), appliquée par un seul produit matriciel par image
    au lieu de 4 passes numpy séparées (bien plus rapide, voir build_variant)."""
    s = saturation
    S = np.array([
        [1 + 2 * s / 3, -s / 3, -s / 3],
        [-s / 3, 1 + 2 * s / 3, -s / 3],
        [-s / 3, -s / 3, 1 + 2 * s / 3],
    ], dtype=np.float64)

    angle = np.deg2rad(hue_deg)
    cos_a, sin_a = np.cos(angle), np.sin(angle)
    H = np.array([
        [0.299 + 0.701 * cos_a + 0.168 * sin_a, 0.587 - 0.587 * cos_a + 0.330 * sin_a, 0.114 - 0.114 * cos_a - 0.497 * sin_a],
        [0.299 - 0.299 * cos_a - 0.328 * sin_a, 0.587 + 0.413 * cos_a + 0.035 * sin_a, 0.114 - 0.114 * cos_a + 0.292 * sin_a],
        [0.299 - 0.300 * cos_a + 1.250 * sin_a, 0.587 - 0.588 * cos_a - 1.050 * sin_a, 0.114 + 0.886 * cos_a - 0.203 * sin_a],
    ], dtype=np.float64)

    k = 1.0 + contrast
    matrice = (H @ S) * k
    biais = brightness * 255.0 * (1.0 + contrast) - 128.0 * contrast

    # matrice 3x4 (colonne de biais incluse) pour cv2.transform : un seul
    # appel OpenCV saturant par image, bien plus rapide qu'un produit
    # matriciel numpy + clip manuel à haute résolution (4K...)
    matrice_biais = np.zeros((3, 4), dtype=np.float32)
    matrice_biais[:, :3] = matrice
    matrice_biais[:, 3] = biais
    return matrice_biais


def build_variant(src_path: Path, out_path: Path, mode: str, locale: str = None, params: dict = None,
                   threads_encodage: int = None, miroir: bool = False):
    """Génère une variante. Si `params` n'est pas fourni, un jeu de
    paramètres est tiré au sort (voir sample_params/batch_params pour
    générer plusieurs variantes garanties différentes les unes des autres).

    `threads_encodage` : nombre de threads ffmpeg à utiliser pour CETTE
    vidéo. Par défaut tous les coeurs (THREADS_ENCODAGE) ; à réduire quand
    plusieurs variantes tournent en parallèle (sinon chaque ffmpeg réclame
    tous les coeurs et elles se marchent dessus au lieu d'aller plus vite).

    `miroir` : inverse la vidéo horizontalement (effet miroir). Option
    explicite seulement — jamais tirée au sort, car ça inverserait aussi
    tout texte présent dans la vidéo."""
    if mode not in PARAM_SPECS:
        raise ValueError("mode doit être 'leger' ou 'agressif'")
    if locale is not None and locale not in LOCALES:
        raise ValueError(f"locale doit être parmi {list(LOCALES)}")
    if params is None:
        params = sample_params(mode)

    clip = VideoFileClip(str(src_path))

    # 1) découpe légère au début/à la fin — toujours bornée à la durée
    #    réelle de la vidéo (sans ça, une vidéo courte peut demander une
    #    découpe plus longue qu'elle-même et faire planter tout le job)
    duree = clip.duration
    trim_start = min(params["trim_start"], max(0.0, duree - 0.3))
    marge_fin = min(params["trim_end"], max(0.0, duree - trim_start - 0.3))
    end_t = duree - marge_fin
    clip = clip.subclipped(trim_start, end_t)

    w, h = clip.size

    # 2) rotation + zoom + recadrage des bords : regroupés dans une seule
    #    passe OpenCV par image (le rotate/crop/resize "effects" de moviepy
    #    sont ~50 à 90x plus lents que leur équivalent OpenCV à cette résolution)
    rotate_deg = params["rotate_deg"]
    crop_pct = params["crop_pct"] + params["zoom"] + abs(rotate_deg) / 90.0
    crop_w = min(w // 2 - 1, int(w * crop_pct / 2))
    crop_h = min(h // 2 - 1, int(h * crop_pct / 2))

    rot_matrix = None
    if abs(rotate_deg) > 0.01:
        rot_matrix = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, 1.0)

    mirror = params["mirror"] or miroir

    # 3) étalonnage couleur (luminosité/contraste/saturation/teinte) : une
    #    seule matrice précalculée, appliquée par un seul cv2.transform
    #    (saturant nativement, pas besoin de clip manuel ensuite)
    matrice_couleur = _matrice_couleur(
        params["brightness"], params["contrast"], params["saturation"], params["hue_deg"]
    )
    noise_std = params["noise_std"]
    bruit_buffer = np.empty((h, w, 3), dtype=np.int16)

    # gamma (courbe tonale) : LUT précalculée une seule fois, appliquée par
    # cv2.LUT (table de correspondance, le plus rapide pour ce genre de
    # transformation non-linéaire sur 8 bits).
    gamma = max(0.3, 1.0 + params["gamma"])
    inv_gamma = 1.0 / gamma
    lut_gamma = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)], dtype=np.uint8)

    # vignette (assombrissement progressif des coins) : masque radial
    # précalculé une seule fois à la taille de l'image, appliqué par simple
    # multiplication par image.
    vignette_force = params["vignette"]
    vignette_mask = None
    if vignette_force > 0:
        yy, xx = np.mgrid[0:h, 0:w]
        rayon = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        vignette_mask = np.clip(1.0 - vignette_force * (rayon ** 2), 1.0 - vignette_force, 1.0)
        vignette_mask = vignette_mask[:, :, None].astype(np.float32)

    # netteté / flou : valeur positive = renforcement de netteté (unsharp
    # mask), négative = flou léger (gaussien) — jamais les deux à la fois.
    sharpen_val = params["sharpen"]

    def _frame_func(get_frame, t):
        frame = get_frame(t)

        if rot_matrix is not None:
            frame = cv2.warpAffine(frame, rot_matrix, (w, h), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE)
        if crop_w or crop_h:
            frame = frame[crop_h:h - crop_h, crop_w:w - crop_w]
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
        if mirror:
            frame = frame[:, ::-1]

        frame = cv2.transform(frame, matrice_couleur)
        frame = cv2.LUT(frame, lut_gamma)

        if sharpen_val > 0:
            flou = cv2.GaussianBlur(frame, (0, 0), 1.0)
            frame = cv2.addWeighted(frame, 1 + sharpen_val, flou, -sharpen_val, 0)
        elif sharpen_val < 0:
            frame = cv2.GaussianBlur(frame, (0, 0), min(2.5, -sharpen_val * 2))

        if vignette_mask is not None:
            frame = (frame.astype(np.float32) * vignette_mask).astype(np.uint8)

        if noise_std > 0:
            # cv2.randn + cv2.add saturant : nettement plus rapide qu'un
            # produit matriciel numpy + clip manuel, surtout en haute
            # résolution (vidéos 4K de smartphone)
            cv2.randn(bruit_buffer, 0, noise_std)
            frame = cv2.add(frame, bruit_buffer, dtype=cv2.CV_8U)

        return frame

    clip = clip.transform(_frame_func, apply_to="video")

    # 4) vitesse de lecture (image + son, restent synchronisés)
    speed = 1.0 + params["speed_delta"]
    if abs(speed - 1.0) > 1e-3:
        clip = clip.with_effects([vfx.MultiplySpeed(speed)])

    # 6) volume audio + fondu d'entrée/sortie (l'égalisation graves/aigus
    # est appliquée plus loin, directement en sortie ffmpeg — voir
    # ffmpeg_params ci-dessous)
    if clip.audio is not None:
        volume = 1.0 + params["volume_delta"]
        fade_s = min(params["fade_s"], max(0.01, clip.duration / 4))
        clip = clip.with_effects([
            afx.MultiplyVolume(volume),
            afx.AudioFadeIn(fade_s),
            afx.AudioFadeOut(fade_s),
        ])

    # un fps légèrement différent à chaque export change aussi l'empreinte
    # binaire du fichier
    fps = clip.fps or 30
    fps = fps + random.choice([-1, 0, 1]) * 0.001

    # Qualité : CRF (facteur de qualité constant) plutôt qu'un bitrate fixe
    # — c'est ce qui donne la meilleure qualité possible par taille de
    # fichier avec libx264 (16-18 = quasi sans perte à l'oeil). Varié
    # légèrement par variante, comme le reste, pour l'empreinte binaire.
    crf = random.randint(16, 18)
    # +faststart : place l'index (moov atom) au début du fichier plutôt
    # qu'à la fin — sans ça, certains lecteurs (dont la lecture intégrée
    # dans l'app) n'arrivent pas à lire la vidéo en streaming et affichent
    # une erreur, alors que le fichier reste valide (d'où le téléchargement
    # et le partage qui, eux, fonctionnaient quand même).
    ffmpeg_params = ["-crf", str(crf)]
    if out_path.suffix.lower() in (".mp4", ".mov", ".m4v"):
        ffmpeg_params += ["-movflags", "+faststart"]

    # égalisation graves/aigus : filtre ffmpeg — ne peut pas être appliqué
    # ici (moviepy réencode déjà l'audio en amont puis fait un "-acodec copy"
    # au mux final, incompatible avec un filtre). Appliqué à la place dans
    # randomize_metadata(), qui fait de toute façon une seconde passe ffmpeg
    # sur le fichier entier.
    filtre_audio = None
    if clip.audio is not None:
        gain_graves = params["eq_bass"] * 30.0
        gain_aigus = params["eq_treble"] * 30.0
        filtre_audio = (
            f"equalizer=f=100:t=o:w=2:g={gain_graves:.2f},"
            f"equalizer=f=8000:t=o:w=2:g={gain_aigus:.2f}"
        )

    tmp_out = out_path.with_suffix(".tmp" + out_path.suffix)
    clip.write_videofile(
        str(tmp_out),
        fps=fps,
        codec="libx264",
        preset=PRESET_ENCODAGE,
        ffmpeg_params=ffmpeg_params,
        threads=threads_encodage or THREADS_ENCODAGE,
        audio_codec="aac" if clip.audio is not None else None,
        temp_audiofile=str(out_path.with_suffix(".tmp.m4a")),
        remove_temp=True,
        logger=None,
    )
    clip.close()

    randomize_metadata(tmp_out, out_path, locale, filtre_audio=filtre_audio)
    tmp_out.unlink(missing_ok=True)


def randomize_metadata(src: Path, dst: Path, locale: str = None, filtre_audio: str = None):
    """Réécrit les métadonnées (titre, auteur, horodatage, encodeur, appareil,
    logiciel de montage, id unique) pour que le conteneur ne porte plus
    l'empreinte d'origine, et les localise sur le pays demandé (langue,
    position GPS + altitude, fuseau horaire, modèle d'appareil plausible)."""
    unique_id = uuid.uuid4().hex[:12]
    fake_encoders = ["Lavf60.16.100", "Lavf61.1.100", "HandBrake 1.7.3", "Lavf59.27.100"]

    cmd = [FFMPEG, "-y", "-i", str(src), "-map_metadata", "-1"]

    loc = LOCALES.get(locale)
    creation_time = creation_time_for(loc["tz"]) if loc else "now"
    logiciel = random.choice(LOGICIELS_PLAUSIBLES)

    cmd += [
        "-metadata", f"title=clip_{unique_id}",
        "-metadata", f"comment={unique_id}",
        "-metadata", f"encoder={random.choice(fake_encoders)}",
        "-metadata", f"creation_time={creation_time}",
        "-metadata", f"com.apple.quicktime.software={logiciel}",
    ]

    if loc:
        altitude = random.randint(5, 180)
        cmd += [
            "-metadata", f"location={loc['location']}{altitude}/",
            "-metadata", f"location-eng={loc['location']}{altitude}/",
            "-metadata", f"com.apple.quicktime.location.ISO6709={loc['location']}{altitude}/",
            "-metadata:s:v:0", f"language={loc['language']}",
            "-metadata:s:a:0", f"language={loc['language']}",
        ]
        if loc.get("appareils"):
            appareil = random.choice(loc["appareils"])
            cmd += [
                "-metadata", f"com.apple.quicktime.model={appareil}",
                "-metadata", f"com.apple.quicktime.make={appareil.split()[0]}",
            ]

    if dst.suffix.lower() in (".mp4", ".mov", ".m4v"):
        cmd += ["-movflags", "+faststart"]

    if filtre_audio:
        # vidéo toujours en copie (rapide, aucune perte) ; seul l'audio est
        # réencodé, pour pouvoir y appliquer l'égalisation graves/aigus.
        cmd += ["-c:v", "copy", "-af", filtre_audio, "-c:a", "aac", str(dst)]
    else:
        cmd += ["-c", "copy", str(dst)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # fallback : si -c copy échoue (conteneur particulier), on garde
        # simplement le fichier déjà encodé tel quel
        src.replace(dst)


def main():
    parser = argparse.ArgumentParser(description="Génère des variantes d'une vidéo.")
    parser.add_argument("input", type=Path, help="Vidéo source")
    parser.add_argument("--mode", choices=["leger", "agressif"], default="leger")
    parser.add_argument("--locale", choices=list(LOCALES), default=None,
                         help="Pays à utiliser pour localiser les métadonnées (langue, position, fuseau horaire)")
    parser.add_argument("--out", type=Path, default=None, help="Fichier de sortie (mode simple)")
    parser.add_argument("--count", type=int, default=1, help="Nombre de variantes à générer")
    parser.add_argument("--outdir", type=Path, default=None, help="Dossier de sortie si --count > 1")
    parser.add_argument("--miroir", action="store_true",
                         help="Inverse la vidéo horizontalement (jamais tiré au sort, car ça inverse aussi le texte à l'écran)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Fichier introuvable : {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.count <= 1:
        out = args.out or args.input.with_name(
            f"{args.input.stem}_{args.mode}_{uuid.uuid4().hex[:6]}{args.input.suffix}"
        )
        build_variant(args.input, out, args.mode, args.locale, miroir=args.miroir)
        print(f"Variante générée : {out}")
    else:
        outdir = args.outdir or args.input.parent / f"{args.input.stem}_variantes"
        outdir.mkdir(parents=True, exist_ok=True)
        lot = batch_params(args.mode, args.count)
        for i, params in enumerate(lot):
            out = outdir / f"{args.input.stem}_{args.mode}_{i+1:02d}_{uuid.uuid4().hex[:6]}{args.input.suffix}"
            build_variant(args.input, out, args.mode, args.locale, params, miroir=args.miroir)
            print(f"[{i+1}/{args.count}] Variante générée : {out}")


if __name__ == "__main__":
    main()
