# Site de candidature chatters Vexa

Un lien que tu envoies au candidat. Une page par étape, il ne passe à la suivante que si l'étape est validée.
Toi, tu as une page **rapport** avec tous les dossiers.

## Le parcours du candidat

Le site candidat est en anglais. Ton rapport reste en français.

1. **Profil** : prénom, nom, âge, pays, ville, **partage de localisation obligatoire**, pseudo Discord, langues.
2. **Test de frappe** : un texte en anglais s'affiche (impossible à copier), il le recopie. Le chrono part à la première touche, les lettres deviennent vertes ou rouges en direct. Il faut **40 mots/min** et **90 % de précision**. 3 essais ; s'il rate les 3, la candidature s'arrête.
3. **Expérience** : s'il répond **débutant, on arrête** (message poli).
4. **Agence** : nom, durée, créatrices gérées, CA moyen par shift, meilleur mois, meilleur résultat, pourquoi il part.
5. **Shifts** : Matin 06h–14h, Après-midi 14h–22h, Nuit 22h–06h (heure de Paris), nombre de shifts par semaine.
6. **Résultats** : il ajoute jusqu'à 10 captures d'écran de ses stats.

S'il ferme la page, il reprend là où il s'était arrêté en rouvrant le lien sur le même appareil.

## Ton rapport (`/admin`)

- Compteurs : candidatures, dossiers complets, ≥ 40 mots/min, débutants stoppés, retenus.
- Filtres (Complets, En cours, Débutants, Frappe ratée, Retenus) et recherche.
- Chaque dossier : profil, **lieu déclaré vs position GPS réelle** (lien carte + ville trouvée automatiquement), fuseau horaire et IP, scores de frappe avec alertes (vitesse anormale, tentative de collage), agence et chiffres, shifts, captures (clic pour agrandir).
- Boutons **Retenu / À revoir / Refusé** + note interne.
- **Exporter (CSV)** pour Excel / Numbers.

## Mode admin

Le lien `/#admin-<ta clé>` (affiché au lancement) ouvre la page candidat avec un bandeau pour aller sur n'importe quelle étape et passer le test de frappe.

## Lancer

Double-clique **`lancer-candidature.command`** :
- ton rapport s'ouvre dans le navigateur ;
- si `cloudflared` est installé (`brew install cloudflared`, une seule fois), un lien `https://….trycloudflare.com` s'affiche : **c'est ce lien que tu envoies aux candidats**.

Le site ne marche que tant que la fenêtre est ouverte, et le lien trycloudflare change à chaque relance.
Pour un lien fixe disponible 24h/24, il faut héberger `serveur.py` (Render, Railway, un petit VPS…) avec un disque persistant pour le dossier `donnees/`.

La clé d'accès au rapport est dans `donnees/cle_admin.txt` (ou mets la tienne avec la variable `ADMIN_CLE`).

## Réglages

En haut de `serveur.py` : nom de l'agence, mots/min minimum, précision, nombre d'essais, horaires des shifts, textes du test de frappe.

## Données

Tout reste sur ta machine : `donnees/candidats.db` et `donnees/fichiers/` (les captures). Aucune dépendance à installer (Python seul).
