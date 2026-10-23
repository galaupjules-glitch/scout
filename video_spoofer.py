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
import random
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from moviepy import VideoFileClip
import moviepy.video.fx as vfx
import moviepy.audio.fx as afx
from imageio_ffmpeg import get_ffmpeg_exe

FFMPEG = get_ffmpeg_exe()

# Plages de variation par mode : (min, max) sauf indication contraire
RANGES = {
    "leger": {
        "crop_pct": (0.0, 0.025),       # recadrage des bords, 0 à 2.5%
        "zoom": (1.0, 1.03),            # léger zoom
        "brightness": (-0.04, 0.04),    # +/- 4%
        "contrast": (-0.05, 0.05),
        "saturation": (-0.06, 0.06),
        "hue_deg": (-3, 3),
        "speed": (0.98, 1.02),
        "rotate_deg": (-0.8, 0.8),
        "noise_std": (0.0, 2.0),        # bruit gaussien léger (0-255)
        "volume": (0.97, 1.03),
        "trim_start": (0.0, 0.15),      # secondes coupées au début
        "trim_end": (0.0, 0.15),
        "mirror_chance": 0.0,
    },
    "agressif": {
        "crop_pct": (0.04, 0.09),
        "zoom": (1.03, 1.12),
        "brightness": (-0.12, 0.12),
        "contrast": (-0.15, 0.15),
        "saturation": (-0.18, 0.18),
        "hue_deg": (-15, 15),
        "speed": (0.90, 1.10),
        "rotate_deg": (-2.5, 2.5),
        "noise_std": (3.0, 9.0),
        "volume": (0.85, 1.15),
        "trim_start": (0.1, 0.5),
        "trim_end": (0.1, 0.5),
        "mirror_chance": 0.5,
    },
}

# Localisation des métadonnées : langue, position (ISO 6709) et fuseau
# horaire d'une grande ville du pays visé, pour que le fichier porte une
# empreinte cohérente avec ce pays plutôt qu'avec le lieu réel de tournage.
LOCALES = {
    "fr": {
        "label": "France",
        "language": "fra",
        "location": "+48.8566+002.3522/",
        "tz": "+01:00",
    },
    "us": {
        "label": "États-Unis",
        "language": "eng",
        "location": "+40.7128-074.0060/",
        "tz": "-05:00",
    },
    "ca": {
        "label": "Canada",
        "language": "eng",
        "location": "+45.5017-073.5673/",
        "tz": "-05:00",
    },
    "au": {
        "label": "Australie",
        "language": "eng",
        "location": "-33.8688+151.2093/",
        "tz": "+11:00",
    },
}


def creation_time_for(tz_offset: str) -> str:
    sign = 1 if tz_offset.startswith("+") else -1
    hh, mm = tz_offset[1:].split(":")
    delta = timedelta(hours=int(hh), minutes=int(mm)) * sign
    local = datetime.now(timezone.utc) + delta
    return local.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset


def rand_in(bounds):
    lo, hi = bounds
    return random.uniform(lo, hi)


def add_noise_frame(get_frame, t, std):
    frame = get_frame(t).astype(np.int16)
    if std > 0:
        noise = np.random.normal(0, std, frame.shape).astype(np.int16)
        frame = frame + noise
    return np.clip(frame, 0, 255).astype("uint8")


def apply_color_grade(get_frame, t, brightness, contrast, saturation, hue_deg):
    frame = get_frame(t).astype(np.float32)

    # luminosité
    frame = frame + brightness * 255

    # contraste autour du gris moyen
    frame = (frame - 128) * (1 + contrast) + 128

    # saturation via conversion approx HSV sans dépendance cv2
    mean = frame.mean(axis=2, keepdims=True)
    frame = mean + (frame - mean) * (1 + saturation)

    # rotation de teinte simplifiée : léger mélange des canaux RGB
    if hue_deg:
        angle = np.deg2rad(hue_deg)
        cos_a, sin_a = np.cos(angle), np.sin(angle)
        r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
        # matrice de rotation de teinte approximée (YIQ-like shift)
        new_r = r * (0.299 + 0.701 * cos_a + 0.168 * sin_a) + \
            g * (0.587 - 0.587 * cos_a + 0.330 * sin_a) + \
            b * (0.114 - 0.114 * cos_a - 0.497 * sin_a)
        new_g = r * (0.299 - 0.299 * cos_a - 0.328 * sin_a) + \
            g * (0.587 + 0.413 * cos_a + 0.035 * sin_a) + \
            b * (0.114 - 0.114 * cos_a + 0.292 * sin_a)
        new_b = r * (0.299 - 0.3 * cos_a + 1.25 * sin_a) + \
            g * (0.587 - 0.588 * cos_a - 1.05 * sin_a) + \
            b * (0.114 + 0.886 * cos_a - 0.203 * sin_a)
        frame = np.stack([new_r, new_g, new_b], axis=-1)

    return np.clip(frame, 0, 255).astype("uint8")


