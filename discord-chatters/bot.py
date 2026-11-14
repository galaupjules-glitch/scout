"""Bot Discord de recrutement des chatters.

Parcours (dans un fil privé par candidat) :
  1. Présentation      (prénom, nom, âge, pays/ville, langues)
  2. Test de localisation (répondre à 3 messages de fans en anglais naturel)
  3. Test de frappe    (recopier un texte affiché en image, chrono -> mots/minute)
  4. Expérience        (débutant -> on arrête poliment)
  5. Agence précédente (nom, durée, chiffres)
  6. Shifts souhaités  (matin / après-midi / nuit)
  7. Résultats         (captures d'écran de leurs stats)
-> dossier complet envoyé dans le salon staff avec boutons Accepter / Refuser.

Lancer :  python3 bot.py
"""
import io
import json
import random
import re
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import config as C

ICI = Path(__file__).parent
DB_PATH = ICI / "candidats.db"
UPLOADS = ICI / "uploads"

ORDRE = ["identite", "localisation", "frappe", "experience", "agence", "shifts", "resultats", "termine"]
COULEUR = discord.Colour.from_rgb(201, 168, 106)  # doré


# ============================================================================
# Base de données (SQLite, un fichier à côté du bot)
# ============================================================================
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS candidats (
            user_id INTEGER PRIMARY KEY,
            pseudo TEXT, thread_id INTEGER, etape TEXT, statut TEXT,
            data TEXT, cree_le REAL, maj_le REAL)"""
    )
    return con


def charger(uid):
    with db() as con:
        r = con.execute("SELECT * FROM candidats WHERE user_id=?", (uid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["data"] = json.loads(d["data"] or "{}")
    return d


def sauver(c):
    c["maj_le"] = time.time()
    with db() as con:
        con.execute(
            """INSERT OR REPLACE INTO candidats
               (user_id, pseudo, thread_id, etape, statut, data, cree_le, maj_le)
               VALUES (?,?,?,?,?,?,?,?)""",
            (c["user_id"], c["pseudo"], c["thread_id"], c["etape"], c["statut"],
             json.dumps(c["data"], ensure_ascii=False), c["cree_le"], c["maj_le"]),
        )


def etape_suivante(c):
    c["etape"] = ORDRE[ORDRE.index(c["etape"]) + 1]


async def verifier(interaction, etape):
    """Renvoie le candidat si c'est bien lui, dans son fil, à la bonne étape."""
    c = charger(interaction.user.id)
    if not c or c["thread_id"] != interaction.channel_id:
        await interaction.response.send_message("Ce fil n'est pas le tien 🙂", ephemeral=True)
        return None
    if c["etape"] != etape:
        await interaction.response.send_message("Cette étape est déjà passée.", ephemeral=True)
        return None
    return c


def tronquer(t, n=1000):
    t = str(t or "—")
    return t if len(t) <= n else t[: n - 1] + "…"


# ============================================================================
# Envoi de chaque étape dans le fil
# ============================================================================
def embed(titre, texte, etape=None):
    e = discord.Embed(title=titre, description=texte, colour=COULEUR)
    if etape in ORDRE[:-1]:
        e.set_footer(text=f"Étape {ORDRE.index(etape) + 1} / {len(ORDRE) - 1}")
    return e


