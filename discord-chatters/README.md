# 💬 Bot Discord — recrutement des chatters

Un candidat clique sur **Postuler** → un **fil privé** s'ouvre et le bot le guide :

| # | Étape | Ce que tu récupères |
|---|-------|---------------------|
| 1 | Présentation | prénom, nom, âge, pays/ville, langues + niveau d'anglais |
| 2 | Test de localisation | ses réponses à 3 messages de fans en anglais (slang US) |
| 3 | Test de frappe | texte affiché **en image** (pas de copier-coller), chrono → **mots/min** + **précision**. Objectif 40 mots/min, 2 essais |
| 4 | Expérience | **débutant → on arrête** poliment |
| 5 | Agence | nom de l'agence, durée, créatrices gérées, **chiffres** (par shift / mois), raison du départ |
| 6 | Shifts | matin / après-midi / nuit (plusieurs choix possibles) |
| 7 | Résultats | jusqu'à 10 captures d'écran envoyées dans le fil |

À la fin, le **dossier complet** (+ captures) arrive dans ton salon staff avec **Accepter / Refuser**.
Accepter = rôle Chatter donné + message de bienvenue dans son fil. Refuser = message poli. Le fil est archivé.

## Installation (une fois)

1. **Créer le bot** : https://discord.com/developers/applications → *New Application* → onglet **Bot** :
   - *Reset Token* → copie le token
   - active **MESSAGE CONTENT INTENT** (obligatoire pour le test de frappe)
2. **L'inviter** : onglet *OAuth2 > URL Generator* → scopes `bot` + `applications.commands`, permissions :
   Envoyer des messages, Créer des fils privés, Envoyer des messages dans les fils, Gérer les fils,
   Joindre des fichiers, Ajouter des réactions, Gérer les rôles. Ouvre le lien et choisis ton serveur.
3. **Sur ton serveur** : un salon `#candidature` (visible par tous, écriture fermée), un salon `#dossiers-staff`
   (privé), un rôle `Chatter`. Le rôle du bot doit être **au-dessus** du rôle Chatter.
4. Copie `.env.example` en `.env` et remplis-le (Discord > Paramètres > Avancés > Mode développeur,
   puis clic droit > *Copier l'identifiant*).
5. Double-clique `lancer-bot.command` (ou `pip install -r requirements.txt && python3 bot.py`).
6. Dans `#candidature`, tape `/panneau` → le message avec le bouton **Postuler** apparaît.

## Commandes staff

- `/panneau` — publie le bouton Postuler dans le salon
- `/candidat @membre` — voir son dossier
- `/reset-candidat @membre` — efface sa candidature (il peut repostuler)
- `/export-candidats` — tout en CSV (s'ouvre dans Excel / Numbers)

## Réglages

Tout est dans `config.py` : textes du test de frappe, messages de fans, horaires des shifts,
seuil de mots/min, nombre d'essais, arrêter ou non si le test de frappe est raté.
Pour changer l'ordre des étapes : liste `ORDRE` en haut de `bot.py`.

Les données restent sur ta machine : `candidats.db` + dossier `uploads/`.
Le bot ne tourne que quand `lancer-bot.command` est ouvert (pour du 24/7, il faudra un petit serveur).
