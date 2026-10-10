"""Fonctions partagees du telechargeur 1BHIH1.

Bibliotheque standard uniquement pour tout ce qui est reseau et fichiers
(le workflow GitHub installe yt-dlp et spotdl a cote).
Aucune valeur secrete n'est ecrite ici : tout vient de l'environnement.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Petites fonctions utilitaires
# ---------------------------------------------------------------------------

def env(nom: str, defaut: str = "") -> str:
    v = os.environ.get(nom)
    return v if v not in (None, "") else defaut


def slug(txt: str, maxi: int = 60, souligne: bool = False) -> str:
    """Nom de dossier propre : sans accent, sans caractere interdit, sans espace.

    souligne=True conserve les « _ » (utile pour un nom deja compose, ex. 2026-10-10_14h32_Ma-playlist).
    """
    t = unicodedata.normalize("NFKD", txt or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s.-]", "", t, flags=re.UNICODE)
    t = re.sub(r"\s+" if souligne else r"[\s_]+", "-", t).strip("-.")
    t = re.sub(r"-{2,}", "-", t)
    return (t[:maxi].strip("-.") or "sans-nom")


def duree(sec: float | int | None) -> str:
    s = int(sec or 0)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def hms(sec: float | int | None) -> str:
    s = int(sec or 0)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Analyse du lien fourni par l'utilisateur
# ---------------------------------------------------------------------------

YOUTUBE_HOTES = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
SPOTIFY_HOTES = {"open.spotify.com", "play.spotify.com"}
YT_ID = re.compile(r"^[\w-]{11}$")
YT_DANS_URL = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")


def parse_lien(brut: str) -> dict[str, Any]:
    """Reconnait le lien : source (youtube/spotify), genre (video/playlist/album/titre) et identifiant.

    Leve ValueError avec un message lisible si le lien n'est pas utilisable.
    """
    texte = (brut or "").strip()
    if not texte:
        raise ValueError("Colle d'abord un lien YouTube ou Spotify.")
    if texte.startswith("spotify:"):
        texte = "https://open.spotify.com/" + texte.split(":", 1)[1].replace(":", "/")
    if "//" not in texte:
        if YT_ID.match(texte):
            return {"source": "youtube", "genre": "video", "id": texte, "url": f"https://www.youtube.com/watch?v={texte}"}
        texte = "https://" + texte

    u = urllib.parse.urlparse(texte)
    if u.scheme not in ("http", "https"):
        raise ValueError("Le lien doit commencer par https://")
    hote = (u.hostname or "").lower()

    if hote in SPOTIFY_HOTES:
        morceaux = [p for p in u.path.split("/") if p]
        if not morceaux:
            raise ValueError("Lien Spotify incomplet.")
        if morceaux[0].startswith("intl-"):
            morceaux = morceaux[1:]
        if len(morceaux) < 2:
            raise ValueError("Lien Spotify incomplet : il manque le type (playlist, album, titre).")
        genre, ident = morceaux[0], morceaux[1]
        if genre not in ("playlist", "album", "track", "artist"):
            raise ValueError(f"Type Spotify non gere : {genre}. Utilise playlist, album ou track.")
        if not re.fullmatch(r"[A-Za-z0-9]{22}", ident):
            raise ValueError("Identifiant Spotify invalide (22 caracteres attendus).")
        return {"source": "spotify", "genre": genre, "id": ident, "url": f"https://open.spotify.com/{genre}/{ident}"}

    if hote in YOUTUBE_HOTES or hote.endswith(".youtube.com"):
        if hote == "youtu.be" or hote == "www.youtu.be":
            ident = u.path.strip("/").split("/")[0]
            if not YT_ID.match(ident or ""):
                raise ValueError("Lien youtu.be invalide.")
            return {"source": "youtube", "genre": "video", "id": ident, "url": f"https://www.youtube.com/watch?v={ident}"}
        q = urllib.parse.parse_qs(u.query)
        if q.get("list"):
            return {"source": "youtube", "genre": "playlist", "id": q["list"][0], "url": texte}
        if q.get("v"):
            ident = q["v"][0]
            if not YT_ID.match(ident):
                raise ValueError("Identifiant de video YouTube invalide.")
            return {"source": "youtube", "genre": "video", "id": ident, "url": texte}
        m = YT_DANS_URL.search(u.path)
        if m:
            return {"source": "youtube", "genre": "video", "id": m.group(1), "url": texte}
        raise ValueError("Lien YouTube non reconnu (il manque v=... ou list=...).")

    raise ValueError("Seuls les liens YouTube et Spotify sont acceptes.")


# ---------------------------------------------------------------------------
# Options de telechargement
# ---------------------------------------------------------------------------

FORMATS = {
    "mp3": {"genre": "audio", "bitrates": {"128", "192", "320"}, "defaut": "192"},
    "m4a": {"genre": "audio", "bitrates": {"128", "192"}, "defaut": "192"},
    "wav": {"genre": "audio", "bitrates": set(), "defaut": ""},
    "mp4": {"genre": "video", "bitrates": {"360", "480", "720", "1080"}, "defaut": "720"},
}


def options(format_demande: str, qualite: str | None, limite: int) -> dict[str, Any]:
    """Valide le format et la qualite, et renvoie les options normalisees."""
    f = (format_demande or "mp3").lower().strip()
    if f not in FORMATS:
        raise ValueError(f"Format inconnu : {f}. Choix possibles : {', '.join(FORMATS)}.")
    info = FORMATS[f]
    q = (str(qualite) if qualite not in (None, "") else info["defaut"]).strip()
    if info["bitrates"] and q not in info["bitrates"]:
        raise ValueError(f"Qualite invalide pour {f} : {q}. Choix : {', '.join(sorted(info['bitrates']))}.")
    if q and not q.isdigit():
        raise ValueError("La qualite doit etre un nombre.")
    limite = max(1, min(int(limite or 0) or 200, 500))     # garde-fou : au plus 500 pistes
    return {"format": f, "genre": info["genre"], "qualite": q, "limite": limite}


def fichier_cookies() -> str | None:
    """Chemin du fichier de cookies YouTube (facultatif, variable DL_COOKIES) s'il existe."""
    c = env("DL_COOKIES")
    return c if c and Path(c).is_file() else None


