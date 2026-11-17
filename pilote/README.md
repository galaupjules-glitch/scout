# 🚀 Pilote — publication auto Drive → Publer + super analyse (cible USA)

Double-clique `lancer-pilote.command` (ou `python3 pilote/pilote.py`) → http://127.0.0.1:8790

## Ce que ça fait
- **Drive** : lit toutes les vidéos d'un dossier. Légende = fichier `.txt` du même nom, sinon description Drive, sinon banque de légendes.
- **Planning** : X publications / jour / compte, jusqu'à 30 jours d'avance, envoyées sur **Publer** (c'est Publer qui publie → ton ordi peut être éteint).
- **Heures auto** : calculées en heure de New York à partir de tes vraies stats + best times Publer + profil d'audience US (quand tu as peu de données), avec écart minimum et variation aléatoire de ± X min.
- **Pas de doublon** : chaque vidéo n'est utilisée qu'une fois (ou une fois par compte en mode « même vidéo partout »).
- **Recyclage** : si le Drive est vide, reposte les vidéos gagnantes (indice ≥ 1.3) après X jours.
- **Autopilote** : toutes les 3 h il complète les créneaux manquants ; une fois par jour il refait l'analyse → les heures s'ajustent toutes seules.
- **Super analyse** : top / flops, heatmap jour × heure (ET), score par heure, meilleurs jours, fréquence idéale, longueur de légende, nb et top hashtags, comptes en progression / en baisse, vidéos Drive gagnantes, recommandations en clair.

## Configuration (onglet Réglages)
1. **Publer** : Settings → Access & Login → API Keys (plan Business/Enterprise). Scopes : workspaces, accounts, posts, media, analytics. Clique « Charger mes workspaces ».
2. **Google Drive** : console.cloud.google.com → activer « Google Drive API » → Identifiants → Créer une clé API. Partage le dossier en « Tous les utilisateurs disposant du lien ».
3. Décoche **Mode démo**, choisis les comptes, « Tester la connexion ».

Les clés sont stockées dans `pilote/data/config.json` (ignoré par git).
