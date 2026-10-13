#!/usr/bin/env python3
"""
Import d'une playlist Spotify pour l'onglet Musique du site (lancé par GitHub Actions, jamais en local).

    python scripts/sp_import.py <lien spotify> <ticket> [--out dossier]

Écrit  <out>/<ticket>.json  :
    {"ok": true, "ticket", "name", "src", "total", "found", "ts",
     "tracks": [{"t": titre, "a": artistes, "s": secondes, "u": uri, "y": {"id", "q", "s"} | null}]}
ou {"ok": false, "error": "..."} si ça échoue (le fichier est écrit dans tous les cas).

Étapes : 1) spotDL lit la playlist et choisit la vidéo YouTube de chaque titre ;
         2) si spotDL échoue, repli sur la page « embed » de Spotify (100 titres au plus) ;
         3) les titres sans vidéo sont cherchés sur YouTube Music (durée ±3 s, ni live ni remix).
Clés Spotify facultatives (secrets du dépôt) : SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET.
"""
import argparse, json, os, re, sys, time
from pathlib import Path

import spotify2yt as S  # noqa: E402

BUDGET_S = int(os.environ.get("SP_BUDGET_S", "1500"))   # temps maximum pour chercher les vidéos manquantes


def build(name, tracks, src):
    out = []
    for t in tracks:
        y = t.get("youtube")
        s = int(t.get("duration_s") or 0)
        out.append({"t": t["title"], "a": t.get("artists", ""), "s": s, "u": (t.get("uri") or "")[:60],
                    "y": {"id": y["id"], "q": y.get("match") if y.get("match") in ("exact", "probable", "faible") else "exact", "s": s} if y and y.get("id") else None})
    return {"ok": True, "name": name, "src": src, "total": len(out), "found": sum(1 for x in out if x["y"]), "ts": int(time.time()), "tracks": out}


def run(link):
    pl = S.parse_link(link)
    if not pl:
        raise RuntimeError("Lien Spotify non reconnu : il faut une playlist ou un album.")
    cfg = {"spotdl": {"threads": 4, "client_id": os.environ.get("SPOTIFY_CLIENT_ID", ""), "client_secret": os.environ.get("SPOTIFY_CLIENT_SECRET", "")}}
    name = tracks = None; src = "spotdl"; more = False; err = ""
    if os.environ.get("SP_SOURCE", "spotdl") == "spotdl":
        try:
            name, tracks, more = S.read_spotdl(f"https://open.spotify.com/{pl[0]}/{pl[1]}", cfg)
        except Exception as e:  # noqa: BLE001
            err = str(e); print("spotDL a échoué :", err)
    if not tracks:
        src = "embed"
        name, tracks, more = S.read_embed(*pl)
        print(f"Repli sur la page embed de Spotify : {len(tracks)} titres (100 au maximum).")
    print(f"« {name} » : {len(tracks)} titres, source {src}")
    todo = [t for t in tracks if not t.get("youtube")]
    if todo:
        m = S.Matcher({"matcher": "ytmusic", "tolerance_s": 3, "pause_s": 0.2})
        t0 = time.time()
        print(f"Recherche YouTube Music pour {len(todo)} titres sans vidéo…")
        for i, t in enumerate(todo, 1):
            if time.time() - t0 > BUDGET_S:
                print("Temps de recherche épuisé : les titres restants seront cherchés par le site."); break
            try:
                t["youtube"] = m.find(t)
            except Exception as e:  # noqa: BLE001
                print("  erreur :", str(e)[:80])
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    res = build(name, tracks, src)
    if more and src == "embed":
        res["note"] = "Spotify n'a donné que 100 titres (spotDL a échoué" + (" : " + err[:140] if err else "") + ")."
    elif err:
        res["note"] = ""
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("url"); ap.add_argument("ticket"); ap.add_argument("--out", default="out")
    a = ap.parse_args()
    if not re.fullmatch(r"[\w-]{4,30}", a.ticket):
        sys.exit("ticket invalide")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); f = out / f"{a.ticket}.json"
    try:
        res = run(a.url)
        res["ticket"] = a.ticket
        f.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"OK : {res['found']}/{res['total']} vidéos trouvées")
    except Exception as e:  # noqa: BLE001
        f.write_text(json.dumps({"ok": False, "ticket": a.ticket, "error": str(e)[:300], "ts": int(time.time())}, ensure_ascii=False), encoding="utf-8")
        print("ÉCHEC :", e); sys.exit(1)


if __name__ == "__main__":
    main()
