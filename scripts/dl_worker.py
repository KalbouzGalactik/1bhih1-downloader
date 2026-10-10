#!/usr/bin/env python3
"""Telechargeur 1BHIH1 : une demande = un dossier daté dans « Output » + une archive ZIP sur GitHub.

Ce programme tourne dans GitHub Actions (voir .github/workflows/downloader.yml),
declenche par le site. Il :

  0. nettoie d'abord ce qui a depasse la duree de conservation (30 min / 1 h, voir DL_TTL_MIN) ;
  1. lit le lien fourni (YouTube ou Spotify) et le format demande ;
  2. telecharge avec yt-dlp (YouTube) ou spotdl (Spotify) ;
  3. range le resultat dans  Output/<AAAA-MM-JJ_HHhMM>_<nom-de-la-playlist>/ ;
  4. fabrique une archive ZIP (decoupee si elle est trop grosse) ;
  5. depose l'archive dans une « release » GitHub et publie les liens, avec l'heure d'expiration.

Utilisation locale (sans GitHub) :
    python scripts/dl_worker.py --url "https://www.youtube.com/playlist?list=..." --local --simulation
    python scripts/dl_worker.py --purger --local          # ne fait que le nettoyage
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dl_common as C  # noqa: E402

EXTENSIONS = {"mp3", "m4a", "wav", "opus", "flac", "webm", "mp4", "mkv"}


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Telechargeur 1BHIH1 (yt-dlp + spotdl)")
    p.add_argument("--url", default=C.env("DL_URL"), help="lien YouTube ou Spotify")
    p.add_argument("--ticket", default=C.env("DL_TICKET"), help="identifiant de la demande")
    p.add_argument("--format", default=C.env("DL_FORMAT", "mp3"), help="mp3, m4a, wav ou mp4")
    p.add_argument("--qualite", default=C.env("DL_QUALITE", ""), help="128/192/320 (audio) ou 360/480/720/1080 (video)")
    p.add_argument("--limite", type=int, default=int(C.env("DL_LIMITE", "200") or 200), help="nombre maximal de pistes")
    p.add_argument("--sortie", default=C.env("DL_SORTIE", C.SORTIE_DEFAUT), help="dossier de sortie (defaut : Output)")
    p.add_argument("--ttl-min", default=C.env("DL_TTL_MIN", str(C.TTL_DEFAUT_MIN)), help="duree de conservation en minutes (defaut : 60)")
    p.add_argument("--zip-max-mo", type=int, default=int(C.env("DL_ZIP_MAX_MO", "150") or 150), help="taille maximale d'une archive")
    p.add_argument("--dossier-etat", default=C.env("DL_DOSSIER_ETAT", ".github/queue"), help="dossier d'etat publie sur GitHub")
    p.add_argument("--purger", action="store_true", help="ne rien telecharger : seulement supprimer ce qui a expire")
    p.add_argument("--local", action="store_true", help="ne rien envoyer sur GitHub (test sur ta machine)")
    p.add_argument("--simulation", action="store_true", help="fabriquer des fichiers d'exemple sans rien telecharger")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Nettoyage : tout ce qui a depasse la duree de conservation disparait
# ---------------------------------------------------------------------------

def nettoyer(base: Path, ttl: int, ticket: str = "", github: bool = True) -> None:
    """Supprime les anciens dossiers Output, et sur GitHub les archives / etats expires."""
    print(f"Nettoyage : tout ce qui date de plus de {ttl} min est supprime.", flush=True)
    for nom in C.purger_local(base, ttl):
        print(f"  dossier supprime : {nom}")
    if github:
        bilan = C.purger_github(ttl, garder_ticket=ticket)
        print(f"  GitHub : {bilan['releases']} archive(s) supprimee(s), {bilan['expires']} etat(s) passe(s) a « expire », "
              f"{bilan['etats_supprimes']} ancien(s) etat(s) efface(s)" + (f", {bilan['erreurs']} erreur(s)" if bilan["erreurs"] else ""))


# ---------------------------------------------------------------------------
# Rangement : Output/<date_heure>_<playlist>/
# ---------------------------------------------------------------------------

def ranger(staging: Path, base: Path, titre: str, instant: float) -> Path:
    """Deplace le resultat du telechargement dans Output/<date_heure>_<titre>/ et renvoie ce dossier."""
    base.mkdir(parents=True, exist_ok=True)
    final = C.dossier_unique(base, C.nom_dossier(titre, instant))
    items = C.compter_fichiers(str(staging), EXTENSIONS)
    tops = {p.relative_to(staging).parts[0] for p in items}
    unique = staging / next(iter(tops)) if len(tops) == 1 else None
    if unique is not None and unique.is_dir():          # yt-dlp a deja cree un dossier de playlist : on le prend tel quel
        shutil.move(str(unique), str(final))
        shutil.rmtree(staging, ignore_errors=True)
    else:
        shutil.move(str(staging), str(final))
    try:
        staging.parent.rmdir()                           # supprime « _en-cours » s'il est vide
    except OSError:
        pass
    return final


def fichiers_exemple(staging: Path) -> None:
    """Mode simulation : fabrique quelques faux morceaux dans un dossier de playlist."""
    dossier = staging / "Playlist de demonstration"
    dossier.mkdir(parents=True, exist_ok=True)
    for i in range(1, 6):
        (dossier / f"{i:02d} - Morceau de demonstration.mp3").write_bytes(b"\x00" * (400 * 1024))


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    a = arguments(argv)
    ttl = C.ttl_min(a.ttl_min)
    base = Path(a.sortie).resolve()
    hors_ligne = a.local or a.simulation

    if a.purger:
        nettoyer(base, ttl, github=not hors_ligne)
        return 0
    if not a.url:
        print("ERREUR : aucun lien fourni (--url ou DL_URL).")
        return 2

    try:
        source = C.parse_lien(a.url)
        fichiers = C.options(a.format, a.qualite, a.limite)
    except ValueError as e:
        print(f"ERREUR : {e}")
        return 2

    ticket = C.slug(a.ticket or f"demande-{int(time.time())}", 40)
    instant = time.time()                                # sert au nom du dossier : date et heure de la demande
    print("=" * 68)
    print(f"Demande   : {ticket}")
    print(f"Lien      : {source['url']}")
    print(f"Source    : {source['source']} / {source['genre']} (id {source['id']})")
    print(f"Format    : {fichiers['format']} {fichiers['qualite']}".rstrip())
    print(f"Limite    : {fichiers['limite']} pistes   |   espace disque libre : {C.espace_libre_mo()} Mo")
    print(f"Sortie    : {base}   |   conservation : {ttl} min")
    ck = C.fichier_cookies()
    print(f"Cookies   : {'fournis (' + str(sum(1 for l in Path(ck).read_text(errors='replace').splitlines() if l.strip() and not l.startswith('#'))) + ' entrees)' if ck else 'aucun'}")
    print("=" * 68, flush=True)

    # --- 0. nettoyage : une nouvelle demande efface ce qui a expire -------------
    try:
        nettoyer(base, ttl, ticket, github=not hors_ligne)
    except Exception as e:                               # le nettoyage ne doit jamais bloquer un telechargement
        print(f"  (nettoyage incomplet : {e})")

    etat = C.Etat(ticket, source["url"], fichiers["format"], fichiers["qualite"], a.dossier_etat)
    etat.actif = not hors_ligne
    etat.maj(source=source["source"], genre=source["genre"])
    etat.publier(f"demande {ticket} : demarrage", force=True)

    # --- 1. telechargement (dans un dossier de travail, range ensuite) ----------
    staging = base / "_en-cours" / ticket          # sans point initial : certains outils ignorent les dossiers caches
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    debut = time.time()
    outil = "simulation"
    code = 0
    vus: dict = {"position": 0, "total": 0, "liste": None}
    dernieres: deque = deque(maxlen=80)                 # fin du journal : sert a expliquer un echec

    if a.simulation:
        print("MODE SIMULATION : aucun reseau, aucun envoi GitHub.")
        fichiers_exemple(staging)
    else:
        args, outil = C.commande(source, fichiers, str(staging))
        if outil == "yt-dlp":
            args += ["--max-downloads", str(fichiers["limite"]), "-P", str(staging), source["url"]]

        def sur_ligne(ligne: str) -> None:
            dernieres.append(ligne)
            info = C.lire_progression(ligne)
            if not info:
                return
            if "liste" in info:
                vus["liste"] = info["liste"]
                etat.maj(total=info["total"], titre=info["liste"])
            elif "position" in info:
                vus["position"], vus["total"] = info["position"], info["total"]
                etat.maj(fait=info["position"], total=info["total"])
            else:
                vus["position"] += 1
                etat.maj(fait=vus["position"], titre=etat.data["titre"] or info.get("file", ""))
            etat.publier()

        try:
            code = C.executer(args, str(staging), sur_ligne)
        except FileNotFoundError:
            etat.maj(statut="failed", erreur=f"{outil} n'est pas installe sur cette machine.")
            etat.publier(force=True)
            print(f"ECHEC : {outil} introuvable.")
            return 1
        print(f"\n  {outil} termine en {C.duree(time.time() - debut)} (code {code})", flush=True)

    # --- 2. ce qui a ete telecharge ---------------------------------------------
    items = C.compter_fichiers(str(staging), EXTENSIONS)
    if not items and not a.simulation:
        # l'outil a peut-etre ecrit ailleurs que prevu : on cherche les fichiers apparus pendant la demande
        ailleurs = [p for p in C.retrouver_fichiers(debut, [Path.cwd(), base], EXTENSIONS) if staging not in p.parents]
        if ailleurs:
            print(f"  ATTENTION : {len(ailleurs)} fichier(s) ecrit(s) hors du dossier prevu, recuperes :", flush=True)
            for p in ailleurs:
                print(f"    {p}", flush=True)
                shutil.move(str(p), str(staging / p.name))
            items = C.compter_fichiers(str(staging), EXTENSIONS)
        else:
            # rien nulle part : on demande a yt-dlp lui-meme pourquoi YouTube refuse (spotdl cache la vraie raison)
            sonde = C.sonde_youtube(list(dernieres))
            if sonde:
                print("  Sonde yt-dlp sur la premiere video en echec :", flush=True)
                for l in sonde:
                    print(f"    {l}", flush=True)
                dernieres.extend(sonde)
    if not items:
        cause = C.diagnostic(list(dernieres)) or "Le lien est peut-etre prive, indisponible ou bloque."
        etat.maj(statut="failed", erreur=f"Aucun fichier telecharge ({outil}, code {code}). {cause}")
        etat.publier(force=True)
        shutil.rmtree(staging, ignore_errors=True)
        print("ECHEC : aucun fichier telecharge.")
        return 1

    # nom de la playlist : dossier cree par yt-dlp, sinon nom annonce par spotdl, sinon oEmbed Spotify, sinon le ticket
    tops = {p.relative_to(staging).parts[0] for p in items}
    titre = next(iter(tops)) if len(tops) == 1 and (staging / next(iter(tops))).is_dir() else None
    if not titre and source["source"] == "spotify":
        titre = vus["liste"] or C.titre_spotify(source["url"])
    if not titre and a.simulation:
        titre = "Playlist de demonstration"
    titre = titre or f"Demande {ticket}"

    final = ranger(staging, base, titre, instant)
    items = C.compter_fichiers(str(final), EXTENSIONS)
    octets = sum(p.stat().st_size for p in items)
    etat.maj(titre=titre, dossier=final.name, total=len(items), fait=len(items), octets=octets)
    etat.publier(f"{ticket} : {len(items)} fichiers, preparation de l'archive", force=True)
    print(f"  dossier : {final.relative_to(base.parent) if base.parent in final.parents else final}", flush=True)
    print(f"  {len(items)} fichiers, {octets / 1024 / 1024:.1f} Mo a archiver", flush=True)

    # --- 3. archive ZIP (dans le meme dossier, nommee comme lui) ----------------
    try:
        zips = C.decouper(items, str(final), str(final), a.zip_max_mo, base_nom=final.name)
    except Exception as e:
        etat.maj(statut="failed", erreur=f"Archive impossible : {e}")
        etat.publier(force=True)
        return 1
    print(f"  {len(zips)} archive(s) : " + ", ".join(f"{z.name} ({z.stat().st_size / 1024 / 1024:.1f} Mo)" for z in zips), flush=True)

    # --- 4. publication ---------------------------------------------------------
    fin = int(time.time())
    expire = fin + ttl * 60
    if hors_ligne:
        etat.maj(statut="done", termine=fin, expire=expire, duree=int(fin - debut), erreur=None,
                 fichiers=[{"nom": z.name, "taille": z.stat().st_size, "url": str(z.resolve())} for z in zips])
        print("\nMODE LOCAL : rien n'a ete envoye sur GitHub." if a.local else "\nMODE SIMULATION : rien n'a ete envoye.")
        print(json.dumps(etat.data, ensure_ascii=False, indent=2))
        return 0

    try:
        liens = C.publier_release(ticket, final.name, zips, ttl)
    except Exception as e:
        etat.maj(statut="failed", erreur=f"Publication impossible : {e}")
        etat.publier(force=True)
        print(f"ECHEC de publication : {e}")
        return 1

    etat.maj(statut="done", fichiers=liens, duree=int(fin - debut), termine=fin, expire=expire, erreur=None)
    etat.publier(f"{ticket} : archive disponible", force=True)
    print(f"\nTERMINE. Les archives seront supprimees dans {ttl} min (au plus tard a la demande suivante).")
    for l in liens:
        print(f"  {l['nom']}  ({l['taille'] / 1024 / 1024:.1f} Mo)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrompu.")
        sys.exit(130)
    except Exception as e:                              # derniere barriere : on trace clairement
        import traceback
        traceback.print_exc()
        print(f"ERREUR INATTENDUE : {e}")
        sys.exit(1)
