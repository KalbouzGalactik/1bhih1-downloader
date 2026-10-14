#!/usr/bin/env python3
"""
spotify2yt : playlist Spotify  ->  txt / csv / json  (+ vidéo YouTube de chaque titre)

Tout est réglé dans config.yaml.  Lancer :  py spotify2yt.py   (ou double-clic sur lancer.bat)

Trois façons de lire la playlist (réglage "source" dans config.yaml) :
  - "spotdl": recommandé. Utilise spotDL (lit Spotify et trouve la vidéo YouTube de chaque titre).
  - "embed" : sans compte, mais Spotify ne donne que les 100 premiers titres.
  - "api"   : TOUS les titres (1000+), avec TON compte Spotify (connexion dans le navigateur, une fois).
              Spotify n'autorise plus la lecture complète que pour les playlists dont tu es
              propriétaire ou collaborateur (règle de février 2026).
  - "auto"  : api si un client_id est renseigné, sinon embed.
"""
import base64, csv, hashlib, http.server, json, os, re, secrets, socketserver, sys, threading, time, urllib.parse, webbrowser
from pathlib import Path

try:
    import requests, yaml
except ImportError:
    sys.exit("Il manque des modules : py -m pip install -r requirements.txt")

API = os.environ.get("S2Y_API", "https://api.spotify.com/v1")
AUTH = os.environ.get("S2Y_AUTH", "https://accounts.spotify.com")
EMBED = os.environ.get("S2Y_EMBED", "https://open.spotify.com/embed")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- liens
def parse_link(s):
    m = re.search(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?|spotify:)(playlist|album)[/:]([A-Za-z0-9]{22})", s)
    return (m.group(1), m.group(2)) if m else None


def clean(x):
    return re.sub(r"\s+", " ", str(x or "").replace(" ", " ").replace("​", " ")).strip()


# ---------------------------------------------------------------- source 1 : embed (100 titres, sans compte)
def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def read_embed(kind, pid):
    r = requests.get(f"{EMBED}/{kind}/{pid}", headers={"User-Agent": UA, "Accept-Language": "fr,en;q=0.7"}, timeout=20)
    if r.status_code == 404:
        raise RuntimeError("Playlist introuvable ou privée.")
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("Page Spotify illisible.")
    for o in walk(json.loads(m.group(1))):
        if isinstance(o.get("trackList"), list) and o["trackList"]:
            tr = [{"title": clean(x.get("title")), "artists": clean(x.get("subtitle")),
                   "duration_s": round((x.get("duration") or 0) / 1000), "uri": x.get("uri", "")} for x in o["trackList"]]
            return clean(o.get("name") or o.get("title") or "Playlist"), [t for t in tr if t["title"]], len(tr) >= 100
    raise RuntimeError("Aucun titre trouvé (playlist privée ou vide).")


# ---------------------------------------------------------------- source 2 : API officielle (tous les titres)
SCOPES = "playlist-read-private playlist-read-collaborative"


def login(client_id, port):
    """Connexion Spotify (PKCE, sans secret). Le jeton est gardé dans .token.json."""
    tok_file = HERE / ".token.json"
    if tok_file.exists():
        t = json.loads(tok_file.read_text())
        if t.get("expires_at", 0) > time.time() + 60:
            return t["access_token"]
        if t.get("refresh_token"):
            r = requests.post(f"{AUTH}/api/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": client_id}, timeout=20)
            if r.ok:
                j = r.json(); t.update(access_token=j["access_token"], expires_at=time.time() + j["expires_in"])
                t["refresh_token"] = j.get("refresh_token", t["refresh_token"]); tok_file.write_text(json.dumps(t)); return t["access_token"]
    redirect = f"http://127.0.0.1:{port}/callback"
    verifier = secrets.token_urlsafe(64)[:100]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(8); box = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            box["code"] = (q.get("code") or [""])[0]; box["state"] = (q.get("state") or [""])[0]; box["err"] = (q.get("error") or [""])[0]
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write("<h2>Connecté. Tu peux fermer cet onglet.</h2>".encode())
        def log_message(self, *a): pass

    class Srv(socketserver.TCPServer):
        allow_reuse_address = True
    srv = Srv(("127.0.0.1", port), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    url = f"{AUTH}/authorize?" + urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect, "scope": SCOPES, "state": state, "code_challenge_method": "S256", "code_challenge": challenge})
    print("Ouverture du navigateur pour te connecter à Spotify…\n(si rien ne s'ouvre :", url, ")")
    webbrowser.open(url)
    for _ in range(300):
        if box.get("code") or box.get("err"):
            break
        time.sleep(1)
    srv.server_close()
    if not box.get("code") or box.get("state") != state:
        raise RuntimeError("Connexion Spotify refusée ou expirée : " + (box.get("err") or "pas de réponse"))
    r = requests.post(f"{AUTH}/api/token", data={"grant_type": "authorization_code", "code": box["code"], "redirect_uri": redirect, "client_id": client_id, "code_verifier": verifier}, timeout=20)
    r.raise_for_status(); j = r.json(); j["expires_at"] = time.time() + j["expires_in"]
    tok_file.write_text(json.dumps(j)); return j["access_token"]


