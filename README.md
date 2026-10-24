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

Scout pilote Chrome avec son **propre moteur** (`navigateur.py`, aucune dépendance externe). Il ouvre un Chrome séparé, le « Chrome Scout », avec son propre profil dans `~/.scout-chrome`.

- 1re fois : `python3 navigateur.py ouvrir` → connecte-toi au **compte scouting** (pas ta modèle !) dans cette fenêtre. La session reste mémorisée.
- Vérifier : `python3 navigateur.py doctor` doit afficher le compte connecté.

Secours : l'ancien moteur OpenCLI reste utilisable avec `SCOUT_MOTEUR=opencli python3 scout.py ...`. Une copie complète d'OpenCLI 1.8.8 et de son extension est sauvegardée dans `vendor/opencli-1.8.8/`. Si un jour il disparaît d'internet, double-clique `reinstaller.command`.

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

## Vexa — spoofer vidéo & photo

Petite appli (web ou .app locale) pour générer des variantes d'une vidéo ou d'une photo (recadrage, couleurs, grain, métadonnées...) en mode léger ou agressif, avec localisation des métadonnées par pays.

```bash
python3 -m pip install --user flask moviepy numpy pillow opencv-python-headless imageio-ffmpeg piexif
python3 video_spoofer_web.py
```

Ou double-clique `lancer-spoofer.command` (serveur web + tunnel) ou l'app native `Vexa.app` (voir plus bas) pour une utilisation 100% locale, sans navigateur ni réseau. Ouvre http://127.0.0.1:8766/, choisis Vidéo ou Photo, dépose ton fichier, choisis un mode, le nombre de variantes et le pays, télécharge le résultat.

**Pays (métadonnées)** : France, États-Unis, Canada, Australie. Pour une vidéo : langue, position (ISO 6709) et fuseau horaire sur une grande ville du pays choisi. Pour une photo : GPS EXIF, date/heure, fabricant/logiciel randomisés.

**Inverser (miroir)** : désactivé par défaut (décoché) pour ne jamais inverser un texte présent dans le média ; à activer explicitement si besoin.

Ligne de commande directe : `python3 video_spoofer.py ma_video.mp4 --mode leger --locale us` ou `python3 image_spoofer.py ma_photo.jpg --mode agressif --locale fr`.

**Application native** (recommandé) : `./build-vexa-native.sh`, puis ouvre `dist/Vexa.app` (clic droit → Ouvrir la première fois, l'app n'étant pas signée). C'est une vraie fenêtre Mac (Swift + WKWebView, `vexa-native/app/main.swift`) qui démarre et affiche son propre serveur local empaqueté — jamais d'onglet de navigateur, jamais de dépendance à internet. Chaque personne qui l'installe a sa propre vitesse, sans dépendre du réseau de qui que ce soit d'autre.

Limite à garder en tête : ça change l'empreinte technique du fichier (hash, métadonnées, bitrate...), mais Instagram utilise aussi du hachage perceptuel sur l'image/le son — aucune garantie que le mode léger suffise à éviter une détection de contenu dupliqué.
