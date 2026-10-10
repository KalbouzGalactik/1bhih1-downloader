# 1BHIH1 — Downloader (téléchargement de playlists sur GitHub Actions)

Dépôt du **téléchargeur** utilisé par l'outil « Downloader » du site 1BHIH1.
Le site n'héberge ni ne télécharge aucun fichier : il met la demande dans une file,
déclenche le workflow de ce dépôt, puis suit son avancement.

```
site (Cloudflare)                     ce dépôt (GitHub)
──────────────────                    ──────────────────────────────────────────────
Outils → Downloader                   .github/workflows/downloader.yml
  └─ POST /api/dl/job  ────────────►   workflow_dispatch (url, ticket, format…)
  └─ GET /api/dl/status ◄──────────      └─ scripts/dl_worker.py
  └─ GET /api/dl/file   ◄──────────          0. supprime ce qui a expiré
        (archive relayée par le Worker)      1. yt-dlp (YouTube) / spotdl (Spotify)
                                              2. range dans Output/<date_heure>_<playlist>/
                                              3. ZIP  →  release GitHub
                                              4. nettoie une dernière fois
                                          .github/queue/<ticket>.json   (l'état)
                                          .github/workflows/nettoyer.yml (facultatif)
```

## Où vont les musiques, et pendant combien de temps

Chaque demande crée **un dossier à son nom** dans `Output/` :

```
Output/
└─ 2026-10-10_14h32_Ma-playlist/            ← date, heure (Bruxelles), nom de la playlist
   ├─ 01 - Premier titre [abc123].mp3
   ├─ 02 - Deuxième titre [def456].mp3
   └─ 2026-10-10_14h32_Ma-playlist.zip      ← l'archive publiée
```

- Le **nom de la playlist** vient de YouTube (dossier créé par yt-dlp) ou de Spotify (nom annoncé par spotdl,
  sinon page publique Spotify) ; à défaut, le dossier s'appelle `Demande <ticket>`.
- **Les musiques sont supprimées 60 minutes après leur création** (réglable : `DL_TTL_MIN`, par ex. `30`).
- Le nettoyage se déclenche **à chaque nouvelle demande**, avant tout téléchargement. Il supprime :
  les dossiers `Output/` expirés, les archives (releases) expirées et leur tag, puis marque leur état
  « expiré » (le site affiche alors « archive supprimée »). Les états de plus de 24 h sont effacés.
- La demande en cours n'est **jamais** touchée.
- En plus, **le site refuse de servir une archive expirée** dès l'heure dite, même si GitHub ne l'a pas encore
  supprimée (rien n'est supprimé tant qu'aucune nouvelle demande n'arrive, à moins d'activer `nettoyer.yml`).

> Sur GitHub Actions, `Output/` vit le temps de l'exécution (la machine est effacée ensuite). Ce qui reste
> d'une demande, c'est son **archive ZIP dans une release**, c'est elle qui est supprimée. Rien n'est
> jamais ajouté au dépôt Git lui-même : il ne grossit pas.

## 1. Mettre ce dépôt en place

1. Crée un dépôt GitHub (public de préférence : les quotas Actions y sont illimités) et dépose **tout le contenu de ce dossier**.
2. **Settings → Actions → General** : autorise les workflows (`permissions: contents: write` est déjà dans les `.yml`).

## 2. Le jeton GitHub (une fois)

GitHub → **Settings** (du compte) → **Developer settings** → **Fine-grained tokens** → *Generate new token* :
dépôt = celui-ci ; **Contents** : Read and write ; **Actions** : Read and write ; **Metadata** : Read.
Copie-le tout de suite : c'est le secret `DOWNLOAD_TOKEN` côté Cloudflare.

## 3. Secrets et variables du dépôt (facultatifs)

**Settings → Secrets and variables → Actions**

| Nom | Type | Valeur | Rôle |
|---|---|---|---|
| `DL_CALLBACK_URL` | Secret | `https://ton-site.pages.dev` | le workflow prévient le site de sa progression |
| `DL_CALLBACK_TOKEN` | Secret | longue chaîne au hasard | même valeur que `DL_TOKEN` côté Cloudflare |
| `DL_TTL_MIN` | **Variable** | `60` (ou `30`) | durée de conservation des musiques, en minutes |
| `YT_COOKIES` | Secret | contenu d'un `cookies.txt` | seulement si YouTube bloque GitHub (voir plus bas) |

## 4. Côté site (Cloudflare)