def _source_ytdlp(page: str, format: str, qualite: str | None) -> list[str]:
    """Options ligne de commande pour yt-dlp (page web ou playlist YouTube)."""
    cookies = fichier_cookies()
    commun = [
        "yt-dlp", *(["--cookies", cookies] if cookies else []), "--no-warnings", "--newline", "--yes-playlist",
        "--ignore-errors", "--no-abort-on-error", "--retries", "3", "--fragment-retries", "3",
        "--socket-timeout", "30", "--no-progress",
        "--print", "before_dl:__POSITION__:%(playlist_index)s:%(playlist_count)s",
        "--print", "after_move:filepath",
        "-o", "%(playlist_title,title)s/%(playlist_index&{} - |)s%(title)s [%(id)s].%(ext)s",
    ]
    if format == "mp4":
        filtre = "" if qualite == "1080" else f"[height<={qualite}]"
        return commun + [
            "-f", f"bv*{filtre}+ba/b{filtre}/b", "--merge-output-format", "mp4",
            "--embed-metadata", "--embed-thumbnail", "--no-write-thumbnail",
        ]
    if format == "wav":
        return commun + ["-x", "--audio-format", "wav", "--audio-quality", "0"]
    # mp3 et m4a
    return commun + ["-x", "--audio-format", format, "--audio-quality", f"{qualite}K", "--embed-metadata", "--embed-thumbnail", "--no-write-thumbnail"]