async def envoyer_etape(fil, c):
    et = c["etape"]
    if et == "identite":
        await fil.send(
            f"Bienvenue <@{c['user_id']}> ! 👋",
            embed=embed("Présente-toi", "Commence par nous dire qui tu es. Clique sur le bouton.", et),
            view=VueIdentite(),
        )
    elif et == "localisation":
        fans = "\n".join(f"**Fan #{i + 1} :** {m}" for i, m in enumerate(C.MESSAGES_FANS))
        await fil.send(embed=embed(
            "Test de localisation 🌎",
            "Tu es la créatrice. Réponds à ces fans **en anglais**, comme le ferait une "
            "fille américaine : naturel, court, avec le bon slang. Pas de traduction mot à mot.\n\n"
            f"{fans}", et), view=VueLocalisation())
    elif et == "frappe":
        await fil.send(embed=embed(
            "Test de frappe ⌨️",
            f"Un texte va s'afficher en image. Recopie-le **le plus vite et le plus exactement "
            f"possible**, puis envoie-le directement ici dans le fil.\n\n"
            f"Objectif : **{C.WPM_MIN} mots/minute** avec **{C.PRECISION_MIN} %** de précision.\n"
            f"Le chrono démarre dès que l'image apparaît. Essais : {C.ESSAIS_FRAPPE}.", et),
            view=VueFrappe())
    elif et == "experience":
        await fil.send(embed=embed("Ton expérience", "As-tu déjà travaillé comme chatter ?", et),
                       view=VueExperience())
    elif et == "agence":
        await fil.send(embed=embed(
            "Ton expérience en agence 🏢",
            "Dis-nous avec qui tu as bossé et tes chiffres. Sois honnête, on vérifie.", et),
            view=VueAgence())
    elif et == "shifts":
        lignes = "\n".join(f"• **{n}** — {h}" for _, n, h in C.SHIFTS)
        await fil.send(embed=embed("Tes shifts ⏰", f"Quels shifts t'intéressent ?\n\n{lignes}", et),
                       view=VueShifts())
    elif et == "resultats":
        await fil.send(embed=embed(
            "Tes résultats 📈",
            "Envoie ici, dans le fil, des **captures d'écran** de tes résultats "
            "(stats de ventes, dashboard, conversations qui ont converti…). "
            f"Jusqu'à {C.MAX_FICHIERS} images.\n\nQuand c'est bon, clique sur **J'ai terminé**.", et),
            view=VueResultats())


async def avancer(interaction_ou_fil, c):
    etape_suivante(c)
    sauver(c)
    fil = interaction_ou_fil if isinstance(interaction_ou_fil, discord.Thread) else interaction_ou_fil.channel
    await envoyer_etape(fil, c)


# ============================================================================
# Étape 1 : identité
# ============================================================================
class ModalIdentite(discord.ui.Modal, title="Présente-toi"):
    prenom = discord.ui.TextInput(label="Prénom", max_length=50)
    nom = discord.ui.TextInput(label="Nom", max_length=50)
    age = discord.ui.TextInput(label="Âge", max_length=3)
    lieu = discord.ui.TextInput(label="D'où viens-tu ? (pays, ville)", max_length=100)
    langues = discord.ui.TextInput(
        label="Langues parlées + ton niveau d'anglais", max_length=200,
        placeholder="ex : Français natif, anglais courant (C1)")

    async def on_submit(self, interaction):
        c = await verifier(interaction, "identite")
        if not c:
            return
        c["data"]["identite"] = {k: str(getattr(self, k)) for k in ("prenom", "nom", "age", "lieu", "langues")}
        await interaction.response.send_message(f"Merci {self.prenom} ! ✅")
        await avancer(interaction, c)