def api_get(url, token, params=None):
    for _ in range(6):
        r = requests.get(url, headers={"Authorization": "Bearer " + token}, params=params, timeout=25)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "3")) + 1); continue
        return r
    raise RuntimeError("Spotify limite les requêtes, réessaie dans une minute.")


def read_api(kind, pid, token):
    r = api_get(f"{API}/{kind}s/{pid}", token, {"fields": "name"} if kind == "playlist" else None)
    if r.status_code in (401, 403):
        raise RuntimeError("Spotify refuse l'accès. Supprime .token.json et recommence la connexion.")
    if r.status_code == 404:
        raise RuntimeError("Playlist introuvable (ou privée et pas à toi).")
    r.raise_for_status(); name = clean(r.json().get("name") or "Playlist")
    tracks, url, params = [], f"{API}/{kind}s/{pid}/" + ("items" if kind == "playlist" else "tracks"), {"limit": 50 if kind == "playlist" else 50}
    while url:
        r = api_get(url, token, params); params = None
        if r.status_code == 404 and url.endswith("/items"):       # anciens comptes / anciennes API : /tracks
            url = url[:-6] + "/tracks"; r = api_get(url, token, {"limit": 50})
        if r.status_code == 403 and ("/items" in url or "/tracks" in url) and kind == "playlist":       # playlist d'un autre : Spotify ne donne plus les titres
            raise RuntimeError("Spotify ne donne les titres que des playlists dont tu es propriétaire ou collaborateur. "
                               "Copie la playlist dans ton compte (Ajouter à une autre playlist → tout sélectionner) puis relance.")
        r.raise_for_status(); j = r.json()
        for it in j.get("items", []):
            t = it.get("track") or it.get("item") or it
            if not t or t.get("is_local") or not t.get("name"):
                continue
            tracks.append({"title": clean(t["name"]), "artists": ", ".join(a["name"] for a in t.get("artists", [])),
                           "duration_s": round((t.get("duration_ms") or 0) / 1000), "uri": t.get("uri", ""), "album": clean((t.get("album") or {}).get("name"))})
        url = j.get("next"); print(f"\r  {len(tracks)} titres lus…", end="", flush=True)
    print()
    return name, tracks, False


# ---------------------------------------------------------------- source 3 : spotDL (lit Spotify ET trouve les vidéos YouTube)
def read_spotdl(link, cfg):
    """Lance `spotdl save` : il lit la playlist et associe chaque titre à une vidéo YouTube (download_url)."""
    import subprocess, tempfile
    sd = cfg.get("spotdl") or {}
    out = Path(tempfile.mkdtemp()) / "pl.spotdl"
    cmd = [sys.executable, "-m", "spotdl", "save", link, "--save-file", str(out), "--threads", str(sd.get("threads", 4))]
    if sd.get("client_id") and sd.get("client_secret"):
        cmd += ["--client-id", str(sd["client_id"]), "--client-secret", str(sd["client_secret"])]
    if sd.get("audio"):
        cmd += ["--audio"] + [str(x) for x in sd["audio"]]
    print("  spotDL travaille (la recherche des vidéos est incluse, ça peut durer)…")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    tail = []
    for line in p.stdout:
        print(line, end="", flush=True)
        if line.strip():
            tail.append(line.strip())
            tail = tail[-6:]
    p.wait()
    if p.returncode != 0 or not out.exists():
        why = re.sub(r"\x1b\[[0-9;]*m", "", " | ".join(tail))[-320:]
        raise RuntimeError("spotDL a échoué" + (" : " + why if why else "") + ".")
    songs = json.loads(out.read_text(encoding="utf-8"))
    if isinstance(songs, dict):
        songs = songs.get("songs") or []
    tracks, name = [], None
    for x in songs:
        if not x.get("name"):
            continue
        name = name or x.get("list_name")
        t = {"title": clean(x["name"]), "artists": ", ".join(x.get("artists") or [x.get("artist", "")]), "duration_s": int(x.get("duration") or 0),
             "uri": x.get("song_id") and "spotify:track:" + x["song_id"] or "", "album": clean(x.get("album_name"))}
        m = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", str(x.get("download_url") or ""))
        if m:
            t["youtube"] = {"id": m.group(1), "url": "https://www.youtube.com/watch?v=" + m.group(1), "yt_title": "", "yt_duration_s": 0, "match": "spotdl"}
        tracks.append(t)
    if not tracks:
        raise RuntimeError("spotDL n'a renvoyé aucun titre.")
    return clean(name or "Playlist"), tracks, False


