#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construit le serveur Discord Vexa (rôles, salons, permissions, messages, webhook des candidatures).

    DISCORD_TOKEN=... GUILD_ID=... python3 construire_serveur.py

À lancer UNE fois sur un serveur neuf. Le token du bot n'est jamais écrit sur le disque.
Sortie : l'URL du webhook #applications (à mettre dans le site de candidature) et les IDs des messages.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
TOKEN = os.environ["DISCORD_TOKEN"]
G = os.environ["GUILD_ID"]
LIEN_CANDIDATURE = os.environ.get("LIEN_CANDIDATURE", "")  # peut être ajouté plus tard

# permissions
VIEW, SEND, HISTORY, REACT = 1 << 10, 1 << 11, 1 << 16, 1 << 6
ATTACH, EMBED, CONNECT, SPEAK = 1 << 15, 1 << 14, 1 << 20, 1 << 21
THREAD_SEND, MANAGE_MSG = 1 << 38, 1 << 13
ADMIN = 1 << 3
MGMT = MANAGE_MSG | (1 << 1) | (1 << 40) | (1 << 27) | (1 << 24) | (1 << 22) | 1 | (1 << 7)

OR = 0xC9A86A  # doré Vexa


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    for _ in range(6):
        r = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": f"Bot {TOKEN}", "Content-Type": "application/json",
            "User-Agent": "DiscordBot (vexa-setup, 1.0)"})
        try:
            with urllib.request.urlopen(r) as resp:
                txt = resp.read()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read()).get("retry_after", 1)) + 0.2)
                continue
            raise SystemExit(f"{method} {path} -> {e.code} {e.read().decode()[:300]}")
    raise SystemExit("rate limit")


def ow(id_, allow=0, deny=0):
    return {"id": id_, "type": 0, "allow": str(allow), "deny": str(deny)}


def embed(titre, texte, champs=None):
    e = {"title": titre, "description": texte, "color": OR}
    if champs:
        e["fields"] = [{"name": n, "value": v, "inline": False} for n, v in champs]
    return e