def build_variant(src_path: Path, out_path: Path, mode: str, locale: str = None):
    if mode not in RANGES:
        raise ValueError("mode doit être 'leger' ou 'agressif'")
    if locale is not None and locale not in LOCALES:
        raise ValueError(f"locale doit être parmi {list(LOCALES)}")
    r = RANGES[mode]

    clip = VideoFileClip(str(src_path))

    # 1) découpe légère au début/à la fin
    trim_start = rand_in(r["trim_start"])
    trim_end = rand_in(r["trim_end"])
    end_t = max(trim_start + 0.3, clip.duration - trim_end)
    clip = clip.subclipped(trim_start, end_t)

    # 2) miroir horizontal (mode agressif seulement, aléatoire)
    if random.random() < r["mirror_chance"]:
        clip = clip.with_effects([vfx.MirrorX()])

    # 3) rotation très légère + zoom + recadrage des bords (supprime les
    #    marges noires introduites par la rotation et change le cadrage)
    rotate_deg = rand_in(r["rotate_deg"])
    zoom = rand_in(r["zoom"])
    crop_pct = rand_in(r["crop_pct"]) + abs(rotate_deg) / 90.0

    if abs(rotate_deg) > 0.01:
        clip = clip.with_effects([vfx.Rotate(rotate_deg, unit="deg")])

    w, h = clip.size
    crop_w = int(w * crop_pct / 2)
    crop_h = int(h * crop_pct / 2)
    if crop_w or crop_h:
        clip = clip.with_effects([
            vfx.Crop(x1=crop_w, y1=crop_h, x2=w - crop_w, y2=h - crop_h)
        ])

    if abs(zoom - 1.0) > 1e-3:
        clip = clip.with_effects([vfx.Resize(zoom)])
        # on revient à une taille proche de l'originale pour rester cohérent
        clip = clip.with_effects([vfx.Resize((w, h))])

    # 4) étalonnage couleur + bruit, image par image
    brightness = rand_in(r["brightness"])
    contrast = rand_in(r["contrast"])
    saturation = rand_in(r["saturation"])
    hue_deg = rand_in(r["hue_deg"])
    noise_std = rand_in(r["noise_std"])

    def _frame_func(get_frame, t):
        graded = apply_color_grade(get_frame, t, brightness, contrast, saturation, hue_deg)
        return add_noise_frame(lambda _t: graded, t, noise_std)

    clip = clip.transform(_frame_func, apply_to="video")

    # 5) vitesse de lecture (image + son, restent synchronisés)
    speed = rand_in(r["speed"])
    if abs(speed - 1.0) > 1e-3:
        clip = clip.with_effects([vfx.MultiplySpeed(speed)])

    # 6) volume audio
    if clip.audio is not None:
        volume = rand_in(r["volume"])
        clip = clip.with_effects([afx.MultiplyVolume(volume)])

    # un bitrate/fps légèrement différent à chaque export change aussi
    # l'empreinte binaire du fichier
    fps = clip.fps or 30
    fps = fps + random.choice([-1, 0, 1]) * 0.001
    bitrate = f"{random.randint(3200, 5200)}k"

    tmp_out = out_path.with_suffix(".tmp" + out_path.suffix)
    clip.write_videofile(
        str(tmp_out),
        fps=fps,
        bitrate=bitrate,
        codec="libx264",
        audio_codec="aac" if clip.audio is not None else None,
        temp_audiofile=str(out_path.with_suffix(".tmp.m4a")),
        remove_temp=True,
        logger=None,
    )
    clip.close()

    randomize_metadata(tmp_out, out_path, locale)
    tmp_out.unlink(missing_ok=True)


def randomize_metadata(src: Path, dst: Path, locale: str = None):
    """Réécrit les métadonnées (titre, auteur, horodatage, encodeur, id
    unique) pour que le conteneur ne porte plus l'empreinte d'origine, et
    les localise sur le pays demandé (langue, position, fuseau horaire)."""
    unique_id = uuid.uuid4().hex[:12]
    fake_encoders = ["Lavf60.16.100", "Lavf61.1.100", "HandBrake 1.7.3", "Lavf59.27.100"]

    cmd = [FFMPEG, "-y", "-i", str(src), "-map_metadata", "-1"]

    loc = LOCALES.get(locale)
    creation_time = creation_time_for(loc["tz"]) if loc else "now"

    cmd += [
        "-metadata", f"title=clip_{unique_id}",
        "-metadata", f"comment={unique_id}",
        "-metadata", f"encoder={random.choice(fake_encoders)}",
        "-metadata", f"creation_time={creation_time}",
    ]

    if loc:
        cmd += [
            "-metadata", f"location={loc['location']}",
            "-metadata", f"location-eng={loc['location']}",
            "-metadata", f"com.apple.quicktime.location.ISO6709={loc['location']}",
            "-metadata:s:v:0", f"language={loc['language']}",
            "-metadata:s:a:0", f"language={loc['language']}",
        ]

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
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Fichier introuvable : {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.count <= 1:
        out = args.out or args.input.with_name(
            f"{args.input.stem}_{args.mode}_{uuid.uuid4().hex[:6]}{args.input.suffix}"
        )
        build_variant(args.input, out, args.mode, args.locale)
        print(f"Variante générée : {out}")
    else:
        outdir = args.outdir or args.input.parent / f"{args.input.stem}_variantes"
        outdir.mkdir(parents=True, exist_ok=True)
        for i in range(args.count):
            out = outdir / f"{args.input.stem}_{args.mode}_{i+1:02d}_{uuid.uuid4().hex[:6]}{args.input.suffix}"
            build_variant(args.input, out, args.mode, args.locale)
            print(f"[{i+1}/{args.count}] Variante générée : {out}")


if __name__ == "__main__":
    main()