# ---------------------------------------------------------------- YouTube
BAD = re.compile(r"\b(live|concert|cover|karaoke|instrumental|remix|sped ?up|slowed|reverb|8d|nightcore|reaction|tutorial|acoustic)\b", re.I)


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "").lower()); s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and ")).strip()


def score(c, tr, tol):
    """Plus c'est haut, mieux c'est. Durée (±tol s), mots du titre et de l'artiste, pas de live/remix."""
    s = 0.0
    if tr["duration_s"] and c.get("dur"):
        d = abs(c["dur"] - tr["duration_s"]); s += 4 if d <= tol else 2 if d <= tol * 3 else -3 if d > 15 else 0
    blob = norm(c["title"] + " " + c.get("artist", ""))
    tw = [w for w in norm(re.sub(r"\(.*?\)|\[.*?\]", " ", tr["title"])).split() if len(w) > 1]
    if tw: s += 4 * sum(w in blob.split() for w in tw) / len(tw)
    a0 = norm(tr["artists"].split(",")[0])
    if a0 and a0 in blob: s += 2
    if BAD.search(c["title"]) and not BAD.search(tr["title"]): s -= 4
    if c.get("official"): s += 1.5
    return s


class Matcher:
    def __init__(self, cfg):
        self.tol = cfg.get("tolerance_s", 3); self.pause = cfg.get("pause_s", 0.3); self.mode = cfg.get("matcher", "ytmusic")
        self.ytm = self.ydl = None
        if self.mode == "ytmusic":
            try:
                from ytmusicapi import YTMusic; self.ytm = YTMusic()
            except Exception as e:
                print("ytmusicapi indisponible (", e, ") -> yt-dlp"); self.mode = "ytdlp"
        if self.mode == "ytdlp":
            try:
                import yt_dlp; self.ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True})
            except Exception as e:
                print("yt-dlp indisponible (", e, ") -> pas de vidéos"); self.mode = "none"

    def candidates(self, q):
        if self.mode == "ytmusic":
            out = []
            for r in self.ytm.search(q, filter="songs", limit=6):
                if r.get("videoId"):
                    out.append({"id": r["videoId"], "title": r.get("title", ""), "artist": ", ".join(a["name"] for a in r.get("artists", []) if a.get("name")), "dur": r.get("duration_seconds") or 0, "official": True})
            return out
        if self.mode == "ytdlp":
            e = (self.ydl.extract_info("ytsearch6:" + q, download=False) or {}).get("entries") or []
            return [{"id": x["id"], "title": x.get("title", ""), "artist": x.get("channel") or x.get("uploader") or "", "dur": int(x.get("duration") or 0), "official": bool(re.search(r"- topic$", str(x.get("channel") or ""), re.I))} for x in e if x and x.get("id")]
        return []

    def find(self, tr):
        if self.mode == "none":
            return None
        q = f'{tr["artists"].split(",")[0]} {tr["title"]}'.strip()
        for _ in range(3):
            try:
                best = max(self.candidates(q), key=lambda c: score(c, tr, self.tol), default=None); break
            except Exception as e:
                print("  (erreur recherche, nouvel essai)", str(e)[:60]); time.sleep(3); best = None
        time.sleep(self.pause)
        if not best:
            return None
        sc = score(best, tr, self.tol)
        return {"id": best["id"], "url": "https://www.youtube.com/watch?v=" + best["id"], "yt_title": best["title"], "yt_duration_s": best["dur"], "match": "exact" if sc >= 7 else "probable" if sc >= 4 else "faible"}


