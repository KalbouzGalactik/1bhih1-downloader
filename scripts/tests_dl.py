#!/usr/bin/env python3
"""Tests du telechargeur 1BHIH1 (aucun reseau, aucun compte GitHub).

    python scripts/tests_dl.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dl_common as C  # noqa: E402

RACINE = Path(__file__).resolve().parent


def dernier_json(texte: str):
    """Renvoie le dernier objet JSON complet trouve dans un texte (ignore le bavardage autour)."""
    decodeur = json.JSONDecoder()
    trouve = None
    i = 0
    while True:
        i = texte.find("{", i)
        if i < 0:
            return trouve
        try:
            obj, fin = decodeur.raw_decode(texte, i)
            if isinstance(obj, dict):
                trouve = obj
            i = fin
        except json.JSONDecodeError:
            i += 1


class TestParseLien(unittest.TestCase):
    def test_youtube_video(self):
        for lien in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                     "https://youtu.be/dQw4w9WgXcQ",
                     "https://www.youtube.com/shorts/dQw4w9WgXcQ",
                     "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
                     "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
                     "dQw4w9WgXcQ"):
            r = C.parse_lien(lien)
            self.assertEqual(r["source"], "youtube", lien)
            self.assertEqual(r["genre"], "video", lien)
            self.assertEqual(r["id"], "dQw4w9WgXcQ", lien)

    def test_youtube_playlist(self):
        r = C.parse_lien("https://www.youtube.com/playlist?list=PLabcdefghijklmnop")
        self.assertEqual((r["source"], r["genre"]), ("youtube", "playlist"))
        self.assertEqual(r["id"], "PLabcdefghijklmnop")

    def test_playlist_avec_video_et_list(self):
        r = C.parse_lien("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijklmnop")
        self.assertEqual(r["genre"], "playlist", "list= doit primer sur v=")

    def test_spotify(self):
        for genre, ident in (("playlist", "37i9dQZF1DXcBWIGoYBM5M"),
                             ("album", "1ATL5GLyefJaxhQzSPVrLX"),
                             ("track", "4cOdK2wGLETKBW3PvgPWqT")):
            r = C.parse_lien(f"https://open.spotify.com/{genre}/{ident}?si=xyz")
            self.assertEqual((r["source"], r["genre"], r["id"]), ("spotify", genre, ident))

    def test_spotify_lien_intl(self):
        r = C.parse_lien("https://open.spotify.com/intl-fr/playlist/37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual((r["source"], r["id"]), ("spotify", "37i9dQZF1DXcBWIGoYBM5M"))

    def test_spotify_uri(self):
        r = C.parse_lien("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual((r["source"], r["id"]), ("spotify", "37i9dQZF1DXcBWIGoYBM5M"))

    def test_erreurs(self):
        for mauvais in ("", "   ", "https://vimeo.com/12345", "https://open.spotify.com/",
                        "https://open.spotify.com/playlist/trop-court",
                        "https://www.youtube.com/watch?v=tropcourt", "pas un lien du tout"):
            with self.assertRaises(ValueError, msg=mauvais):
                C.parse_lien(mauvais)


class TestOptions(unittest.TestCase):
    def test_defauts(self):
        o = C.options("mp3", "", 200)
        self.assertEqual((o["format"], o["qualite"], o["genre"]), ("mp3", "192", "audio"))

    def test_video(self):
        o = C.options("mp4", "720", 50)
        self.assertEqual((o["genre"], o["qualite"]), ("video", "720"))

    def test_wav_sans_qualite(self):
        o = C.options("wav", None, 10)
        self.assertEqual((o["format"], o["qualite"]), ("wav", ""))

    def test_limite_bornee(self):
        self.assertEqual(C.options("mp3", "192", 0)["limite"], 200)
        self.assertEqual(C.options("mp3", "192", 99999)["limite"], 500)
        self.assertEqual(C.options("mp3", "192", 12)["limite"], 12)

    def test_erreurs(self):
        with self.assertRaises(ValueError):
            C.options("ogg", "192", 10)
        with self.assertRaises(ValueError):
            C.options("mp3", "999", 10)
        with self.assertRaises(ValueError):
            C.options("mp4", "abc", 10)


class TestCommandes(unittest.TestCase):
    def test_ytdlp_mp3(self):
        src = C.parse_lien("https://www.youtube.com/playlist?list=PLabcdefghijklmnop")
        f = C.options("mp3", "320", 200)
        args, outil = C.commande(src, f, "/tmp/x")
        self.assertEqual(outil, "yt-dlp")
        self.assertEqual(args[0], "yt-dlp", "la commande doit commencer par le programme a lancer")
        self.assertIn("--audio-format", args)
        self.assertIn("320K", args)
        self.assertIn("--yes-playlist", args)
        self.assertIn("%(playlist_title,title)s", " ".join(args))     # un dossier par playlist
        self.assertNotIn(src["url"], args, "le lien est ajoute par main(), pas par commande()")

    def test_ytdlp_mp4_720(self):
        src = C.parse_lien("https://youtu.be/dQw4w9WgXcQ")
        args, _ = C.commande(src, C.options("mp4", "720", 10), "/tmp/x")
        self.assertIn("bv*[height<=720]+ba/b[height<=720]/b", args)

    def test_ytdlp_mp4_1080_sans_filtre(self):
        src = C.parse_lien("https://youtu.be/dQw4w9WgXcQ")
        args, _ = C.commande(src, C.options("mp4", "1080", 10), "/tmp/x")
        self.assertIn("bv*+ba/b/b", args)

    def test_spotdl(self):
        src = C.parse_lien("https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX")
        args, outil = C.commande(src, C.options("mp3", "192", 200), "/tmp/x")
        self.assertEqual(outil, "spotdl")
        self.assertEqual(args[:2], ["spotdl", "download"])
        self.assertIn("--bitrate", args)
        self.assertIn("192k", args)
        self.assertIn(src["url"], args)


class TestProgression(unittest.TestCase):
    def test_lignes_ytdlp(self):
        self.assertEqual(C.lire_progression("[download] Downloading item 3 of 40"),
                         {"position": 3, "total": 40})
        self.assertEqual(C.lire_progression("__POSITION__:7:12"), {"position": 7, "total": 12})

    def test_ligne_sans_interet(self):
        self.assertIsNone(C.lire_progression("[youtube] dQw4w9WgXcQ: Downloading webpage"))

    def test_lignes_spotdl(self):
        info = C.lire_progression('Downloaded "Titre - Artiste"')
        self.assertEqual(info, {"file": "Titre - Artiste"})
        info = C.lire_progression('Downloaded "Titre - Artiste": https://music.youtube.com/watch?v=abc')
        self.assertEqual(info, {"file": "Titre - Artiste"})

    def test_spotdl_annonce_la_playlist(self):
        info = C.lire_progression("Found 25 songs in Mon Mix du lundi (Playlist)")
        self.assertEqual(info, {"total": 25, "liste": "Mon Mix du lundi"})


class TestSlugEtDuree(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(C.slug("Été 2025 : mes chansons préférées !"), "Ete-2025-mes-chansons-preferees")
        self.assertEqual(C.slug("   "), "sans-nom")
        self.assertLessEqual(len(C.slug("a" * 200)), 60)

    def test_slug_garde_le_souligne_si_demande(self):
        self.assertEqual(C.slug("2026-10-10_14h32_Ma playlist", 80, souligne=True), "2026-10-10_14h32_Ma-playlist")
        self.assertEqual(C.slug("a_b c"), "a-b-c")

    def test_duree(self):
        self.assertEqual(C.duree(45), "45 s")
        self.assertEqual(C.duree(90), "1 min 30 s")
        self.assertEqual(C.duree(3725), "1 h 02 min")
        self.assertEqual(C.hms(3725), "01:02:05")


class TestDecoupage(unittest.TestCase):
    def _fichiers(self, dossier: Path, nombre: int, taille: int) -> list[Path]:
        out = []
        for i in range(nombre):
            p = dossier / f"morceau-{i:02d}.mp3"
            p.write_bytes(b"\x00" * taille)
            out.append(p)
        return out

    def test_un_seul_zip(self):
        with tempfile.TemporaryDirectory() as t:
            racine = Path(t) / "Ma Playlist"
            racine.mkdir()
            items = self._fichiers(racine, 4, 1000)
            zips = C.decouper(items, str(racine), str(Path(t) / "zips"), 10)
            self.assertEqual(len(zips), 1)
            self.assertEqual(zips[0].name, "musique.zip")
            import zipfile
            with zipfile.ZipFile(zips[0]) as z:
                noms = z.namelist()
            self.assertEqual(len(noms), 4)
            self.assertTrue(all(n.startswith("Ma-Playlist/") for n in noms), noms)

    def test_plusieurs_parties(self):
        with tempfile.TemporaryDirectory() as t:
            racine = Path(t) / "Grosse Playlist"
            racine.mkdir()
            items = self._fichiers(racine, 10, 400 * 1024)      # 4 Mo au total
            zips = C.decouper(items, str(racine), str(Path(t) / "zips"), 1)   # limite : 1 Mo
            self.assertGreater(len(zips), 1, "le decoupage doit produire plusieurs parties")
            import zipfile
            total = 0
            for z in zips:
                self.assertRegex(z.name, r"musique-partie-\d+\.zip")
                with zipfile.ZipFile(z) as f:
                    total += len([n for n in f.namelist() if n.endswith(".mp3")])
            self.assertEqual(total, 10, "aucun fichier ne doit etre perdu")

    def test_liste_vide(self):
        with tempfile.TemporaryDirectory() as t:
            racine = Path(t) / "Vide"
            racine.mkdir()
            zips = C.decouper([], str(racine), str(Path(t) / "zips"), 10)
            self.assertEqual(len(zips), 1)


class TestDecoupageNom(unittest.TestCase):
    def test_nom_de_base_personnalise(self):
        with tempfile.TemporaryDirectory() as t:
            racine = Path(t) / "2026-10-10_14h32_Ma-playlist"
            racine.mkdir()
            (racine / "a.mp3").write_bytes(b"\x00" * 100)
            zips = C.decouper(C.compter_fichiers(str(racine), {"mp3"}), str(racine), str(racine), 10, base_nom=racine.name)
            self.assertEqual(zips[0].name, "2026-10-10_14h32_Ma-playlist.zip")
            with zipfile.ZipFile(zips[0]) as z:
                self.assertEqual(z.namelist(), ["2026-10-10_14h32_Ma-playlist/a.mp3"])


class TestDossierOutput(unittest.TestCase):
    def test_horodatage_heure_de_bruxelles(self):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("Europe/Brussels")
        except Exception:
            self.skipTest("base de fuseaux horaires absente")
        ete = datetime(2026, 10, 10, 12, 32, tzinfo=timezone.utc).timestamp()      # UTC+2
        hiver = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc).timestamp()     # UTC+1
        self.assertEqual(C.horodatage(ete), "2026-10-10_14h32")
        self.assertEqual(C.horodatage(hiver), "2026-12-01_13h00")
        self.assertEqual(C.nom_dossier("Été 2025 : mes chansons !", ete), "2026-10-10_14h32_Ete-2025-mes-chansons")

    def test_dossier_unique(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            (base / "x").mkdir()
            (base / "x-2").mkdir()
            self.assertEqual(C.dossier_unique(base, "y").name, "y")
            self.assertEqual(C.dossier_unique(base, "x").name, "x-3")

    def test_duree_de_conservation(self):
        self.assertEqual(C.ttl_min("30"), 30)
        self.assertEqual(C.ttl_min(60), 60)
        self.assertEqual(C.ttl_min("1"), 5, "minimum 5 min")
        self.assertEqual(C.ttl_min("999999"), 1440, "maximum 24 h")
        self.assertEqual(C.ttl_min("pas un nombre"), 60)
        self.assertEqual(C.ttl_min(None), 60)


class TestPurgeLocale(unittest.TestCase):
    def _dossier(self, base: Path, nom: str, age_min: int) -> Path:
        d = base / nom
        d.mkdir(parents=True)
        (d / "a.mp3").write_bytes(b"x")
        t = time.time() - age_min * 60
        os.utime(d / "a.mp3", (t, t))
        os.utime(d, (t, t))
        return d

    def test_supprime_seulement_ce_qui_a_expire(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t) / "Output"
            self._dossier(base, "2026-10-10_10h00_Vieux", 90)
            self._dossier(base, "2026-10-10_11h45_Recent", 10)
            self._dossier(base, "2026-10-10_09h00_Garde", 300)
            self._dossier(base, "_en-cours", 200)
            sup = C.purger_local(base, 60, garder=("2026-10-10_09h00_Garde",))
            self.assertCountEqual(sup, ["2026-10-10_10h00_Vieux", "_en-cours"])
            self.assertEqual(sorted(p.name for p in base.iterdir()), ["2026-10-10_09h00_Garde", "2026-10-10_11h45_Recent"])

    def test_30_minutes(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t) / "Output"
            self._dossier(base, "a", 45)
            self._dossier(base, "b", 20)
            self.assertEqual(C.purger_local(base, 30), ["a"])

    def test_dossier_absent(self):
        self.assertEqual(C.purger_local("/tmp/n-existe-pas-du-tout", 60), [])


class TestDiagnostic(unittest.TestCase):
    def test_cookies_refuses_et_403(self):
        self.assertIn("cookies", C.diagnostic(["WARNING: The provided YouTube account cookies are no longer valid"]).lower())
        self.assertIn("403", C.diagnostic(["ERROR: unable to download video data: HTTP Error 403: Forbidden"]))

    def test_sources_audio_spotdl_reglables(self):
        src = C.parse_lien("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
        os.environ["DL_AUDIO"] = "soundcloud youtube-music"
        try:
            args = C.commande(src, C.options("mp3", "", 5), "/tmp/x")[0]
        finally:
            del os.environ["DL_AUDIO"]
        i = args.index("--audio")
        self.assertEqual(args[i + 1:i + 3], ["soundcloud", "youtube-music"])
        self.assertNotIn("--audio", C.commande(src, C.options("mp3", "", 5), "/tmp/x")[0])

    def test_url_de_la_sonde(self):
        lignes = ["AudioProviderError: YT-DLP download error -", "https://www.youtube.com/watch?v=LSGl3d4KOMk"]
        self.assertEqual(C.YT_URL.findall("\n".join(lignes)), ["https://www.youtube.com/watch?v=LSGl3d4KOMk"])

    def test_annonce_sans_fichier(self):
        d = C.diagnostic(['Downloaded "Sting - Titre":', "https://www.youtube.com/watch?v=abc"])
        self.assertIn("aucun fichier audio", d)

    def test_deno_manquant(self):
        d = C.diagnostic(["Some YouTube downloads require Deno. Run spotdl --download-deno or install Deno", "AudioProviderError: YT-DLP download error"])
        self.assertIn("Deno", d)

    def test_robot(self):
        self.assertIn("YT_COOKIES", C.diagnostic(["ERROR: [youtube] abc: Sign in to confirm you're not a bot"]))

    def test_aucun_resultat(self):
        self.assertIn("ne laisse rien", C.diagnostic(["YouTube Music returned no usable results for x - y after 3 attempts"]))

    def test_inconnu(self):
        self.assertIsNone(C.diagnostic(["tout va bien"]))

    def test_cookies_ajoutes_aux_commandes(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "c.txt"
            f.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            os.environ["DL_COOKIES"] = str(f)
            try:
                yt = C.commande(C.parse_lien("https://youtu.be/dQw4w9WgXcQ"), C.options("mp3", "", 5), "/tmp/x")[0]
                sp = C.commande(C.parse_lien("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"), C.options("mp3", "", 5), "/tmp/x")[0]
            finally:
                del os.environ["DL_COOKIES"]
            self.assertEqual(yt[yt.index("--cookies") + 1], str(f))
            self.assertEqual(sp[sp.index("--cookie-file") + 1], str(f))
        sans = C.commande(C.parse_lien("https://youtu.be/dQw4w9WgXcQ"), C.options("mp3", "", 5), "/tmp/x")[0]
        self.assertNotIn("--cookies", sans)


class FauxGitHub:
    """Remplace dl_common.github : memorise les appels et repond comme l'API."""

    def __init__(self, releases, etats):
        self.releases, self.etats, self.appels = releases, etats, []

    def __call__(self, chemin, methode="GET", donnees=None, corps_brut=None, type_contenu=None, timeout=120):
        self.appels.append((methode, chemin.split("?")[0], donnees))
        base = chemin.split("?")[0]
        if methode == "GET" and base == "/releases":
            return 200, self.releases
        if methode == "DELETE" and (base.startswith("/releases/") or base.startswith("/git/refs/tags/")):
            return 204, None
        if methode == "GET" and base == "/contents/.github/queue":
            return 200, [{"name": n, "path": f".github/queue/{n}", "sha": "s-" + n} for n in self.etats]
        if base.startswith("/contents/.github/queue/"):
            nom = base.rsplit("/", 1)[1]
            if methode == "GET":
                contenu = self.etats.get(nom, "")
                return 200, {"content": base64.b64encode(contenu.encode()).decode(), "sha": "s-" + nom}
            if methode in ("PUT", "DELETE"):
                return 200, {}
        return 404, {}


