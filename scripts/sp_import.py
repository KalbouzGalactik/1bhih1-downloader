#!/usr/bin/env python3
"""
Import d'une playlist Spotify pour l'onglet Musique du site (lancé par GitHub Actions, jamais en local).

    python scripts/sp_import.py <lien spotify> <ticket> [--out dossier]

1) Facultatif : API officielle de Spotify au nom du compte administrateur (secrets SPOTIFY_CLIENT_ID et SPOTIFY_REFRESH_TOKEN) : tes playlists.
2) Sinon : lecture sans connexion comme le fait le lecteur web de Spotify (module SpotifyScraper) : toute playlist publique, aucun compte.
3) Dernier recours : page publique de Spotify, 100 titres au maximum.
4) Chaque titre est cherché sur YouTube Music (le même moteur que spotDL), durée comparée à ±3 s, ni live ni remix.
Écrit <out>/<ticket>.json : {"ok": true, "name", "src", "total", "found", "note", "tracks": [{"t","a","s","u","y"}]}
ou {"ok": false, "error": "..."} en cas d'échec.
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import spotify2yt as S  # noqa: E402

BUDGET_S = int(os.environ.get("SP_BUDGET_S", "1800"))   # temps maximum pour chercher les vidéos


def user_token():
    cid, rt = os.environ.get("SPOTIFY_CLIENT_ID", "").strip(), os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
    if not cid or not rt:
        return None
    r = requests.post(S.AUTH + "/api/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid}, timeout=25)
    if r.status_code != 200:
        why = ""
        try: why = r.json().get("error_description") or r.json().get("error") or ""
        except Exception: pass  # noqa: BLE001
        raise RuntimeError("Le jeton Spotify est refusé (" + why + "). Refais la connexion dans Paramètres → Spotify, puis remplace le secret SPOTIFY_REFRESH_TOKEN.")
    return r.json()["access_token"]


def _tracks_from(d):
    """Cherche la liste des titres dans le dictionnaire renvoyé par SpotifyScraper (la forme exacte peut varier)."""
    def find(o, depth=0):
        if depth > 4: return None
        if isinstance(o, dict):
            for k in ("tracks", "items", "track_list", "trackList"):
                v = o.get(k)
                if isinstance(v, list) and v: return v
                if isinstance(v, dict):
                    r = find(v, depth + 1)
                    if r: return r
            for v in o.values():
                r = find(v, depth + 1)
                if r: return r
        return None
    out = []
    for x in find(d) or []:
        if not isinstance(x, dict): continue
        t = x.get("track") if isinstance(x.get("track"), dict) else x.get("item") if isinstance(x.get("item"), dict) else x
        title = S.clean(t.get("name") or t.get("title"))
        if not title: continue
        arts = t.get("artists") or t.get("artist") or t.get("subtitle") or []
        if isinstance(arts, str): arts = [arts]
        names = [a.get("name") if isinstance(a, dict) else str(a) for a in arts]
        ms = t.get("duration_ms") or t.get("durationMs") or t.get("duration") or 0
        try: ms = float(ms)
        except Exception: ms = 0  # noqa: BLE001
        sec = int(ms / 1000) if ms > 3000 else int(ms)
        uri = t.get("uri") or (("spotify:track:" + t["id"]) if t.get("id") else "")
        out.append({"title": title, "artists": ", ".join(n for n in names if n), "duration_s": sec, "uri": uri, "album": S.clean((t.get("album") or {}).get("name") if isinstance(t.get("album"), dict) else t.get("album"))})
    return out


def read_scraper(kind, pid):
    """Sans connexion : même méthode que le lecteur web de Spotify (module SpotifyScraper). Marche pour toute playlist publique."""
    from spotify_scraper import SpotifyClient
    url = f"https://open.spotify.com/{kind}/{pid}"
    with SpotifyClient() as c:
        obj = c.get_playlist(url, max_tracks=3000) if kind == "playlist" else c.get_album(url)
    d = obj if isinstance(obj, dict) else (obj.to_dict() if hasattr(obj, "to_dict") else obj.model_dump() if hasattr(obj, "model_dump") else getattr(obj, "__dict__", {}))
    name = S.clean(d.get("name") or d.get("title") or "Playlist")
    tracks = _tracks_from(d)
    if not tracks:
        raise RuntimeError("SpotifyScraper n'a renvoyé aucun titre")
    return name, tracks


def build(name, tracks, src):
    out = []
    for t in tracks:
        y = t.get("youtube"); s = int(t.get("duration_s") or 0)
        out.append({"t": t["title"], "a": t.get("artists", ""), "s": s, "u": (t.get("uri") or "")[:60],
                    "y": {"id": y["id"], "q": y.get("match") if y.get("match") in ("exact", "probable", "faible") else "exact", "s": s} if y and y.get("id") else None})
    return {"ok": True, "name": name, "src": src, "total": len(out), "found": sum(1 for x in out if x["y"]), "ts": int(time.time()), "tracks": out}


def read(link):
    pl = S.parse_link(link)
    if not pl:
        raise RuntimeError("Lien Spotify non reconnu : il faut une playlist ou un album.")
    note = ""
    have = bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_REFRESH_TOKEN"))
    if have:   # facultatif : ton compte, API officielle (tes playlists seulement)
        try:
            tok = user_token()
            name, tracks, _ = S.read_api(pl[0], pl[1], tok)
            if tracks:
                return name, tracks, "api", ""
            note = "L'API Spotify n'a renvoyé aucun titre. "
        except Exception as e:  # noqa: BLE001
            note = str(e)[:300] + " "
            print("API Spotify :", e)
    try:
        name, tracks = read_scraper(*pl)
        print(f"SpotifyScraper : {len(tracks)} titres")
        return name, tracks, "scraper", ""
    except Exception as e:  # noqa: BLE001
        note += "Lecture sans connexion impossible (" + str(e)[:140] + "). "
        print("SpotifyScraper :", e)
    try:
        name, tracks, more = S.read_embed(*pl)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError((note + "La page publique de Spotify ne répond pas non plus : " + str(e)[:120]).strip())
    if more:
        note += "Seuls les 100 premiers titres sont lisibles sans l'API."
    return name, tracks, "embed", note.strip()


def run(link):
    name, tracks, src, note = read(link)
    print(f"« {name} » : {len(tracks)} titres, source {src}")
    m = S.Matcher({"matcher": "ytmusic", "tolerance_s": 3, "pause_s": 0.15})
    t0 = time.time(); done = [0]

    def work(t):
        if time.time() - t0 > BUDGET_S:
            return
        for _ in range(2):
            try:
                t["youtube"] = m.find(t); break
            except Exception as e:  # noqa: BLE001
                print("  erreur :", str(e)[:80]); time.sleep(2)
        done[0] += 1
        if done[0] % 50 == 0:
            print(f"  {done[0]}/{len(tracks)} ({int(time.time() - t0)} s)", flush=True)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(work, tracks))
    res = build(name, tracks, src)
    left = sum(1 for t in tracks if not t.get("youtube"))
    if left and time.time() - t0 > BUDGET_S:
        note = (note + " Temps de recherche épuisé : les titres restants seront cherchés par le site.").strip()
    res["note"] = note
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("url"); ap.add_argument("ticket"); ap.add_argument("--out", default="out")
    a = ap.parse_args()
    if not re.fullmatch(r"[\w-]{4,30}", a.ticket):
        sys.exit("ticket invalide")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); f = out / f"{a.ticket}.json"
    try:
        res = run(a.url); res["ticket"] = a.ticket
        f.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"OK : {res['found']}/{res['total']} vidéos trouvées")
    except Exception as e:  # noqa: BLE001
        f.write_text(json.dumps({"ok": False, "ticket": a.ticket, "error": str(e)[:300], "ts": int(time.time())}, ensure_ascii=False), encoding="utf-8")
        print("ÉCHEC :", e); sys.exit(1)


if __name__ == "__main__":
    main()