# ---------------------------------------------------------------- sorties
def safe(n):
    return re.sub(r'[\\/:*?"<>|]+', "_", n).strip(" .")[:80] or "playlist"


def write_all(name, tracks, formats, outdir):
    outdir.mkdir(parents=True, exist_ok=True); base = outdir / safe(name); files = []
    if "json" in formats:
        base.with_suffix(".json").write_text(json.dumps({"name": name, "count": len(tracks), "tracks": tracks}, ensure_ascii=False, indent=2), encoding="utf-8"); files.append(base.with_suffix(".json"))
    if "csv" in formats:     # mêmes colonnes qu'Exportify + YouTube : importable tel quel sur le site
        with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["Track URI", "Track Name", "Album Name", "Artist Name(s)", "Duration (ms)", "YouTube ID", "YouTube URL", "YouTube Title", "Match"])
            for t in tracks:
                y = t.get("youtube") or {}
                w.writerow([t.get("uri", ""), t["title"], t.get("album", ""), t["artists"].replace(", ", ";"), t["duration_s"] * 1000, y.get("id", ""), y.get("url", ""), y.get("yt_title", ""), y.get("match", "")])
        files.append(base.with_suffix(".csv"))
    if "txt" in formats:
        lines = [f'{t["artists"].split(",")[0]} - {t["title"]}' if t["artists"] else t["title"] for t in tracks]
        base.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8"); files.append(base.with_suffix(".txt"))
    if "urls" in formats:    # un lien YouTube par ligne (pratique avec yt-dlp -a urls.txt)
        u = [t["youtube"]["url"] for t in tracks if t.get("youtube")]
        p = outdir / (safe(name) + "_urls.txt"); p.write_text("\n".join(u) + "\n", encoding="utf-8"); files.append(p)
    return files


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    links = [l for l in (cfg.get("playlists") or []) if l and "..." not in str(l)]
    if not links:
        sys.exit("Mets au moins un lien de playlist dans config.yaml (section playlists).")
    source = cfg.get("source", "auto"); cid = (cfg.get("spotify") or {}).get("client_id", "")
    port = int((cfg.get("spotify") or {}).get("redirect_port", 8888))
    formats = [f.lower() for f in (cfg.get("formats") or ["csv", "json", "txt"])]
    outdir = (HERE / cfg.get("output_dir", "sortie")); yt = cfg.get("youtube") or {}
    use_sd = source == "spotdl"
    use_api = source == "api" or (source == "auto" and cid)
    if use_api and not cid:
        sys.exit("source: api demande un client_id (voir LISEZ-MOI.md, étape « Créer l'application Spotify »).")
    matcher = Matcher(yt) if yt.get("enabled", True) and not use_sd else None
    token = login(cid, port) if use_api else None

    for link in links:
        pl = parse_link(str(link))
        if not pl:
            print("Lien non reconnu :", link); continue
        print("\n▶", link)
        try:
            name, tracks, more = read_spotdl(str(link), cfg) if use_sd else read_api(*pl, token) if use_api else read_embed(*pl)
        except Exception as e:
            print("  Échec :", e); continue
        print(f"  « {name} » : {len(tracks)} titres")
        if more:
            print("  ⚠ Spotify n'en donne que 100 sans compte : renseigne client_id dans config.yaml (source: api) pour tout avoir.")
        cache_f = outdir / (safe(name) + ".cache.json"); outdir.mkdir(parents=True, exist_ok=True)
        cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
        if matcher and matcher.mode != "none":
            print("  Recherche des vidéos YouTube (reprise automatique si tu interromps)…")
            for i, t in enumerate(tracks, 1):
                k = t["uri"] or f'{t["artists"]}|{t["title"]}'
                if k not in cache:
                    cache[k] = matcher.find(t)
                    if i % 10 == 0: cache_f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                t["youtube"] = cache[k]
                print(f"\r  {i}/{len(tracks)}", end="", flush=True)
            cache_f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8"); print()
            print(f'  {sum(1 for t in tracks if t.get("youtube"))}/{len(tracks)} vidéos trouvées')
        for f in write_all(name, tracks, formats, outdir):
            print("  écrit :", f)
    print("\nTerminé.")


if __name__ == "__main__":
    main()