def _source_spotdl(url: str, format: str, qualite: str | None, dossier: str) -> list[str]:
    """Options ligne de commande pour spotdl (v4 : sous-commande « download »)."""
    fmt = "mp3" if format in ("mp3", "m4a") else format
    sortie = f"{dossier}/{{artist}} - {{title}}.{{output-ext}}"
    cookies = fichier_cookies()
    base = ["spotdl", "download", url, "--output", sortie, "--format", fmt, *(["--cookie-file", cookies] if cookies else [])]
    audio = env("DL_AUDIO").split()                    # sources audio de spotdl, dans l'ordre (ex. « soundcloud youtube-music »)
    if audio:
        base += ["--audio", *audio]
    if fmt == "wav":
        return base + ["--bitrate", "disabled"]
    return base + ["--bitrate", f"{qualite}k" if qualite else "192k"]


def commande(source: dict[str, Any], fichiers: dict[str, Any], dossier: str) -> tuple[list[str], str]:
    """Construit la commande de telechargement. Renvoie (arguments, outil utilise)."""
    if source["source"] == "spotify":
        return _source_spotdl(source["url"], fichiers["format"], fichiers["qualite"], dossier), "spotdl"
    return _source_ytdlp(source["url"], fichiers["format"], fichiers["qualite"]), "yt-dlp"


# ---------------------------------------------------------------------------
# Suivi des morceaux : lignes de sortie de yt-dlp et comptage des fichiers
# ---------------------------------------------------------------------------
POSITION = re.compile(r"__POSITION__:(\d+):(\d+)")
PROGRESSION_YTDLP = re.compile(r"\[download\]\s+Downloading item (\d+) of (\d+)")
PROGRESSION_SPOTDL = re.compile(r'Downloaded\s+"([^"]+)"|Downloaded\s+(\S.*?)\s*$')
TROUVES_SPOTDL = re.compile(r"Found\s+(\d+)\s+songs?\s+in\s+(.+?)\s+\((?:Playlist|Album|Artist|Saved|Track|Song)", re.IGNORECASE)


def lire_progression(ligne: str) -> dict[str, Any] | None:
    """Reconnait une ligne de progression. Renvoie None si rien, sinon les infos trouvees."""
    m = POSITION.search(ligne)
    if m:
        return {"position": int(m.group(1)), "total": int(m.group(2))}
    m = PROGRESSION_YTDLP.search(ligne)
    if m:
        return {"position": int(m.group(1)), "total": int(m.group(2))}
    m = TROUVES_SPOTDL.search(ligne)
    if m:
        return {"total": int(m.group(1)), "liste": m.group(2).strip()}
    m = PROGRESSION_SPOTDL.search(ligne)
    if m:
        return {"file": (m.group(1) or m.group(2)).strip()}
    return None


def diagnostic(lignes: list[str]) -> str | None:
    """Explication lisible d'un echec de YouTube, d'apres les dernieres lignes du journal (None si inconnu)."""
    t = "\n".join(lignes).lower()
    if "no longer valid" in t or "invalid netscape" in t or "cookies file" in t and "error" in t:
        return ("Les cookies YouTube fournis sont refuses (invalides, mal copies ou deja expires). "
                "Re-exporte-les et recolle-les dans le secret YT_COOKIES (voir le README).")
    if any(l.lstrip().lower().startswith("downloaded") for l in lignes):
        return ("l'outil annonce avoir telecharge, mais aucun fichier audio n'a ete trouve "
                "(probleme de dossier de sortie ou de conversion ffmpeg : voir le journal).")
    if "require deno" in t or "javascript runtime" in t or "no supported javascript" in t:
        return ("YouTube exige l'outil Deno sur le serveur (etape « Installer Deno » du workflow) et le paquet "
                "yt-dlp[default]. Verifie que l'etape a bien tourne.")
    if "not a bot" in t or "sign in to confirm" in t or "cookies-from-browser" in t:
        return ("YouTube refuse les serveurs GitHub (« confirme que tu n'es pas un robot »). "
                "Ajoute des cookies : secret YT_COOKIES du depot (voir le README).")
    if "http error 403" in t or "po token" in t or "sabr" in t:
        return ("YouTube refuse le flux audio a ce serveur (HTTP 403 / PO token). Ce blocage touche souvent les "
                "serveurs GitHub : voir le README, section « Si YouTube bloque GitHub ».")
    if "no usable results" in t or "no results found" in t or "download error" in t:
        return ("YouTube ne laisse rien telecharger depuis ce serveur (blocage ou limite de requetes). "
                "Reessaie plus tard, ou ajoute des cookies (secret YT_COOKIES).")
    return None