class VueIdentite(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Me présenter", style=discord.ButtonStyle.primary, emoji="📝", custom_id="cand:identite")
    async def go(self, interaction, _):
        if await verifier_silencieux(interaction, "identite"):
            await interaction.response.send_modal(ModalIdentite())


async def verifier_silencieux(interaction, etape):
    c = charger(interaction.user.id)
    if not c or c["thread_id"] != interaction.channel_id or c["etape"] != etape:
        await interaction.response.send_message("Ce bouton n'est pas (ou plus) pour toi.", ephemeral=True)
        return False
    return True


# ============================================================================
# Étape 2 : localisation
# ============================================================================
class ModalLocalisation(discord.ui.Modal, title="Test de localisation"):
    def __init__(self):
        super().__init__()
        self.champs = []
        for i, msg in enumerate(C.MESSAGES_FANS[:5]):
            t = discord.ui.TextInput(label=f"Ta réponse au fan #{i + 1}", placeholder=msg[:100],
                                     style=discord.TextStyle.paragraph, max_length=400)
            self.champs.append(t)
            self.add_item(t)

    async def on_submit(self, interaction):
        c = await verifier(interaction, "localisation")
        if not c:
            return
        c["data"]["localisation"] = [{"fan": f, "reponse": str(t)} for f, t in zip(C.MESSAGES_FANS, self.champs)]
        await interaction.response.send_message("Réponses enregistrées ✅")
        await avancer(interaction, c)


class VueLocalisation(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Répondre aux fans", style=discord.ButtonStyle.primary, emoji="💬", custom_id="cand:localisation")
    async def go(self, interaction, _):
        if await verifier_silencieux(interaction, "localisation"):
            await interaction.response.send_modal(ModalLocalisation())


# ============================================================================
# Étape 3 : test de frappe
# ============================================================================
def _police(taille):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, taille)
    return ImageFont.load_default(size=taille)


def image_texte(texte):
    """Rend le texte en PNG (impossible à copier-coller)."""
    from PIL import Image, ImageDraw
    police, largeur, marge = _police(32), 1000, 50
    brouillon = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lignes, ligne = [], ""
    for mot in texte.split():
        essai = f"{ligne} {mot}".strip()
        if brouillon.textlength(essai, font=police) > largeur - 2 * marge:
            lignes.append(ligne)
            ligne = mot
        else:
            ligne = essai
    lignes.append(ligne)
    h_ligne = 48
    img = Image.new("RGB", (largeur, 2 * marge + h_ligne * len(lignes)), (24, 26, 33))
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lignes):
        d.text((marge, marge + i * h_ligne), l, font=police, fill=(240, 232, 214))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def _normaliser(t):
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


def noter_frappe(reference, tape, secondes):
    reference, tape = _normaliser(reference), _normaliser(tape)
    minutes = max(secondes, 1) / 60
    wpm_brut = (len(tape) / 5) / minutes            # norme : 1 mot = 5 caractères
    precision = SequenceMatcher(None, reference, tape).ratio() * 100
    wpm_net = wpm_brut * precision / 100
    return {
        "wpm": round(wpm_net, 1), "wpm_brut": round(wpm_brut, 1),
        "precision": round(precision, 1), "secondes": round(secondes, 1),
        "reussi": wpm_net >= C.WPM_MIN and precision >= C.PRECISION_MIN,
        "suspect": wpm_brut >= C.WPM_SUSPECT,
    }