def main():
    guild = req("GET", f"/guilds/{G}")
    owner = guild["owner_id"]

    # 1. nettoyage des salons par défaut
    for c in req("GET", f"/guilds/{G}/channels"):
        req("DELETE", f"/channels/{c['id']}")

    # 2. rôles (créés du plus bas au plus haut)
    trainee = req("POST", f"/guilds/{G}/roles", {"name": "Trainee", "color": 0x95A5A6, "hoist": True, "permissions": "0"})
    chatter = req("POST", f"/guilds/{G}/roles", {"name": "Chatter", "color": 0x57F287, "hoist": True, "permissions": "0"})
    mgmt = req("POST", f"/guilds/{G}/roles", {"name": "Management", "color": 0x5865F2, "hoist": True, "permissions": str(MGMT)})
    boss = req("POST", f"/guilds/{G}/roles", {"name": "👑 Boss", "color": OR, "hoist": True, "permissions": str(ADMIN)})
    req("PUT", f"/guilds/{G}/members/{owner}/roles/{boss['id']}")
    E, T, C, M, B = G, trainee["id"], chatter["id"], mgmt["id"], boss["id"]
    lecture = VIEW | HISTORY | REACT
    staff = [ow(M, VIEW | SEND | HISTORY | ATTACH | EMBED | MANAGE_MSG), ow(B, VIEW | SEND | HISTORY)]

    def cat(nom, overwrites):
        return req("POST", f"/guilds/{G}/channels", {"name": nom, "type": 4, "permission_overwrites": overwrites})

    def salon(nom, parent, overwrites, sujet="", type_=0):
        b = {"name": nom, "type": type_, "parent_id": parent["id"], "permission_overwrites": overwrites}
        if sujet:
            b["topic"] = sujet
        return req("POST", f"/guilds/{G}/channels", b)

    # 3. WELCOME : tout le monde lit, personne n'écrit (sauf staff)
    ow_welcome = [ow(E, lecture, SEND)] + staff
    welcome = cat("Welcome", ow_welcome)
    start = salon("start-here", welcome, ow_welcome, "Start here: how Vexa works.")
    rules = salon("rules", welcome, ow_welcome, "Read the rules before anything else.")
    apply_ = salon("apply", welcome, ow_welcome, "Haven't filled in the Vexa Chatter Application yet? Start here.")
    annonces = salon("announcements", welcome, ow_welcome, "Agency announcements.")

    # 4. TRAINING : Trainee + Chatter
    ow_training = [ow(E, 0, VIEW), ow(T, lecture, SEND), ow(C, lecture, SEND)] + staff
    training = cat("Training", ow_training)
    doc = salon("training-doc", training, ow_training, "Training material and the Vexa Chatter Application.")
    ow_questions = [ow(E, 0, VIEW), ow(T, lecture | SEND | ATTACH | THREAD_SEND), ow(C, lecture | SEND | ATTACH | THREAD_SEND)] + staff
    questions = salon("training-questions", training, ow_questions, "Ask your training questions here.")
    wpm = salon("wpm-test", training, ow_training, "Typing test info. The minimum is 40 WPM.")

    # 5. SHIFT : Chatter
    ow_shift = [ow(E, 0, VIEW), ow(C, lecture, SEND)] + staff
    shift = cat("Shift", ow_shift)
    schedule = salon("schedule", shift, ow_shift, "Shift schedule (Paris time).")

    # 6. VOICE
    ow_voice = [ow(E, 0, VIEW), ow(T, VIEW | CONNECT | SPEAK), ow(C, VIEW | CONNECT | SPEAK),
                ow(M, VIEW | CONNECT | SPEAK), ow(B, VIEW | CONNECT | SPEAK)]
    voice = cat("Voice Channels", ow_voice)
    salon("Shadowing", voice, ow_voice, type_=2)

    # 7. MANAGEMENT (privé : Boss + Management)
    ow_mgmt = [ow(E, 0, VIEW), ow(M, VIEW | SEND | HISTORY | ATTACH | EMBED), ow(B, VIEW | SEND | HISTORY | ATTACH | EMBED)]
    management = cat("Management", ow_mgmt)
    apps = salon("applications", management, ow_mgmt, "New applications from the Vexa Chatter Application arrive here automatically.")
    salon("staff-chat", management, ow_mgmt, "Private chat for management.")

    # 8. messages
    msg = lambda ch, e: req("POST", f"/channels/{ch['id']}/messages", {"embeds": [e]})
    lien = LIEN_CANDIDATURE or "_Link coming soon._"
    msg(start, embed("Welcome to Vexa Agency 👋",
        "Glad to have you here. Follow these steps in order:",
        [("1 · Read the rules", f"Go to <#{rules['id']}> and read everything."),
         ("2 · Training", f"Read <#{doc['id']}>. Ask anything in <#{questions['id']}>."),
         ("3 · Shadowing", "Join the **Shadowing** voice channel to watch an experienced chatter work."),
         ("4 · Shifts", f"Once you're a Chatter, your shifts are posted in <#{schedule['id']}>.")]))
    msg(apply_, embed("Apply to Vexa 📝",
        "Everyone has to fill in the **Vexa Chatter Application** before joining the team, even if you're already on this server.",
        [("What's inside", "Your profile, a location check, the typing test (40 WPM), your experience, your numbers and your results. About 10 minutes."),
         ("Link", lien),
         ("Use Google Chrome", "You'll need to allow location access. Management reviews every application and gets back to you here.")]))
    msg(rules, embed("Vexa Rules 📜", "\n".join([
        "**1.** Be professional and respectful with everyone in the team.",
        "**2.** Everything you see here is confidential. Never share content, screenshots or fan information outside Vexa.",
        "**3.** Show up on time for your shifts. If you can't make it, warn management at least 24 hours before.",
        "**4.** Be online **15 minutes before your shift** for the handover with the previous chatter.",
        "**5.** Follow the scripts and pricing from training. Ask before improvising on prices.",
        "**6.** One account per person. Never share your logins.",
        "**7.** Breaking these rules means removal from the team."])))
    doc_msg = msg(doc, embed("Training doc 📚",
        "Start with the Vexa Chatter Application. It includes your profile, the typing test, your experience and your results.",
        [("Vexa Chatter Application", lien),
         ("After the application", "Management reviews your answers and gets back to you here on Discord.")]))
    msg(wpm, embed("Typing test ⌨️",
        f"The typing test is inside the Vexa Chatter Application (link in <#{doc['id']}>).\n\n"
        "**40 WPM** minimum, **90% accuracy**, 3 attempts.\nYour result is sent to management automatically."))
    msg(schedule, embed("Shifts ⏰", "All times are Paris time.",
        [("🌅 Morning", "06:00 – 14:00"), ("☀️ Afternoon", "14:00 – 22:00"), ("🌙 Night", "22:00 – 06:00")]))
    msg(annonces, embed("Vexa Agency is open 🚀", "Announcements from management will be posted here."))

    # 9. webhook des candidatures (avatar = icône du serveur)
    avatar = None
    if guild.get("icon"):
        try:
            with urllib.request.urlopen(f"https://cdn.discordapp.com/icons/{G}/{guild['icon']}.png?size=256") as r:
                avatar = "data:image/png;base64," + base64.b64encode(r.read()).decode()
        except Exception:
            pass
    wh = req("POST", f"/channels/{apps['id']}/webhooks", {"name": "Vexa Applications", **({"avatar": avatar} if avatar else {})})
    msg(apps, embed("Applications 📋", "Every application from the Vexa Chatter Application lands here: profile, location check, typing score, agency, numbers, shifts and screenshots."))

    print(json.dumps({
        "webhook": f"https://discord.com/api/webhooks/{wh['id']}/{wh['token']}",
        "training_doc": {"channel": doc["id"], "message": doc_msg["id"]},
        "roles": {"boss": B, "management": M, "chatter": C, "trainee": T},
    }))


if __name__ == "__main__":
    main()
