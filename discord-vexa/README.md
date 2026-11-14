# Discord Vexa + site de candidature

- `construire_serveur.py` : a construit le serveur Vexa Agency (rôles 👑 Boss / Management / Chatter / Trainee,
  catégories Welcome, Training, Shift, Voice Channels, Management, messages, webhook `#applications`).
- `site/` : version du site de candidature **sans serveur**. Chaque candidature (et chaque arrêt : débutant,
  test de frappe raté) est envoyée dans le salon privé `#applications` avec profil, localisation GPS + ville,
  score de frappe, agence, chiffres, shifts et captures.

Fabriquer le site (le webhook n'est jamais mis dans le dépôt) :

    WEBHOOK=https://discord.com/api/webhooks/... ADMIN=motdepasse python3 site/build.py sortie/

Puis glisser le dossier `sortie/` sur https://app.netlify.com/drop.
Mode admin : ajouter `#admin-<motdepasse>` à la fin du lien.

## Changements faits après la construction (14/11)

- `#start-here` supprimé ; les membres ne peuvent plus créer de fils (`@everyone` sans « Créer des fils »).
- `#apply` déplacé dans **Management** et rendu privé : il contient le lien de candidature à envoyer aux candidats.
- Lien de candidature retiré de `#training-doc` et `#wpm-test` (les membres acceptés ne repassent pas le test).
- Serveur passé en **Communauté** avec l'écran **« accepter les règles »** obligatoire à l'arrivée (reprend les 7 règles de `#rules`).