class TestPublicationRelease(unittest.TestCase):
    """Le ZIP doit partir vers uploads.github.com (adresse `upload_url`), pas vers api.github.com : sinon HTTP 404."""

    def setUp(self):
        self.github, self.requete = C.github, C._requete
        os.environ.update(GITHUB_REPOSITORY="u/r", GH_TOKEN="jeton-secret")

    def tearDown(self):
        C.github, C._requete = self.github, self.requete
        for k in ("GITHUB_REPOSITORY", "GH_TOKEN"):
            os.environ.pop(k, None)

    def test_envoi_vers_uploads_github(self):
        envois = []
        C.github = lambda chemin, methode="GET", donnees=None, **k: (201, {
            "id": 42, "upload_url": "https://uploads.github.com/repos/u/r/releases/42/assets{?name,label}"})

        def faux(url, methode="GET", donnees=None, entetes=None, corps_brut=None, timeout=60):
            envois.append((url, methode, entetes, corps_brut))
            return 201, {"browser_download_url": "https://github.com/u/r/releases/download/dl-x/a%20b.zip"}
        C._requete = faux
        with tempfile.TemporaryDirectory() as t:
            z = Path(t) / "a b.zip"
            z.write_bytes(b"PKcontenu")
            liens = C.publier_release("x", "2026-10-10_14h32_test", [z], 30)
        self.assertEqual(len(envois), 1)
        url, methode, entetes, corps = envois[0]
        self.assertEqual(url, "https://uploads.github.com/repos/u/r/releases/42/assets?name=a%20b.zip")
        self.assertEqual((methode, corps), ("POST", b"PKcontenu"))
        self.assertEqual(entetes["Authorization"], "Bearer jeton-secret")
        self.assertEqual(entetes["Content-Type"], "application/zip")
        self.assertEqual(liens[0]["url"], "https://github.com/u/r/releases/download/dl-x/a%20b.zip")

    def test_adresse_de_secours_sans_upload_url(self):
        envois = []
        C.github = lambda *a, **k: (201, {"id": 7})
        C._requete = lambda url, *a, **k: (envois.append(url), (201, {"browser_download_url": "x"}))[1]
        with tempfile.TemporaryDirectory() as t:
            z = Path(t) / "a.zip"
            z.write_bytes(b"PK")
            C.publier_release("x", "t", [z])
        self.assertTrue(envois[0].startswith("https://uploads.github.com/repos/u/r/releases/7/assets?name=a.zip"))

    def test_refus_de_github_remonte_une_erreur(self):
        C.github = lambda *a, **k: (201, {"id": 7, "upload_url": "https://uploads.github.com/x/assets{?name}"})
        C._requete = lambda *a, **k: (404, {"message": "Not Found"})
        with tempfile.TemporaryDirectory() as t:
            z = Path(t) / "a.zip"
            z.write_bytes(b"PK")
            with self.assertRaises(RuntimeError) as e:
                C.publier_release("x", "t", [z])
            self.assertIn("HTTP 404", str(e.exception))