class VueFrappe(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Lancer le chrono", style=discord.ButtonStyle.success, emoji="⏱️", custom_id="cand:frappe")
    async def go(self, interaction, _):
        c = await verifier(interaction, "frappe")
        if not c:
            return
        f = c["data"].setdefault("frappe", {"essais": []})
        if f.get("debut"):
            return await interaction.response.send_message("Le chrono tourne déjà, envoie ton texte !", ephemeral=True)
        if len(f["essais"]) >= C.ESSAIS_FRAPPE:
            return await interaction.response.send_message("Tu as utilisé tous tes essais.", ephemeral=True)
        texte = random.choice(C.TEXTES_FRAPPE)
        await interaction.response.send_message(
            "**À toi ! Recopie ce texte et envoie-le ici ⬇️**",
            file=discord.File(image_texte(texte), "texte.png"))
        f["texte"], f["debut"] = texte, time.time()
        sauver(c)


async def traiter_frappe(message, c):
    f = c["data"]["frappe"]
    res = noter_frappe(f["texte"], message.content, time.time() - f["debut"])
    f["essais"].append(res)
    f["debut"] = None
    meilleur = max(f["essais"], key=lambda r: (r["reussi"], r["wpm"]))
    f["meilleur"] = meilleur
    sauver(c)

    verdict = "✅ Réussi !" if res["reussi"] else "❌ Pas encore le niveau demandé."
    await message.reply(
        f"{verdict}\n**{res['wpm']} mots/min** · précision **{res['precision']} %** · {res['secondes']} s")

    reste = C.ESSAIS_FRAPPE - len(f["essais"])
    if not res["reussi"] and reste > 0:
        await message.channel.send(f"Tu peux réessayer ({reste} essai restant) 👇", view=VueFrappe())
        return
    if not meilleur["reussi"] and C.STOP_SI_FRAPPE_RATEE:
        c["statut"] = "refuse_frappe"
        sauver(c)
        await message.channel.send(embed=embed(
            "Merci pour ton temps 🙏",
            f"Pour l'instant on cherche des profils à {C.WPM_MIN} mots/min minimum. "
            "Entraîne-toi et repostule plus tard !"))
        await alerter_staff(message.guild, c, "⌨️ Candidature arrêtée : test de frappe raté")
        return
    await avancer(message.channel, c)


# ============================================================================
# Étape 4 : expérience
# ============================================================================
class VueExperience(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Oui, j'ai déjà été chatter", style=discord.ButtonStyle.success, custom_id="cand:exp:oui")
    async def oui(self, interaction, _):
        c = await verifier(interaction, "experience")
        if not c:
            return
        c["data"]["experience"] = "experimente"
        await interaction.response.send_message("Parfait 💪")
        await avancer(interaction, c)

    @discord.ui.button(label="Non, je débute", style=discord.ButtonStyle.secondary, custom_id="cand:exp:non")
    async def non(self, interaction, _):
        c = await verifier(interaction, "experience")
        if not c:
            return
        c["data"]["experience"] = "debutant"
        c["statut"], c["etape"] = "refuse_debutant", "termine"
        sauver(c)
        await interaction.response.send_message(embed=embed(
            "Merci pour ta candidature 🙏",
            f"Pour le moment, {C.NOM_AGENCE} recrute uniquement des chatters **déjà expérimentés**. "
            "Garde le contact, on te recontactera si on ouvre des places pour débutants !"))
        await alerter_staff(interaction.guild, c, "🐣 Candidature arrêtée : débutant")


# ============================================================================
# Étape 5 : agence
# ============================================================================
class ModalAgence(discord.ui.Modal, title="Ton expérience"):
    agence = discord.ui.TextInput(label="Nom de l'agence (ou « freelance »)", max_length=100)
    duree = discord.ui.TextInput(label="Combien de temps ? (mois)", max_length=50)
    creatrices = discord.ui.TextInput(label="Nb de créatrices gérées + leur taille", max_length=200,
                                      placeholder="ex : 3 créatrices, top 1% / 5% OnlyFans")
    chiffres = discord.ui.TextInput(label="Tes chiffres moyens (par shift / par mois)", max_length=300,
                                    style=discord.TextStyle.paragraph,
                                    placeholder="ex : 400 $ / shift de 8h, 9 000 $ le mois dernier")
    depart = discord.ui.TextInput(label="Pourquoi tu cherches une autre agence ?", max_length=400,
                                  style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction):
        c = await verifier(interaction, "agence")
        if not c:
            return
        c["data"]["agence"] = {k: str(getattr(self, k)) for k in ("agence", "duree", "creatrices", "chiffres", "depart")}
        await interaction.response.send_message("Noté ✅")
        await avancer(interaction, c)


class VueAgence(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Remplir", style=discord.ButtonStyle.primary, emoji="🏢", custom_id="cand:agence")
    async def go(self, interaction, _):
        if await verifier_silencieux(interaction, "agence"):
            await interaction.response.send_modal(ModalAgence())


# ============================================================================
# Étape 6 : shifts
# ============================================================================
class VueShifts(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        custom_id="cand:shifts", placeholder="Choisis un ou plusieurs shifts",
        min_values=1, max_values=len(C.SHIFTS),
        options=[discord.SelectOption(label=n, value=v, description=h) for v, n, h in C.SHIFTS])
    async def choix(self, interaction, select):
        c = await verifier(interaction, "shifts")
        if not c:
            return
        noms = {v: f"{n} ({h})" for v, n, h in C.SHIFTS}
        c["data"]["shifts"] = [noms[v] for v in select.values]
        await interaction.response.send_message("Shifts : " + ", ".join(c["data"]["shifts"]) + " ✅")
        await avancer(interaction, c)


# ============================================================================
# Étape 7 : résultats (captures)
# ============================================================================
async def traiter_upload(message, c):
    fichiers = c["data"].setdefault("fichiers", [])
    dossier = UPLOADS / str(c["user_id"])
    dossier.mkdir(parents=True, exist_ok=True)
    ajoutes = 0
    for a in message.attachments:
        if len(fichiers) >= C.MAX_FICHIERS:
            break
        chemin = dossier / f"{message.id}_{a.filename}"
        await a.save(chemin)
        fichiers.append(str(chemin))
        ajoutes += 1
    sauver(c)
    if ajoutes:
        await message.add_reaction("✅")
    if len(fichiers) >= C.MAX_FICHIERS:
        await message.channel.send(f"Maximum de {C.MAX_FICHIERS} fichiers atteint. Clique sur **J'ai terminé**.")


class VueResultats(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="J'ai terminé", style=discord.ButtonStyle.success, emoji="✅", custom_id="cand:fin")
    async def fin(self, interaction, _):
        c = await verifier(interaction, "resultats")
        if not c:
            return
        if not c["data"].get("fichiers"):
            return await interaction.response.send_message(
                "Envoie au moins une capture, ou clique sur **Je n'ai pas de résultats**.", ephemeral=True)
        await finaliser(interaction, c)

    @discord.ui.button(label="Je n'ai pas de résultats", style=discord.ButtonStyle.secondary, custom_id="cand:fin:vide")
    async def vide(self, interaction, _):
        c = await verifier(interaction, "resultats")
        if c:
            await finaliser(interaction, c)


async def finaliser(interaction, c):
    c["etape"], c["statut"] = "termine", "en_attente"
    sauver(c)
    await interaction.response.send_message(embed=embed(
        "Candidature envoyée 🎉",
        "Merci ! L'équipe étudie ton dossier et revient vers toi ici très vite."))
    await envoyer_dossier(interaction.guild, c)


# ============================================================================
# Côté staff : dossier + décision
# ============================================================================
def resume_embed(c):
    d = c["data"]
    i = d.get("identite", {})
    e = discord.Embed(title=f"📋 {i.get('prenom', '')} {i.get('nom', '')}".strip(), colour=COULEUR)
    e.description = f"<@{c['user_id']}> · fil : <#{c['thread_id']}>"
    e.add_field(name="Identité", value=tronquer(
        f"Âge : {i.get('age')}\nLieu : {i.get('lieu')}\nLangues : {i.get('langues')}"), inline=False)
    f = d.get("frappe", {}).get("meilleur")
    if f:
        flag = " ⚠️ suspect (copier-coller ?)" if f["suspect"] else ""
        e.add_field(name="Frappe", value=f"{'✅' if f['reussi'] else '❌'} **{f['wpm']} mots/min** · "
                                         f"{f['precision']} % · {len(d['frappe']['essais'])} essai(s){flag}",
                    inline=False)
    loc = d.get("localisation")
    if loc:
        e.add_field(name="Localisation", value=tronquer(
            "\n".join(f"> {x['fan']}\n→ {x['reponse']}" for x in loc)), inline=False)
    e.add_field(name="Expérience", value="Expérimenté" if d.get("experience") == "experimente" else
                "Débutant" if d.get("experience") else "—", inline=True)
    if d.get("shifts"):
        e.add_field(name="Shifts", value="\n".join(d["shifts"]), inline=True)
    a = d.get("agence")
    if a:
        e.add_field(name="Agence", value=tronquer(
            f"**{a['agence']}** · {a['duree']}\nCréatrices : {a['creatrices']}\n"
            f"Chiffres : {a['chiffres']}\nDépart : {a['depart']}"), inline=False)
    e.add_field(name="Captures", value=str(len(d.get("fichiers", []))), inline=True)
    e.add_field(name="Statut", value=c["statut"], inline=True)
    return e


async def canal_staff(guild):
    if not C.CANAL_STAFF_ID:
        return None
    return guild.get_channel(C.CANAL_STAFF_ID) or await guild.fetch_channel(C.CANAL_STAFF_ID)


async def envoyer_dossier(guild, c):
    canal = await canal_staff(guild)
    if not canal:
        return
    vue = discord.ui.View(timeout=None)
    vue.add_item(BoutonDecision("ok", c["user_id"]))
    vue.add_item(BoutonDecision("non", c["user_id"]))
    fichiers = [discord.File(p) for p in c["data"].get("fichiers", []) if Path(p).exists()][:10]
    await canal.send(embed=resume_embed(c), view=vue, files=fichiers)


async def alerter_staff(guild, c, titre):
    canal = await canal_staff(guild)
    if canal:
        e = resume_embed(c)
        e.title = titre
        await canal.send(embed=e)


class BoutonDecision(discord.ui.DynamicItem[discord.ui.Button], template=r"staff:(?P<action>ok|non):(?P<uid>\d+)"):
    def __init__(self, action, uid):
        ok = action == "ok"
        super().__init__(discord.ui.Button(
            label="Accepter" if ok else "Refuser", emoji="✅" if ok else "❌",
            style=discord.ButtonStyle.success if ok else discord.ButtonStyle.danger,
            custom_id=f"staff:{action}:{uid}"))
        self.action, self.uid = action, uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["uid"]))

    async def callback(self, interaction):
        c = charger(self.uid)
        if not c:
            return await interaction.response.send_message("Candidat introuvable.", ephemeral=True)
        ok = self.action == "ok"
        c["statut"] = "accepte" if ok else "refuse"
        c["data"]["decision_par"] = str(interaction.user)
        sauver(c)

        membre = interaction.guild.get_member(self.uid)
        if membre is None:
            try:
                membre = await interaction.guild.fetch_member(self.uid)
            except discord.NotFound:
                membre = None
        if membre and ok and C.ROLE_CHATTER_ID:
            await membre.add_roles(discord.Object(C.ROLE_CHATTER_ID), reason="Candidature acceptée")
        if membre and C.ROLE_CANDIDAT_ID:
            await membre.remove_roles(discord.Object(C.ROLE_CANDIDAT_ID), reason="Candidature traitée")

        fil = interaction.guild.get_thread(c["thread_id"])
        if fil:
            if ok:
                await fil.send(f"<@{self.uid}>", embed=embed(
                    f"Bienvenue chez {C.NOM_AGENCE} ! 🎉",
                    "Ta candidature est **acceptée**. Un manager va te contacter pour ton onboarding."))
            else:
                await fil.send(f"<@{self.uid}>", embed=embed(
                    "Réponse à ta candidature",
                    "Merci pour ton temps. On ne donne pas suite pour le moment, "
                    "mais n'hésite pas à repostuler dans quelques mois."))
            await fil.edit(archived=True, locked=True)

        e = resume_embed(c)
        e.colour = discord.Colour.green() if ok else discord.Colour.red()
        await interaction.response.edit_message(
            content=f"{'✅ Accepté' if ok else '❌ Refusé'} par {interaction.user.mention}", embed=e, view=None)


# ============================================================================
# Panneau « Postuler »
# ============================================================================
class VuePostuler(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Postuler", style=discord.ButtonStyle.success, emoji="🚀", custom_id="cand:postuler")
    async def postuler(self, interaction, _):
        c = charger(interaction.user.id)
        if c and c["statut"] in ("en_cours", "en_attente"):
            return await interaction.response.send_message(
                f"Tu as déjà une candidature : <#{c['thread_id']}>", ephemeral=True)
        if c:
            return await interaction.response.send_message(
                "Tu as déjà postulé. Contacte un membre du staff si besoin.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        fil = await interaction.channel.create_thread(
            name=f"candidature-{interaction.user.name}"[:100],
            type=discord.ChannelType.private_thread, invitable=False,
            auto_archive_duration=10080)
        await fil.add_user(interaction.user)
        if C.ROLE_CANDIDAT_ID:
            await interaction.user.add_roles(discord.Object(C.ROLE_CANDIDAT_ID), reason="Candidature")
        c = {"user_id": interaction.user.id, "pseudo": str(interaction.user), "thread_id": fil.id,
             "etape": "identite", "statut": "en_cours", "data": {}, "cree_le": time.time()}
        sauver(c)
        await envoyer_etape(fil, c)
        await interaction.followup.send(f"C'est parti 👉 {fil.mention}", ephemeral=True)


def embed_panneau():
    return discord.Embed(
        title=f"Rejoins l'équipe chatting de {C.NOM_AGENCE} 💬",
        colour=COULEUR,
        description=(
            "Tu veux devenir chatter ? Clique sur **Postuler** : un fil privé s'ouvre "
            "et le bot te guide (≈ 10 minutes).\n\n"
            "**Au programme :**\n"
            "1️⃣ Présentation\n2️⃣ Test de localisation (anglais)\n"
            f"3️⃣ Test de frappe ({C.WPM_MIN} mots/min)\n4️⃣ Ton expérience & ton agence\n"
            "5️⃣ Tes shifts\n6️⃣ Tes résultats (captures)"),
    )


# ============================================================================
# Bot
# ============================================================================
class Bot(commands.Bot):
    async def setup_hook(self):
        for v in (VuePostuler, VueIdentite, VueLocalisation, VueFrappe, VueExperience,
                  VueAgence, VueShifts, VueResultats):
            self.add_view(v())
        self.add_dynamic_items(BoutonDecision)
        if C.GUILD_ID:
            g = discord.Object(C.GUILD_ID)
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)


intents = discord.Intents.default()
intents.message_content = True   # nécessaire pour lire le test de frappe
bot = Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} — {len(bot.guilds)} serveur(s)")