| Nom | Type | Valeur | Rôle |
|---|---|---|---|
| `DOWNLOAD_TOKEN` | Secret | le jeton de l'étape 2 | déclencher le workflow, lire les états |
| `DL_REPO` | Variable | `toncompte/1bhih1-downloader` | quel dépôt utiliser |
| `DL_WORKFLOW` | Variable | `downloader.yml` | nom du fichier de workflow |
| `DL_BRANCH` | Variable | `main` | branche |
| `DL_TTL_MIN` | Variable | `60` | même valeur que ci-dessus (sert à l'affichage avant la fin) |
| `DL_MAX_PENDING`, `DL_DAILY_MAX`, `DL_ETA_MIN` | Variables | `8`, `30`, `4` | file d'attente et plafonds |
| `DL_TOKEN` | Secret | = `DL_CALLBACK_TOKEN` | suivi en direct |

## 5. Essayer sans rien casser

Aucun réseau ni compte GitHub nécessaire :

```bash
python scripts/dl_worker.py --url "https://www.youtube.com/playlist?list=XXXX" --ticket essai --local --simulation
python scripts/dl_worker.py --purger --local            # ne fait que le nettoyage de Output/
python scripts/tests_dl.py                              # 60 tests
```

Vrai téléchargement en local (yt-dlp et ffmpeg requis) : même commande sans `--simulation`.

## Si les téléchargements échouent sur GitHub

Le journal du workflow (onglet **Actions**) et le message affiché sur le site disent pourquoi.

| Message dans le journal | Cause | Remède |
|---|---|---|
| `Some YouTube downloads require Deno` puis `YT-DLP download error` | YouTube exige un moteur JavaScript depuis novembre 2025 | déjà réglé : le workflow installe **Deno** et `yt-dlp[default]`. Si ça réapparaît, regarde l'étape « Installer Deno » et la ligne `yt-dlp-ejs` des versions |
| `Sign in to confirm you're not a bot` | YouTube bloque les adresses des serveurs GitHub | ajoute des **cookies** (ci-dessous) |
| `YouTube Music returned no usable results` sur presque tous les titres | même blocage, ou limite de requêtes | réessaie plus tard ; sinon cookies |

**Ajouter des cookies YouTube** (seulement si nécessaire) : avec un **compte Google secondaire** (jamais le tien),
connecte-toi à YouTube dans ton navigateur, exporte les cookies au format *Netscape* (extension « Get cookies.txt LOCALLY »),
puis colle le contenu du fichier dans le secret GitHub `YT_COOKIES`. Un compte utilisé ainsi peut être restreint par Google.
Les cookies expirent : à renouveler de temps en temps.

### Si YouTube bloque GitHub

Quand spotdl affiche seulement `YT-DLP download error`, le workflow lance automatiquement une **sonde** : il relance
yt-dlp sur la première vidéo en échec et imprime sa vraie réponse (« Sonde yt-dlp sur la première vidéo en échec »).
C'est cette ligne qui dit pourquoi. Les causes habituelles, de la plus simple à la plus radicale :

1. **Cookies refusés ou expirés** : Google les invalide vite quand ils sont réutilisés depuis un autre pays/serveur.
   Exporte-les depuis une **fenêtre de navigation privée** (connecte-toi à YouTube, exporte, puis ferme la fenêtre sans te déconnecter),
   et recolle-les dans `YT_COOKIES`.
2. **Adresse du serveur bloquée** (« Sign in to confirm you're not a bot », HTTP 403) : YouTube restreint souvent les
   serveurs GitHub, même avec des cookies. Il n'y a alors pas de réglage fiable côté code.
3. **Autres sources audio pour Spotify** (variable de dépôt `DL_AUDIO`, par ex. `soundcloud youtube-music`) : spotdl cherche
   alors d'abord ailleurs que sur YouTube. Le catalogue est plus petit et la qualité varie ; non testé en réel.
4. **Contourner le problème** : exécuter le même workflow sur **ta propre machine** (Settings → Actions → Runners →
   *New self-hosted runner*, puis variable de dépôt `DL_RUNNER` = `self-hosted`). L'adresse est alors la tienne, pas celle
   d'un serveur ; il faut Python, ffmpeg et Deno installés sur cette machine.

## 6. Réglages

| Réglage | Où | Défaut |
|---|---|---|
| Durée de conservation | variable `DL_TTL_MIN` (5 min à 24 h) | 60 min |
| Dossier de sortie | `DL_SORTIE` dans `downloader.yml` | `Output` |
| Fuseau des noms de dossiers | `DL_FUSEAU` | `Europe/Brussels` |
| Taille maximale d'une archive | `DL_ZIP_MAX_MO` | 150 Mo (découpage en parties) |
| Pistes maximales par demande | formulaire du site, plafonné | 200 (500 max) |
| Nettoyage automatique même sans demande | décommenter `schedule` dans `nettoyer.yml` | désactivé |

## 7. Limites réelles

- **Une seule demande tourne à la fois** (`concurrency`). Attention : GitHub ne garde qu'**une demande en attente**
  par groupe ; une troisième arrivée pendant ce temps remplace l'ancienne en attente. Pour une classe, garde
  `DL_MAX_PENDING` bas (1 ou 2).
- **Le démarrage prend 1 à 3 minutes** (GitHub réveille une machine).
- **Quota Actions** : 2 000 min/mois en gratuit pour un dépôt privé, illimité pour un dépôt public.
- **Spotify** : `spotdl` cherche l'audio sur YouTube ; une playlist privée est inaccessible. La limite de pistes
  n'est appliquée qu'à YouTube.
- **YouTube** : vidéos privées, protégées ou bloquées dans le pays échouent (message exact renvoyé au site).
- **Non testé en réel** (pas de réseau ici) : yt-dlp, spotdl, l'API GitHub et la page Spotify sont simulés
  d'après leur format connu. Fais un premier essai sur une petite playlist.
- **Droit d'auteur** : à n'utiliser que pour de la musique que tu as le droit de télécharger.
