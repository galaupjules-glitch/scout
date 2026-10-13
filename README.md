# 🔎 Scout — lead-gen Instagram local (zéro token)

Outil qui trouve et pré-qualifie des modèles Instagram automatiquement, **sur ta machine, sans consommer de tokens Claude**. Il pilote le navigateur (via OpenCLI déjà installé), récolte les comptes, filtre sur des règles, et te sort une page de tri visuel.

## Comment ça marche

1. **Récolte** : part de comptes-graines, déroule leurs "Comptes similaires" + la page suggestions d'Instagram, en boucle (effet boule de neige — chaque bon lead trouvé devient une nouvelle graine).
2. **Filtre automatique** (zéro jugement humain nécessaire) :
   - Abonnés dans la fourchette (défaut 1 500–25 000)
   - Ratio abonnements/abonnés (élimine les comptes perso/college)
   - Bio : élimine marques, coachs, nail techs, agences, etc.
   - **Non suivi** par ton compte ni ton perso (= pas déjà contacté)
   - **Engagement : ≥ 500 likes** sur le post le plus récent (élimine les followers fantômes)
3. **Capture** : télécharge 1 screenshot par finaliste dans `resultats/shots/`.
4. **Tri visuel** : génère `resultats/review.html` — une page type Tinder où tu fais Garder/Jeter au clic. Le physique, c'est toi qui juges (1 sec par profil). Export final en CSV.

## Prérequis

- Chrome ouvert avec l'extension OpenCLI connectée, et **connecté au compte scouting** (pas ta modèle !).
- Vérifier : `opencli doctor` doit afficher `Extension: connected`.

## Utilisation

```bash
cd "01 - OFM Agency/scout"
python3 scout.py --target 100
```

Options :
- `--target 100` : nombre de leads à retenir (défaut 50)
- `--min 2000 --max 20000` : fourchette d'abonnés
- `--seeds compte1,compte2` : comptes de départ (sinon liste par défaut)
- `--out resultats` : dossier de sortie

À la fin :
- **`resultats/review.html`** → ouvre-le dans ton navigateur, swipe, exporte tes "gardés"
- `resultats/leads.csv` → tous les candidats retenus avec stats
- `resultats/candidates.json` → données brutes

## Réglages

Tout est en haut de `scout.py` (fourchette abonnés, ratio, mots-clés bio blacklist, likes min, comptes-graines). Modifie et relance.

## Limites

- Instagram throttle après ~90 profils d'affilée : si ça se tarit, laisse reposer quelques heures.
- Les likes masqués par certains comptes → le profil passe quand même (marqué "likes masqués"), à toi de juger au swipe.
- Le jugement du physique reste humain (au swipe) — c'est voulu : c'est gratuit et c'est toi qui juges le mieux.