@bot.event
async def on_message(message):
    if message.author.bot or not isinstance(message.channel, discord.Thread):
        return
    c = charger(message.author.id)
    if not c or c["thread_id"] != message.channel.id or c["statut"] != "en_cours":
        return
    if c["etape"] == "frappe" and c["data"].get("frappe", {}).get("debut") and message.content:
        await traiter_frappe(message, c)
    elif c["etape"] == "resultats" and message.attachments:
        await traiter_upload(message, c)


@bot.tree.command(name="panneau", description="Publie le panneau « Postuler » dans ce salon")
@app_commands.default_permissions(administrator=True)
async def panneau(interaction: discord.Interaction):
    await interaction.channel.send(embed=embed_panneau(), view=VuePostuler())
    await interaction.response.send_message("Panneau publié ✅", ephemeral=True)


@bot.tree.command(name="candidat", description="Voir le dossier d'un candidat")
@app_commands.default_permissions(manage_guild=True)
async def candidat(interaction: discord.Interaction, membre: discord.Member):
    c = charger(membre.id)
    if not c:
        return await interaction.response.send_message("Aucune candidature.", ephemeral=True)
    await interaction.response.send_message(embed=resume_embed(c), ephemeral=True)


@bot.tree.command(name="reset-candidat", description="Efface la candidature d'un membre (il pourra repostuler)")
@app_commands.default_permissions(manage_guild=True)
async def reset_candidat(interaction: discord.Interaction, membre: discord.Member):
    with db() as con:
        con.execute("DELETE FROM candidats WHERE user_id=?", (membre.id,))
    await interaction.response.send_message(f"Candidature de {membre.mention} effacée.", ephemeral=True)