class TestPurgeGitHub(unittest.TestCase):
    def setUp(self):
        self.reel = C.github

    def tearDown(self):
        C.github = self.reel

    @staticmethod
    def iso(t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_supprime_les_archives_expirees_seulement(self):
        now = 1_800_000_000
        faux = FauxGitHub(
            releases=[
                {"id": 1, "tag_name": "dl-vieux", "published_at": self.iso(now - 2 * 3600)},
                {"id": 2, "tag_name": "dl-recent", "published_at": self.iso(now - 10 * 60)},
                {"id": 3, "tag_name": "v1.0", "published_at": self.iso(now - 5 * 3600)},          # pas une archive du site
                {"id": 4, "tag_name": "dl-courant", "published_at": self.iso(now - 3 * 3600)},    # demande en cours
                {"id": 5, "tag_name": "dl-limite", "published_at": self.iso(now - 50 * 60)},      # 50 min < 60 min
            ],
            etats={})
        C.github = faux
        bilan = C.purger_github(60, garder_ticket="courant", maintenant=now, branche="main")
        suppressions = [(m, c) for m, c, _ in faux.appels if m == "DELETE"]
        self.assertEqual(suppressions, [("DELETE", "/releases/1"), ("DELETE", "/git/refs/tags/dl-vieux")])
        self.assertEqual(bilan["releases"], 1)

    def test_seuil_de_30_minutes(self):
        now = 1_800_000_000
        faux = FauxGitHub(releases=[{"id": 5, "tag_name": "dl-x", "published_at": self.iso(now - 50 * 60)}], etats={})
        C.github = faux
        self.assertEqual(C.purger_github(30, maintenant=now, branche="main")["releases"], 1)

    def test_etats_expires_et_anciens(self):
        now = 1_800_000_000
        faux = FauxGitHub(releases=[], etats={
            "LISEZ-MOI.txt": "x",
            "expire.json": json.dumps({"ticket": "expire", "statut": "done", "expire": now - 60, "maj": now - 3600, "fichiers": [{"nom": "a.zip"}]}),
            "ancien.json": json.dumps({"ticket": "ancien", "statut": "done", "expire": now - 99999, "maj": now - 30 * 3600}),
            "valide.json": json.dumps({"ticket": "valide", "statut": "done", "expire": now + 1200, "maj": now - 600}),
            "courant.json": json.dumps({"ticket": "courant", "statut": "running", "maj": now - 5}),
            "attente.json": json.dumps({"ticket": "attente", "statut": "waiting", "maj": now - 40 * 3600}),
        })
        C.github = faux
        bilan = C.purger_github(60, garder_ticket="courant", maintenant=now, branche="main")
        self.assertEqual(bilan["expires"], 1)
        self.assertEqual(bilan["etats_supprimes"], 1)
        ecrits = [(m, c) for m, c, _ in faux.appels if m in ("PUT", "DELETE")]
        self.assertIn(("PUT", "/contents/.github/queue/expire.json"), ecrits)
        self.assertIn(("DELETE", "/contents/.github/queue/ancien.json"), ecrits)
        touches = " ".join(c for _, c in ecrits)
        for intact in ("valide", "courant", "attente", "LISEZ"):
            self.assertNotIn(intact, touches, f"{intact} ne doit pas etre modifie")
        # l'etat expire est reecrit sans fichiers et marque « expired »
        corps = [d for m, c, d in faux.appels if m == "PUT" and c.endswith("expire.json")][0]
        reecrit = json.loads(base64.b64decode(corps["content"]))
        self.assertEqual((reecrit["statut"], reecrit["fichiers"]), ("expired", []))

    def test_ne_leve_jamais_d_exception(self):
        def casse(*a, **k):
            raise RuntimeError("reseau coupe")
        C.github = casse
        bilan = C.purger_github(60)
        self.assertGreaterEqual(bilan["erreurs"], 1)


class TestSimulationBoutEnBout(unittest.TestCase):
    """Lance vraiment le programme en mode simulation, comme le fera le workflow."""

    def lancer(self, sortie: Path, *extra: str, ticket: str = "test-123"):
        return subprocess.run(
            [sys.executable, str(RACINE / "dl_worker.py"),
             "--url", "https://www.youtube.com/playlist?list=PLabcdefghijklmnop",
             "--ticket", ticket, "--format", "mp3", "--qualite", "192",
             "--sortie", str(sortie), "--local", "--simulation", *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_simulation(self):
        with tempfile.TemporaryDirectory() as t:
            sortie = Path(t) / "Output"
            r = self.lancer(sortie)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("MODE SIMULATION", r.stdout)
            etat = dernier_json(r.stdout)
            self.assertIsNotNone(etat, "aucun JSON d'etat trouve dans la sortie")
            self.assertEqual((etat["statut"], etat["total"], etat["fait"]), ("done", 5, 5))
            self.assertGreater(etat["octets"], 0)
            # dossier « date_heure_playlist » dans Output, avec les morceaux et l'archive a l'interieur
            dossiers = [p for p in sortie.iterdir() if not p.name.startswith(".")]
            self.assertEqual(len(dossiers), 1, [p.name for p in sortie.iterdir()])
            self.assertRegex(dossiers[0].name, r"^\d{4}-\d{2}-\d{2}_\d{2}h\d{2}_Playlist-de-demonstration$")
            self.assertEqual(etat["dossier"], dossiers[0].name)
            self.assertEqual(len(list(dossiers[0].glob("*.mp3"))), 5)
            self.assertEqual(etat["fichiers"][0]["nom"], dossiers[0].name + ".zip")
            self.assertTrue((dossiers[0] / (dossiers[0].name + ".zip")).is_file())
            self.assertFalse((sortie / "_en-cours").exists(), "le dossier de travail doit disparaitre")
            # date d'expiration : fin + 60 min par defaut
            self.assertEqual(etat["expire"] - etat["termine"], 3600)

    def test_duree_de_30_minutes(self):
        with tempfile.TemporaryDirectory() as t:
            etat = dernier_json(self.lancer(Path(t) / "Output", "--ttl-min", "30").stdout)
            self.assertEqual(etat["expire"] - etat["termine"], 1800)

    def test_une_nouvelle_demande_supprime_les_musiques_expirees(self):
        with tempfile.TemporaryDirectory() as t:
            sortie = Path(t) / "Output"
            self.assertEqual(self.lancer(sortie, ticket="premiere").returncode, 0)
            premier = [p for p in sortie.iterdir() if not p.name.startswith(".")][0]
            # elle est encore valide : une 2e demande la laisse en place
            r = self.lancer(sortie, ticket="seconde")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertTrue(premier.exists(), "une demande recente ne doit pas etre supprimee")
            # on la vieillit de 2 h : la 3e demande la supprime (un marqueur prouve qu'il s'agit bien de l'ancien dossier,
            # car la nouvelle demande peut recreer un dossier du meme nom si elle tombe dans la meme minute)
            (premier / "MARQUEUR").write_text("ancien", encoding="utf-8")
            vieux = time.time() - 2 * 3600
            os.utime(premier, (vieux, vieux))
            r = self.lancer(sortie, ticket="troisieme")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertFalse((premier / "MARQUEUR").exists(), "le dossier expire doit etre supprime a la demande suivante")
            self.assertIn("dossier supprime : " + premier.name, r.stdout)

    def test_purge_seule(self):
        with tempfile.TemporaryDirectory() as t:
            sortie = Path(t) / "Output"
            (sortie / "ancien").mkdir(parents=True)
            vieux = time.time() - 3 * 3600
            os.utime(sortie / "ancien", (vieux, vieux))
            r = subprocess.run([sys.executable, str(RACINE / "dl_worker.py"), "--purger", "--local", "--sortie", str(sortie)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((sortie / "ancien").exists())

    def test_lien_invalide(self):
        r = subprocess.run(
            [sys.executable, str(RACINE / "dl_worker.py"), "--url", "https://vimeo.com/1", "--simulation"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 2)
        self.assertIn("YouTube et Spotify", r.stdout)

    def test_sans_lien(self):
        r = subprocess.run([sys.executable, str(RACINE / "dl_worker.py"), "--simulation"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 2)
        self.assertIn("aucun lien", r.stdout.lower())


@unittest.skipIf(os.name == "nt", "faux yt-dlp : script a shebang, Linux/macOS uniquement")
class TestFauxYtDlp(unittest.TestCase):
    """Execute le vrai chemin de telechargement avec un faux « yt-dlp » : verifie la commande lancee,
    le suivi des morceaux, le rangement dans Output et l'archive."""

    FAUX = """#!{python}
import pathlib, sys
a = sys.argv[1:]
pathlib.Path(sys.argv[0]).with_name("appel.txt").write_text(" ".join(a), encoding="utf-8")
P = pathlib.Path(a[a.index("-P") + 1])
d = P / "Ma Playlist de test"
d.mkdir(parents=True, exist_ok=True)
for i in (1, 2, 3):
    print(f"__POSITION__:{{i}}:3", flush=True)
    f = d / f"{{i}} - Titre {{i}} [abc{{i}}].mp3"
    f.write_bytes(b"x" * 2048)
    print(f, flush=True)
"""

    def test_chemin_reel(self):
        with tempfile.TemporaryDirectory() as t:
            bin_ = Path(t) / "bin"
            bin_.mkdir()
            faux = bin_ / "yt-dlp"
            faux.write_text(self.FAUX.format(python=sys.executable), encoding="utf-8")
            faux.chmod(0o755)
            sortie = Path(t) / "Output"
            env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}")
            r = subprocess.run(
                [sys.executable, str(RACINE / "dl_worker.py"), "--url", "https://www.youtube.com/playlist?list=PLabcdefghijklmnop",
                 "--ticket", "reel", "--sortie", str(sortie), "--local"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            appel = (bin_ / "appel.txt").read_text(encoding="utf-8")
            self.assertIn("--yes-playlist", appel)
            self.assertIn("%(playlist_index&{} - |)s", appel, "le numero doit etre separe du titre")
            self.assertIn(f"-P {(sortie / '_en-cours' / 'reel').resolve()}", appel)
            etat = dernier_json(r.stdout)
            self.assertEqual((etat["statut"], etat["titre"], etat["total"]), ("done", "Ma Playlist de test", 3))
            self.assertRegex(etat["dossier"], r"^\d{4}-\d{2}-\d{2}_\d{2}h\d{2}_Ma-Playlist-de-test$")
            dossier = sortie / etat["dossier"]
            self.assertEqual(len(list(dossier.glob("*.mp3"))), 3)
            self.assertTrue((dossier / (etat["dossier"] + ".zip")).is_file())
            self.assertFalse((sortie / "_en-cours" / "reel").exists())

    def test_echec_explique(self):
        """Tous les titres echouent comme dans ton journal : le message d'erreur doit nommer la cause."""
        faux_ko = """#!{python}
import sys
print("Some YouTube downloads require Deno. Run spotdl --download-deno or install Deno", flush=True)
print("AudioProviderError: YT-DLP download error - https://www.youtube.com/watch?v=abc", flush=True)
sys.exit(1)
"""
        with tempfile.TemporaryDirectory() as t:
            bin_ = Path(t) / "bin"
            bin_.mkdir()
            f = bin_ / "yt-dlp"
            f.write_text(faux_ko.format(python=sys.executable), encoding="utf-8")
            f.chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}")
            r = subprocess.run([sys.executable, str(RACINE / "dl_worker.py"), "--url", "https://youtu.be/dQw4w9WgXcQ",
                                "--sortie", str(Path(t) / "Output"), "--local"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn("ECHEC", r.stdout)

    def test_fichiers_ecrits_ailleurs_sont_recuperes(self):
        """Comme dans ton journal : l'outil dit « Downloaded », code 0, mais le fichier n'est pas dans le dossier prevu."""
        faux = """#!{python}
import pathlib, sys
ailleurs = pathlib.Path(sys.argv[0]).parent.parent / "ailleurs"
ailleurs.mkdir(exist_ok=True)
(ailleurs / "Sting - Titre.mp3").write_bytes(b"x" * 4096)
print('Found 1 songs in test (Playlist)', flush=True)
print('Downloaded "Sting - Titre": https://www.youtube.com/watch?v=abc', flush=True)
"""
        with tempfile.TemporaryDirectory() as t:
            bin_ = Path(t) / "bin"
            bin_.mkdir()
            f = bin_ / "spotdl"
            f.write_text(faux.format(python=sys.executable), encoding="utf-8")
            f.chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}")
            r = subprocess.run([sys.executable, str(RACINE / "dl_worker.py"), "--url", "https://open.spotify.com/playlist/44U2CC8OvaUc1HnNLIR9Nn",
                                "--ticket", "spot", "--sortie", str(Path(t) / "Output"), "--local"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("recuperes", r.stdout)
            etat = dernier_json(r.stdout)
            self.assertEqual((etat["statut"], etat["titre"], etat["total"]), ("done", "test", 1))
            self.assertRegex(etat["dossier"], r"^\d{4}-\d{2}-\d{2}_\d{2}h\d{2}_test$")
            self.assertEqual(len(list((Path(t) / "Output" / etat["dossier"]).glob("*.mp3"))), 1)

    def test_sonde_explique_l_echec_de_spotdl(self):
        """Comme ton dernier journal : spotdl dit « YT-DLP download error » sans raison, et aucun fichier n'existe."""
        spotdl = """#!{python}
print('Found 1 songs in test (Playlist)', flush=True)
print('AudioProviderError: YT-DLP download error -', flush=True)
print('https://www.youtube.com/watch?v=LSGl3d4KOMk', flush=True)
"""
        ytdlp = """#!{python}
import sys
print("ERROR: [youtube] LSGl3d4KOMk: Sign in to confirm you're not a bot", file=sys.stderr)
sys.exit(1)
"""
        with tempfile.TemporaryDirectory() as t:
            bin_ = Path(t) / "bin"
            bin_.mkdir()
            for nom, code in (("spotdl", spotdl), ("yt-dlp", ytdlp)):
                f = bin_ / nom
                f.write_text(code.format(python=sys.executable), encoding="utf-8")
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}")
            r = subprocess.run([sys.executable, str(RACINE / "dl_worker.py"), "--url", "https://open.spotify.com/playlist/44U2CC8OvaUc1HnNLIR9Nn",
                                "--ticket", "spot", "--sortie", str(Path(t) / "Output"), "--local"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=t)
            self.assertEqual(r.returncode, 1)
            self.assertIn("Sonde yt-dlp", r.stdout)
            self.assertIn("not a bot", r.stdout)

    def test_outil_absent(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, PATH=str(Path(t)))                   # aucun yt-dlp accessible
            r = subprocess.run(
                [sys.executable, str(RACINE / "dl_worker.py"), "--url", "https://youtu.be/dQw4w9WgXcQ",
                 "--sortie", str(Path(t) / "Output"), "--local"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn("introuvable", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
