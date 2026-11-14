"""Réglages du bot de recrutement chatters.

Les identifiants secrets (token) et les IDs Discord se mettent dans le fichier `.env`
(copie `.env.example` -> `.env`). Le reste (textes, shifts, seuils) se règle ici.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass


def _id(nom):
    v = os.environ.get(nom, "").strip()
    return int(v) if v.isdigit() else None


# --- Discord (dans .env) -----------------------------------------------------
TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
GUILD_ID = _id("GUILD_ID")                    # ton serveur
CANAL_CANDIDATURE_ID = _id("CANAL_CANDIDATURE_ID")  # salon où est le bouton « Postuler »
CANAL_STAFF_ID = _id("CANAL_STAFF_ID")        # salon privé où arrivent les dossiers
ROLE_CHATTER_ID = _id("ROLE_CHATTER_ID")      # rôle donné quand tu acceptes
ROLE_CANDIDAT_ID = _id("ROLE_CANDIDAT_ID")    # (optionnel) rôle pendant la candidature

NOM_AGENCE = os.environ.get("NOM_AGENCE", "Aurivon")

# --- Test de frappe ----------------------------------------------------------
WPM_MIN = int(os.environ.get("WPM_MIN", 40))          # mots / minute minimum
PRECISION_MIN = int(os.environ.get("PRECISION_MIN", 90))  # % de précision minimum
ESSAIS_FRAPPE = 2            # nombre d'essais autorisés
WPM_SUSPECT = 150            # au-dessus : probablement du copier-coller (OCR) -> signalé au staff
STOP_SI_FRAPPE_RATEE = False  # True = on arrête la candidature si le test est raté

# Textes à recopier (affichés en IMAGE pour empêcher le copier-coller).
# Style chatting, en anglais (les fans sont surtout US/UK).
TEXTES_FRAPPE = [
    "Hey babe, I just got back from the gym and I am so tired but I could not stop "
    "thinking about you. Tell me what you have been up to today, I want to hear "
    "everything. I might have something special to show you later if you are good.",
    "Good morning handsome, did you sleep well? I had the craziest dream last night "
    "and you were in it. I am making coffee right now and planning a little photo "
    "shoot this afternoon. What kind of outfit should I wear for you?",
    "You always know how to make me smile, it is honestly my favorite part of the "
    "day. I am lying in bed with my phone and I have a few new pictures that nobody "
    "else has seen yet. Want me to send you a sneak peek right now?",
]

# --- Test de localisation ----------------------------------------------------
# Le candidat répond comme s'il était la créatrice, en anglais naturel « local »
# (slang, abréviations US). Le staff juge à la lecture.
MESSAGES_FANS = [
    "hey u up? 👀 cant sleep lol",
    "wyd this weekend? any plans or just chillin",
    "ngl ur last post had me dead 😂 where was that taken",
]

# --- Shifts ------------------------------------------------------------------
SHIFTS = [
    ("matin", "Matin", "06h – 14h (heure de Paris)"),
    ("aprem", "Après-midi", "14h – 22h (heure de Paris)"),
    ("nuit", "Nuit", "22h – 06h (heure de Paris)"),
]

# --- Résultats ---------------------------------------------------------------
MAX_FICHIERS = 10  # captures max envoyées par candidat