@bot.tree.command(name="export-candidats", description="Exporte toutes les candidatures en CSV")
@app_commands.default_permissions(manage_guild=True)
async def export_candidats(interaction: discord.Interaction):
    import csv
    with db() as con:
        lignes = con.execute("SELECT * FROM candidats ORDER BY cree_le").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["pseudo", "prenom", "nom", "age", "lieu", "langues", "wpm", "precision", "frappe_ok",
                "experience", "agence", "duree", "creatrices", "chiffres", "shifts", "nb_captures", "statut"])
    for r in lignes:
        d = json.loads(r["data"] or "{}")
        i, a, f = d.get("identite", {}), d.get("agence", {}), d.get("frappe", {}).get("meilleur", {})
        w.writerow([r["pseudo"], i.get("prenom"), i.get("nom"), i.get("age"), i.get("lieu"), i.get("langues"),
                    f.get("wpm"), f.get("precision"), f.get("reussi"), d.get("experience"),
                    a.get("agence"), a.get("duree"), a.get("creatrices"), a.get("chiffres"),
                    " | ".join(d.get("shifts", [])), len(d.get("fichiers", [])), r["statut"]])
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    await interaction.response.send_message(file=discord.File(data, "candidats.csv"), ephemeral=True)


if __name__ == "__main__":
    if not C.TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN manquant : copie .env.example en .env et remplis-le.")
    bot.run(C.TOKEN)