YT_URL = re.compile(r"https://(?:www\.)?youtube\.com/watch\?v=[\w-]{11}")


def sonde_youtube(lignes: list[str], timeout: int = 120) -> list[str]:
    """Relance yt-dlp (sans rien telecharger) sur la premiere video citee dans le journal, pour obtenir la vraie
    raison de l'echec : spotdl ne montre que « YT-DLP download error ». Renvoie les dernieres lignes."""
    import subprocess
    urls = YT_URL.findall("\n".join(lignes))
    if not urls:
        return []
    cookies = fichier_cookies()
    cmd = ["yt-dlp", "--simulate", "--no-warnings", *(["--cookies", cookies] if cookies else []), urls[0]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        sortie = (r.stdout + "\n" + r.stderr).splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        sortie = [f"sonde impossible : {e}"]
    return [l for l in sortie if l.strip()][-25:]


def retrouver_fichiers(depuis: float, racines: list[Path], extensions: set[str]) -> list[Path]:
    """Fichiers audio/video apparus depuis `depuis` (epoch) ailleurs que prevu.

    Filet de securite : si un outil ecrit hors du dossier de travail demande (chemin reinterprete, dossier
    cache ignore...), on retrouve ses fichiers dans les dossiers `racines` au lieu de conclure a un echec.
    """
    trouves: list[Path] = []
    ignores = {".git", "node_modules", "__pycache__"}
    for racine in racines:
        if not racine.is_dir():
            continue
        for p in racine.rglob("*"):
            try:
                if (p.is_file() and p.suffix.lower().lstrip(".") in extensions
                        and p.stat().st_mtime >= depuis - 5 and not (ignores & set(p.parts))):
                    trouves.append(p)
            except OSError:
                continue
    return sorted(set(trouves))


def compter_fichiers(dossier: str, extensions: set[str]) -> list[Path]:
    """Liste les fichiers telecharges, tries par nom."""
    racine = Path(dossier)
    if not racine.is_dir():
        return []
    out = [p for p in racine.rglob("*") if p.is_file() and p.suffix.lower().lstrip(".") in extensions]
    return sorted(out, key=lambda p: str(p).lower())


# ---------------------------------------------------------------------------
# Archive ZIP, decoupee si elle depasse la taille maximale acceptee
# ---------------------------------------------------------------------------

def decouper(items: list[Path], racine: str, dossier_zip: str, max_mo: int, base_nom: str = "musique") -> list[Path]:
    """Fabrique un ou plusieurs ZIP dans dossier_zip. Renvoie la liste des ZIP crees.

    Un seul ZIP si le total tient sous max_mo, sinon plusieurs parties numerotees
    (limite GitHub : 2 Go par fichier d'une release, on reste bien en dessous).
    """
    limite = max(1, max_mo) * 1024 * 1024
    marge = limite * 9 // 10                  # marge : la compression fait varier la taille
    racine_path = Path(racine)
    dossier_zip_path = Path(dossier_zip)
    dossier_zip_path.mkdir(parents=True, exist_ok=True)

    total = sum(p.stat().st_size for p in items)
    if total <= limite or not items:
        groupes = [items]
    else:
        groupes = [[]]
        taille = 0
        for p in items:
            s = p.stat().st_size
            if groupes[-1] and taille + s > marge:
                groupes.append([])
                taille = 0
            groupes[-1].append(p)
            taille += s

    base_nom = slug(base_nom, 100, souligne=True)
    noms = [f"{base_nom}.zip"] if len(groupes) == 1 else [f"{base_nom}-partie-{i}.zip" for i in range(1, len(groupes) + 1)]
    for nom, grp in zip(noms, groupes):
        with zipfile.ZipFile(dossier_zip_path / nom, "w", zipfile.ZIP_DEFLATED) as z:
            for p in grp:
                z.write(p, Path(slug(racine_path.name, 100, souligne=True)) / p.relative_to(racine_path).as_posix())
    return [dossier_zip_path / n for n in noms]


# ---------------------------------------------------------------------------
# Envoi vers GitHub (API REST) et vers le site
# ---------------------------------------------------------------------------

def _requete(url: str, methode: str = "GET", donnees: Any = None, entetes: dict[str, str] | None = None,
             corps_brut: Any = None, timeout: int = 60) -> tuple[int, Any]:
    h = {"User-Agent": "1BHIH1-downloader", "Accept": "application/vnd.github+json"}
    h.update(entetes or {})
    data = None
    if donnees is not None:
        data = json.dumps(donnees).encode()
        h["Content-Type"] = "application/json"
    elif corps_brut is not None:
        data = corps_brut
    req = urllib.request.Request(url, data=data, headers=h, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            brut = r.read()
            try:
                return r.status, json.loads(brut or b"null")
            except json.JSONDecodeError:
                return r.status, brut.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        brut = e.read()
        try:
            return e.code, json.loads(brut or b"null")
        except json.JSONDecodeError:
            return e.code, brut.decode("utf-8", "replace")


def github(chemin: str, methode: str = "GET", donnees: Any = None, corps_brut: Any = None,
           type_contenu: str | None = None, timeout: int = 120) -> tuple[int, Any]:
    """Appel a l'API GitHub. chemin commence par /, ex. /repos/{proprietaire}/{depot}/..."""
    depot = env("GITHUB_REPOSITORY")
    jeton = env("GH_TOKEN") or env("GITHUB_TOKEN")
    if not depot or not jeton:
        raise RuntimeError("GITHUB_REPOSITORY ou GH_TOKEN manquant dans l'environnement.")
    entetes = {"Authorization": f"Bearer {jeton}", "X-GitHub-Api-Version": "2022-11-28"}
    if type_contenu:
        entetes["Content-Type"] = type_contenu
    return _requete(f"https://api.github.com/repos/{depot}{chemin}", methode, donnees, entetes, corps_brut, timeout)


def ecrire_fichier_depot(chemin: str, contenu: str, message: str, branche: str | None = None) -> bool:
    """Cree ou met a jour un fichier du depot (sert a publier l'etat d'une demande)."""
    import base64
    branche = branche or env("GITHUB_REF_NAME", "main")
    code, actu = github(f"/contents/{urllib.parse.quote(chemin)}?ref={urllib.parse.quote(branche)}")
    sha = actu.get("sha") if isinstance(actu, dict) else None
    corps = {
        "message": message,
        "content": base64.b64encode(contenu.encode()).decode(),
        "branch": branche,
    }
    if sha:
        corps["sha"] = sha
    code, rep = github(f"/contents/{urllib.parse.quote(chemin)}", "PUT", corps)
    return 200 <= code < 300


def publier_release(ticket: str, titre: str, fichiers: list[Path], ttl_min_: int | None = None) -> list[dict[str, Any]]:
    """Cree une release GitHub et y depose les ZIP. Renvoie les liens de telechargement."""
    tag = f"dl-{ticket}"
    duree_txt = f"{ttl_min_} min" if ttl_min_ else "un court delai"
    code, rel = github("/releases", "POST", {
        "tag_name": tag,
        "name": titre[:120],
        "body": "Archive generee automatiquement par le telechargeur 1BHIH1.\n\n"
                f"Elle est supprimee automatiquement {duree_txt} apres sa creation "
                "(au plus tard a la demande suivante). Telecharge-la depuis le site.",
        "draft": False,
        "prerelease": False,
    })
    if code == 422:                                  # release deja existante
        code, rel = github(f"/releases/tags/{tag}")
    if not (200 <= code < 300) or not isinstance(rel, dict):
        raise RuntimeError(f"Creation de la release impossible (HTTP {code}) : {str(rel)[:200]}")

    # Les fichiers d'une release ne s'envoient PAS vers api.github.com mais vers uploads.github.com :
    # GitHub donne l'adresse exacte dans `upload_url` (modele « .../assets{?name,label} »).
    base_envoi = str(rel.get("upload_url") or "").split("{")[0] or \
        f"https://uploads.github.com/repos/{env('GITHUB_REPOSITORY')}/releases/{rel['id']}/assets"
    jeton = env("GH_TOKEN") or env("GITHUB_TOKEN")
    liens = []
    for f in fichiers:
        with open(f, "rb") as fh:
            contenu = fh.read()
        code, act = _requete(f"{base_envoi}?name={urllib.parse.quote(f.name)}", "POST", corps_brut=contenu, timeout=900,
                             entetes={"Authorization": f"Bearer {jeton}", "X-GitHub-Api-Version": "2022-11-28",
                                      "Content-Type": "application/zip"})
        if 200 <= code < 300 and isinstance(act, dict):
            liens.append({"nom": f.name, "taille": f.stat().st_size, "url": act.get("browser_download_url", "")})
        else:
            raise RuntimeError(f"Envoi de {f.name} refuse (HTTP {code}) : {str(act)[:200]}")
    return liens


def titre_spotify(url: str, timeout: int = 10) -> str | None:
    """Nom d'une playlist, d'un album ou d'un titre Spotify (oEmbed public, sans cle). None si indisponible."""
    try:
        code, rep = _requete("https://open.spotify.com/oembed?url=" + urllib.parse.quote(url, safe=""),
                             "GET", None, {"Accept": "application/json"}, timeout=timeout)
        if 200 <= code < 300 and isinstance(rep, dict):
            return (str(rep.get("title") or "").strip()) or None
    except Exception:
        pass
    return None


def prevenir_le_site(chemin: str, charge: dict[str, Any]) -> bool:
    """Envoie un evenement au site (facultatif : sans les variables, on ne fait rien)."""
    base = env("DL_CALLBACK_URL")
    jeton = env("DL_CALLBACK_TOKEN")
    if not base:
        return False
    try:
        code, _ = _requete(base.rstrip("/") + chemin, "POST", charge,
                           {"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"}, timeout=20)
        return 200 <= code < 300
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Etat d'une demande de telechargement
# ---------------------------------------------------------------------------

class Etat:
    """Ecrit l'etat de la demande dans le depot (queue/<ticket>.json) pour que le site le lise."""

    STATUTS = {"waiting", "running", "done", "failed"}

    def __init__(self, ticket: str, url: str, format: str, qualite: str, dossier_queue: str = ".github/queue"):
        self.ticket = ticket
        self.dossier = Path(dossier_queue)
        self.debut = time.time()
        self.data: dict[str, Any] = {
            "ticket": ticket,
            "statut": "running",
            "lien": url,
            "format": format,
            "qualite": qualite,
            "debut": int(self.debut),
            "maj": int(self.debut),
            "titre": None,
            "auteur": None,
            "total": None,
            "fait": 0,
            "octets": 0,
            "dossier": None,        # nom du dossier de sortie (date + heure + playlist)
            "expire": None,         # instant (epoch, secondes) apres lequel l'archive est supprimee
            "fichiers": [],
            "erreur": None,
        }
        self.publie = 0.0
        self.actif = True          # False : rien n'est envoye (mode local, simulation, tests)

    def maj(self, **champs: Any) -> None:
        self.data.update(champs)
        self.data["maj"] = int(time.time())

    @property
    def contenu(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)

    def publier(self, message: str = "", force: bool = False) -> bool:
        """Ecrit l'etat dans le depot et previent le site. Au plus une fois par 20 s."""
        if not self.actif:
            return False
        maintenant = time.time()
        if not force and maintenant - self.publie < 20:
            return False
        self.publie = maintenant
        self.data["maj"] = int(maintenant)
        chemin = f"{self.dossier.as_posix()}/{self.ticket}.json"
        ok = False
        try:
            ok = ecrire_fichier_depot(chemin, self.contenu, message or f"etat {self.ticket} : {self.data['statut']}")
        except Exception as e:                        # le suivi ne doit jamais faire echouer le telechargement
            print(f"  (etat GitHub non publie : {e})")
        prevenir_le_site("/api/dl/progress", {
            "ticket": self.ticket, "statut": self.data["statut"], "fait": self.data["fait"],
            "total": self.data["total"], "titre": self.data["titre"], "octets": self.data["octets"],
            "erreur": self.data["erreur"], "dossier": self.data["dossier"], "expire": self.data["expire"],
        })
        return ok


# ---------------------------------------------------------------------------
# Dossier de sortie « Output » : un sous-dossier par demande, nomme date + heure + playlist
# ---------------------------------------------------------------------------

SORTIE_DEFAUT = "Output"
FUSEAU_DEFAUT = "Europe/Brussels"
TTL_DEFAUT_MIN = 60
ETAT_HEURES_DEFAUT = 24


def horodatage(ts: float | None = None, fuseau: str | None = None) -> str:
    """Date et heure pour un nom de dossier : 2026-10-10_14h32 (fuseau de la classe, pas celui du serveur)."""
    ts = time.time() if ts is None else ts
    try:
        from zoneinfo import ZoneInfo
        d = datetime.fromtimestamp(ts, ZoneInfo(fuseau or env("DL_FUSEAU", FUSEAU_DEFAUT)))
    except Exception:                                  # tzdata absent (Windows) : on retombe sur UTC
        d = datetime.fromtimestamp(ts, timezone.utc)
    return d.strftime("%Y-%m-%d_%Hh%M")


def nom_dossier(titre: str, ts: float | None = None) -> str:
    """Nom du dossier d'une demande : « 2026-10-10_14h32_Nom-de-la-playlist »."""
    return f"{horodatage(ts)}_{slug(titre, 50)}"


def dossier_unique(base: Path, nom: str) -> Path:
    """base/nom, ou base/nom-2, nom-3... si le dossier existe deja."""
    cible, n = base / nom, 2
    while cible.exists():
        cible = base / f"{nom}-{n}"
        n += 1
    return cible


def ttl_min(valeur: str | int | None = None) -> int:
    """Duree de conservation des musiques, en minutes (DL_TTL_MIN). Bornee entre 5 min et 24 h ; defaut 60."""
    brut = valeur if valeur not in (None, "") else env("DL_TTL_MIN", str(TTL_DEFAUT_MIN))
    try:
        v = int(float(brut))
    except (TypeError, ValueError):
        v = TTL_DEFAUT_MIN
    return max(5, min(v, 24 * 60))


def purger_local(base: str | Path, ttl_minutes: int, maintenant: float | None = None,
                 garder: tuple[str, ...] = ()) -> list[str]:
    """Supprime de `base` (le dossier Output) tout ce qui est plus vieux que la duree de conservation.

    `garder` : noms a ne jamais toucher (la demande en cours). Renvoie les noms supprimes.
    """
    racine = Path(base)
    if not racine.is_dir():
        return []
    limite = (time.time() if maintenant is None else maintenant) - ttl_minutes * 60
    supprimes: list[str] = []
    for p in sorted(racine.iterdir()):
        if p.name in garder:
            continue
        try:
            if p.stat().st_mtime >= limite:
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            supprimes.append(p.name)
        except OSError as e:
            print(f"  (purge : {p.name} non supprime : {e})")
    return supprimes


def _epoch_github(iso: str | None) -> float | None:
    try:
        return datetime.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def purger_github(ttl_minutes: int, garder_ticket: str = "", maintenant: float | None = None,
                  dossier_etat: str = ".github/queue", branche: str | None = None,
                  heures_etat: int | None = None) -> dict[str, int]:
    """Supprime sur GitHub ce qui a depasse la duree de conservation. Ne leve jamais d'exception.

      1. les releases `dl-*` (archives ZIP) et leur tag, plus vieilles que `ttl_minutes` ;
      2. les etats `done` expires : ils passent a `expired` (le site affiche « supprime ») ;
      3. les vieux fichiers d'etat (termines depuis plus de `heures_etat` h, 24 par defaut).

    La demande en cours (`garder_ticket`) n'est jamais touchee.
    """
    now = time.time() if maintenant is None else maintenant
    branche = branche or env("GITHUB_REF_NAME", "main")
    heures_etat = heures_etat if heures_etat is not None else int(env("DL_ETAT_HEURES", str(ETAT_HEURES_DEFAUT)) or ETAT_HEURES_DEFAUT)
    bilan = {"releases": 0, "expires": 0, "etats_supprimes": 0, "erreurs": 0}
    q = urllib.parse.quote

    try:
        code, rels = github("/releases?per_page=100")
        for r in (rels if 200 <= code < 300 and isinstance(rels, list) else []):
            tag = str(r.get("tag_name") or "")
            if not tag.startswith("dl-") or tag == f"dl-{garder_ticket}":
                continue
            # `created_at` d'une release est la date du commit, pas celle de la publication : on lit `published_at`.
            cree = _epoch_github(r.get("published_at") or r.get("created_at"))
            if cree is None or cree > now - ttl_minutes * 60:
                continue
            c1, _ = github(f"/releases/{r['id']}", "DELETE")
            github(f"/git/refs/tags/{q(tag)}", "DELETE")                  # le tag reste sinon dans le depot
            if c1 in (200, 204):
                bilan["releases"] += 1
            else:
                bilan["erreurs"] += 1
    except Exception as e:
        print(f"  (purge des releases impossible : {e})")
        bilan["erreurs"] += 1

    try:
        code, liste = github(f"/contents/{q(dossier_etat)}?ref={q(branche)}")
        for f in (liste if 200 <= code < 300 and isinstance(liste, list) else []):
            nom = str(f.get("name", ""))
            if not nom.endswith(".json") or nom == f"{garder_ticket}.json":
                continue
            c, doc = github(f"/contents/{q(f['path'])}?ref={q(branche)}")
            if not (200 <= c < 300 and isinstance(doc, dict)):
                continue
            try:
                etat = json.loads(base64.b64decode(doc.get("content", "")).decode("utf-8"))
            except Exception:
                continue
            statut, maj, expire = etat.get("statut"), etat.get("maj") or 0, etat.get("expire")
            if statut in ("done", "expired", "failed") and maj and maj < now - heures_etat * 3600:
                c2, _ = github(f"/contents/{q(f['path'])}", "DELETE",
                               {"message": f"purge : etat {nom} expire", "sha": doc.get("sha"), "branch": branche})
                bilan["etats_supprimes" if c2 in (200, 204) else "erreurs"] += 1
            elif statut == "done" and expire and expire <= now:
                etat.update(statut="expired", fichiers=[], journal="", maj=int(now))
                ok = ecrire_fichier_depot(f["path"], json.dumps(etat, ensure_ascii=False, indent=2),
                                          f"purge : {etat.get('ticket', nom)} expire", branche)
                bilan["expires" if ok else "erreurs"] += 1
    except Exception as e:
        print(f"  (purge des etats impossible : {e})")
        bilan["erreurs"] += 1
    return bilan


# ---------------------------------------------------------------------------
# Execution d'une commande avec lecture de la sortie en direct
# ---------------------------------------------------------------------------

def executer(args: list[str], dossier: str, sur_ligne=None) -> int:
    """Lance une commande, transmet chaque ligne a sur_ligne, renvoie le code de sortie."""
    import subprocess
    print(f"  $ {' '.join(args[:6])}{' ...' if len(args) > 6 else ''}", flush=True)
    proc = subprocess.Popen(args, cwd=dossier, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for ligne in proc.stdout:
        ligne = ligne.rstrip()
        if ligne:
            print("    " + ligne, flush=True)
            if sur_ligne:
                try:
                    sur_ligne(ligne)
                except Exception:
                    pass
    return proc.wait()


def espace_libre_mo(chemin: str = ".") -> int:
    try:
        return shutil.disk_usage(chemin).free // (1024 * 1024)
    except Exception:
        return 0
