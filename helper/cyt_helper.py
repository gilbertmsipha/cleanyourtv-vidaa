"""Clean Your TV for VIDAA: the helper that serves the launcher to the TV and launches apps for it.

A web page on a VIDAA TV may not launch apps itself (VIDAA ignores its platform message and DIAL refuses web
origins), but a paired remote-control client may. This helper is that client: it keeps one MQTT connection to
the TV, using Sidee's pairing (~/.sidee/session.json), and the launcher page asks it over HTTP.

Usage: python helper/cyt_helper.py [path/to/config.json]
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config.json"
if not CONFIG_PATH.exists():
    sys.exit(f"{CONFIG_PATH} is missing: copy config.example.json to config.json and fill it in (see README.md)")
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
sys.path.insert(0, str((CONFIG_PATH.parent / CONFIG.get("sidee_dir", "../sidee")).resolve()))
from core import client, protocol  # noqa: E402  (Sidee)

WEB = ROOT / "web"
DATA = ROOT / "data"
ICONS = DATA / "icons"
APPS_CACHE = DATA / "apps.json"
HIDDEN_FILE = DATA / "hidden.json"
TAKEOVER_FILE = DATA / "takeover.json"


def takeover_on() -> bool:
    """Whether the launcher takes over from VIDAA's home; switched from the launcher's VIDAA menu, kept on disk."""
    if TAKEOVER_FILE.exists():
        return bool(json.loads(TAKEOVER_FILE.read_text(encoding="utf-8")).get("on", True))
    return CONFIG.get("home_takeover", True)


def set_takeover(on: bool):
    DATA.mkdir(exist_ok=True)
    TAKEOVER_FILE.write_text(json.dumps({"on": on}), encoding="utf-8")
ICON_HOSTS = ("img.vidaahub.com", "noobygains.github.io")
# Remote keys the launcher's buttons may send; nothing else is forwarded.
ALLOWED_KEYS = {"KEY_MENU", "KEY_HOME", "KEY_EXIT", "KEY_RETURNS", "KEY_INPUT", "KEY_SETTINGS"}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


class TvLink:
    """One long-lived MQTT connection to the TV, reopened when it drops or the token is refreshed."""

    def __init__(self):
        self.lock = threading.RLock()
        self.sess = None
        self.token = None

    def _connect(self):
        session = client._ensure_ready(client.Session.load())
        if self.sess and self.token == session.access_token and self.sess.client.is_connected():
            return self.sess
        self.close()
        for attempt in range(2):
            sess = client._saved_connection(session, None if session.tokenless else session.access_token)
            try:
                rc = sess.start()
            except OSError:
                rc = None
            if rc is None:
                # Nothing answered at the saved address: the router may have given the TV a new one.
                sess.stop()
                if attempt == 0 and rediscover_tv():
                    session = client._ensure_ready(client.Session.load())
                    continue
                raise ConnectionError("the TV is not reachable (is it on?)")
            if rc == 0:
                sess.subscribe([sess.topics["applist_reply"], sess.topics["mobile"] + "ui_service/data/sourcelist"])
                self.sess, self.token = sess, session.access_token
                log("connected to the TV")
                return sess
            sess.stop()
            if attempt == 0 and not session.tokenless:
                log(f"TV refused the token (rc={rc}); refreshing")
                session = client.refresh(session)
        raise ConnectionError("the TV refused the connection: pair again with Sidee")

    def close(self):
        if self.sess:
            self.sess.stop()
            self.sess = None

    def publish(self, path: str, payload: str = ""):
        """path is relative to the ui service topic, e.g. 'actions/launchapp'."""
        with self.lock:
            sess = self._connect()
            del sess.messages[:-50]
            sess.publish(sess.topics["ui"] + path, payload)

    def request(self, path: str, payload: str, reply_suffix: str, timeout: float = 8.0):
        with self.lock:
            sess = self._connect()
            since = len(sess.messages)
            sess.publish(sess.topics["ui"] + path, payload)
            return sess.wait_for(reply_suffix, timeout, since)

    def send_key(self, key: str):
        with self.lock:
            sess = self._connect()
            sess.publish(f"/remoteapp/tv/remote_service/{sess.client_id}/actions/sendkey", key)

    def launch(self, app: dict):
        self.publish("actions/launchapp", protocol.launch_payload(app["appId"], app["name"], app["url"]))

    def sources(self) -> list:
        raw = self.request("actions/sourcelist", "", "ui_service/data/sourcelist")
        sources = json.loads(raw) if raw else None
        if not isinstance(sources, list):
            raise ConnectionError("the TV did not send its input list")
        return sources

    def change_source(self, source: dict):
        self.publish("actions/changesource", json.dumps(
            {"sourceid": source["sourceid"], "sourcename": source.get("sourcename", "")}))

    def app_list(self) -> list:
        raw = self.request("actions/applist", "0", "ui_service/data/applist")
        apps = protocol.parse_applist_message(raw) if raw else None
        if not apps:
            raise ConnectionError("the TV did not send its app list")
        return apps


def rediscover_tv() -> bool:
    """Find the TV again on the network by its name and save its new address in the pairing."""
    name = CONFIG.get("tv_name", "")
    try:
        found = client.discover(timeout=4)
    except Exception as e:
        log(f"discovery failed: {e}")
        return False
    for tv in found:
        if tv.is_vidaa and (not name or tv.friendly_name == name):
            session = client.Session.load()
            if session.host != tv.host:
                log(f"the TV moved to {tv.host}; updating the pairing")
                session.host = tv.host
                session.save()
            return True
    log("the TV was not found on the network")
    return False


TV = TvLink()


class Apps:
    """The TV's own app list, cached on disk so the launcher works while the TV is still waking up."""

    def __init__(self):
        self.lock = threading.Lock()
        self.apps = json.loads(APPS_CACHE.read_text(encoding="utf-8")) if APPS_CACHE.exists() else []
        self.fetched = 0.0

    def refresh(self):
        apps = TV.app_list()
        DATA.mkdir(exist_ok=True)
        APPS_CACHE.write_text(json.dumps(apps, indent=2, ensure_ascii=False), encoding="utf-8")
        with self.lock:
            self.apps, self.fetched = apps, time.time()
        log(f"app list refreshed: {len(apps)} apps")

    def refresh_if_stale(self):
        # Short, so an app uninstalled on the TV leaves the launcher the next time it opens.
        if time.time() - self.fetched > CONFIG.get("app_list_refresh_minutes", 5) * 60:
            try:
                self.refresh()
            except Exception as e:  # the TV may be off; keep the cached list
                log(f"app list refresh failed: {e}")

    def get(self, app_id: str) -> dict | None:
        with self.lock:
            return next((a for a in self.apps if str(a.get("appId")) == app_id), None)

    @staticmethod
    def removed() -> list:
        """Apps removed from Home in the launcher (hold OK on a tile). They stay installed on the TV."""
        return json.loads(HIDDEN_FILE.read_text(encoding="utf-8")) if HIDDEN_FILE.exists() else []

    def set_removed(self, app_id: str, removed: bool):
        ids = [i for i in self.removed() if i != app_id] + ([app_id] if removed else [])
        DATA.mkdir(exist_ok=True)
        HIDDEN_FILE.write_text(json.dumps(ids), encoding="utf-8")

    def restore_all(self):
        HIDDEN_FILE.unlink(missing_ok=True)

    def for_launcher(self) -> list:
        hidden = set(CONFIG.get("hidden", [])) | set(self.removed())
        with self.lock:
            apps = [a for a in self.apps if str(a.get("appId")) not in hidden and not str(a.get("appId")).startswith("cyt")]
        dock = [str(x) for x in CONFIG.get("dock", [])]
        order = {app_id: i for i, app_id in enumerate(dock)}
        apps.sort(key=lambda a: order.get(str(a.get("appId")), len(order)))
        out = []
        for a in apps:
            entry = {"appId": str(a["appId"]), "name": a.get("name", "").replace("▶", "").strip(),
                     "icon": f"/icon/{a['appId']}"}
            if a.get("url") == "browser":  # the launcher's Browser screen, which hands its searches to this app
                entry.update(name="Browser", browser=True)
            out.append(entry)
        return out


APPS = Apps()


class Sources:
    """The TV's inputs for the Inputs panel, asked for once and kept."""

    def __init__(self):
        self.list = []

    def get(self) -> list:
        if not self.list:
            try:
                self.list = [{"id": s["sourceid"], "name": s.get("displayname", s["sourceid"]).replace("▶", "").strip()}
                             for s in TV.sources()]
            except Exception as e:
                log(f"input list failed: {e}")
        return self.list


SOURCES = Sources()


# The launcher's Browser screen searches with Brave and opens the results in the TV Browser: a page opened as an app
# only gets arrow-key focus, but the TV Browser has a pointer for clicking around websites.
BROWSER = CONFIG.get("browser") or {}
# Suggestions as you type come from DuckDuckGo: Brave's feed answers only Brave's pages and turns scripts away (429).
SUGGEST = "https://duckduckgo.com/ac/?type=list&q="


def browse(url: str):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("only web addresses can be opened")
    app = next((a for a in APPS.apps if a.get("url") == "browser"), {"appId": "16", "name": "TV Browser"})
    TV.launch(dict(app, url=url))


def suggestions(q: str, region: str = "") -> list:
    """Search suggestions for the Browser screen, which may not ask the feed itself (no CORS)."""
    if not q.strip():
        return []
    region = region or BROWSER.get("region", "")  # a DuckDuckGo region code, such as za-en
    req = urllib.request.Request(SUGGEST + quote(q[:200]) + "&kl=" + quote(region[:10]), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            found = json.load(r)[1]
    except Exception:
        return []
    return [s for s in found if isinstance(s, str)][:8]


STREMIO = CONFIG.get("stremio") or {}
STREMIO_PORT = 11470
STREMIO_SERVER_JS = "https://dl.strem.io/server/v4.22.3/desktop/server.js"


class AppMirror:
    """A web app kept on this computer and served from it over plain http.

    Stremio's TV build lives on an https site, and an https page may not call the streaming server on this computer,
    which is http (mixed content). Served from here, the page and the server are both plain http on the home network.
    Files are fetched from the app's site when first asked for and kept; when its index.html changes, the copy is
    dropped, since the other files may change under the same names."""

    TYPES = {".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript",
             ".json": "application/json", ".svg": "image/svg+xml", ".ttf": "font/ttf"}

    def __init__(self, upstream: str, folder: Path):
        self.upstream, self.folder = upstream.rstrip("/") + "/", folder
        self.lock = threading.Lock()

    def _fetch(self, rel: str, timeout: float) -> bytes:
        req = urllib.request.Request(self.upstream + rel, headers={"User-Agent": "cyt-helper"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def _index(self) -> bytes:
        cached = self.folder / "index.html"
        try:
            body = self._fetch("", 4)
        except OSError:
            if cached.exists():  # offline: the kept copy still works
                return cached.read_bytes()
            raise
        with self.lock:
            if not cached.exists() or cached.read_bytes() != body:
                shutil.rmtree(self.folder, ignore_errors=True)
                self.folder.mkdir(parents=True)
                cached.write_bytes(body)
                log(f"web app {self.upstream}: fetched a new version")
        return body

    def get(self, rel: str, query: str) -> tuple[bytes, str] | None:
        if rel in ("", "index.html"):
            return self._index(), "text/html; charset=utf-8"
        if ".." in rel.split("/") or rel.startswith("/") or "\\" in rel:
            return None
        path = self.folder / (rel + ("@" + quote(query, safe="") if query else ""))
        if not path.exists():
            body = self._fetch(rel + ("?" + query if query else ""), 30)
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(f"{path.name}.{threading.get_ident()}.part")
            part.write_bytes(body)
            part.replace(path)
        ctype = self.TYPES.get(Path(rel).suffix.lower()) or mimetypes.guess_type(rel)[0] or "application/octet-stream"
        return path.read_bytes(), ctype


STREMIO_APP = AppMirror(STREMIO["web_app"], DATA / "stremio-app") if STREMIO.get("web_app") else None


class StremioServer(threading.Thread):
    """Stremio's streaming server (torrents and transcoding), kept running on this computer for the TV's Stremio.

    It is Stremio's own server.js, started with NO_CORS: by default it answers only pages from Stremio's own sites,
    and the TV's Stremio comes from this helper. Stremio desktop, if installed, brings server.js and a runtime for it;
    otherwise Node runs a downloaded copy. Stremio desktop opened later finds this server running and uses it."""

    def __init__(self):
        super().__init__(daemon=True, name="stremio-server")
        self.proc = None
        self.warned = ""

    @staticmethod
    def state() -> str:
        """'open' (the TV's Stremio may use it), 'closed' (running, but for Stremio's own sites only) or 'down'."""
        req = urllib.request.Request(f"http://127.0.0.1:{STREMIO_PORT}/settings", headers={"Origin": "http://cyt.invalid"})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return "open" if r.headers.get("Access-Control-Allow-Origin") else "closed"
        except OSError:
            return "down"

    @staticmethod
    def command() -> list | None:
        if STREMIO.get("server_command"):
            return STREMIO["server_command"]
        desktop = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Stremio"
        if (desktop / "stremio-runtime.exe").exists() and (desktop / "server.js").exists():
            return [str(desktop / "stremio-runtime.exe"), str(desktop / "server.js")]
        node = shutil.which("node")
        if not node:
            return None
        script = DATA / "stremio-server" / "server.js"
        if not script.exists():
            log("downloading Stremio's streaming server")
            script.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(STREMIO_SERVER_JS, timeout=120) as r:
                body = r.read()
            script.write_bytes(body)
        return [node, str(script)]

    def start_server(self):
        cmd = self.command()
        if not cmd:
            self.warn("Stremio's streaming server cannot start: install Stremio desktop, or Node.js and ffmpeg")
            return
        DATA.mkdir(exist_ok=True)
        out = open(DATA / "stremio-server.log", "ab")
        self.proc = subprocess.Popen(cmd, cwd=str(Path(cmd[-1]).parent), env=dict(os.environ, NO_CORS="1"),
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log(f"started Stremio's streaming server on port {STREMIO_PORT}")

    @staticmethod
    def apply_settings():
        """The TV's player asks for every audio track of a video at once, and the server transcodes one track of a
        kind at a time by default: each request cancels the one before and playback fails. Allow more."""
        wanted = {"transcodeTrackConcurrency": 8, **STREMIO.get("server_settings", {})}
        base = f"http://127.0.0.1:{STREMIO_PORT}/settings"
        with urllib.request.urlopen(base, timeout=3) as r:
            values = json.load(r).get("values", {})
        changed = {k: v for k, v in wanted.items() if values.get(k) != v}
        if changed:
            req = urllib.request.Request(base, data=json.dumps(changed).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).close()
            log(f"Stremio server settings: {changed}")

    def warn(self, msg: str):
        if self.warned != msg:
            log(msg)
            self.warned = msg

    def run(self):
        while True:
            try:
                state = self.state()
                if state == "down" and (self.proc is None or self.proc.poll() is not None):
                    self.start_server()
                elif state == "closed":
                    self.warn("Stremio desktop's own server is running and keeps the TV out: close Stremio desktop, "
                              "and the helper starts one the TV may use")
                elif state == "open":
                    self.warned = ""
                    self.apply_settings()
            except Exception as e:
                log(f"Stremio server: {e}")
            time.sleep(30)


def launch_target(app: dict) -> dict:
    """The app as the TV should open it: Stremio from this helper, with this computer's streaming server."""
    if STREMIO_APP and str(app.get("appId")) == str(STREMIO.get("app_id")):
        ip = own_ip()
        return dict(app, url=f"http://{ip}:{CONFIG.get('port', 8765)}/stremio/?server=http://{ip}:{STREMIO_PORT}")
    return app


def icon_url(app: dict) -> str | None:
    raw = app.get("httpIcon") or ""
    url = raw[raw.find("http"):] if "http" in raw else ""
    return url if urlsplit(url).hostname in ICON_HOSTS else None


def icon_bytes(app_id: str) -> tuple[bytes, str] | None:
    """The app's icon, fetched once from VIDAA's image host and served same-origin so the page can sample it."""
    ICONS.mkdir(parents=True, exist_ok=True)
    cached = next(ICONS.glob(f"{app_id}.*"), None)
    if cached:
        return cached.read_bytes(), "image/" + cached.suffix[1:]
    app = APPS.get(app_id)
    url = app and icon_url(app)
    if not url:
        return None
    with urllib.request.urlopen(url, timeout=10) as r:
        body, ctype = r.read(), r.headers.get_content_type()
    if not ctype.startswith("image/"):
        return None
    (ICONS / f"{app_id}.{ctype.split('/')[1]}").write_bytes(body)
    return body, ctype


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _allowed(self) -> bool:
        # The TV is allowed at whatever address it has now (the pairing follows it), as are the listed clients.
        ip = self.client_address[0]
        if ip in CONFIG.get("allowed_clients", []) or ip == client.Session.load().host:
            return True
        self.send_error(403)
        return False

    def _json(self, code: int, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", 0)), 2_000_000)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self):
        if not self._allowed():
            return
        path = urlsplit(self.path).path
        if path == "/api/state":
            threading.Thread(target=APPS.refresh_if_stale, daemon=True).start()
            self._json(200, {"apps": APPS.for_launcher(), "dock_size": len(CONFIG.get("dock", [])),
                             "greeting_name": CONFIG.get("greeting_name", ""),
                             "clock_24h": CONFIG.get("clock_24h", False), "sources": SOURCES.get(),
                             "removed_count": len(APPS.removed()), "takeover": takeover_on(), "browser": BROWSER})
            return
        if path == "/api/suggest":
            query = parse_qs(urlsplit(self.path).query)
            self._json(200, suggestions(query.get("q", [""])[0], query.get("kl", [""])[0]))
            return
        if path == "/go":
            self._go()
            return
        if path == "/api/sources":
            try:
                self._json(200, {"ok": True, "sources": TV.sources()})
            except Exception as e:
                TV.close()
                self._json(502, {"ok": False, "error": str(e)})
            return
        if STREMIO_APP and (path == "/stremio" or path.startswith("/stremio/")):
            if path == "/stremio":
                self.send_response(301)
                self.send_header("Location", "/stremio/" + ("?" + urlsplit(self.path).query if urlsplit(self.path).query else ""))
                self.end_headers()
                return
            try:
                got = STREMIO_APP.get(path[len("/stremio/"):], urlsplit(self.path).query)
            except Exception as e:
                log(f"{path}: {e}")
                got = None
            if not got:
                self.send_error(404)
                return
            body, ctype = got
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/icon/"):
            try:
                got = icon_bytes(path[len("/icon/"):])
            except Exception as e:
                log(f"icon {path}: {e}")
                got = None
            if not got:
                self.send_error(404)
                return
            body, ctype = got
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def _go(self):
        """GET /go?app=ID | ?source=ID | ?browse=URL | ?key=KEY, plus &back=URL. The https launcher navigates here, since it may
        not fetch a LAN http address but may navigate to one. Shows a black page while the TV acts, then returns to
        back if nothing took over the screen."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        back = query.get("back", "")
        origins = CONFIG.get("launcher_origins", []) + [f"http://{self.headers.get('Host', '')}/"]
        if not any(back.startswith(o.rstrip("/") + "/") for o in origins):
            back = "/"
        error, text, wait = "", "", 7000
        try:
            if query.get("takeover") in ("on", "off"):
                set_takeover(query["takeover"] == "on")
                log("home takeover switched " + query["takeover"])
                wait = 300
            if "app" in query:
                app = APPS.get(query["app"])
                if not app:
                    error = "That app is not on the TV."
                else:
                    text = "Opening " + app.get("name", "the app").replace("▶", "").strip() + "…"
                    TV.launch(launch_target(app))
                    HOME_WATCH.opened(str(app.get("appId")))
                    log(f"launched {app.get('name')} via /go")
            elif "source" in query:
                source = next((s for s in TV.sources() if s.get("sourceid") == query["source"]), None)
                if not source:
                    error = "That input is not on the TV."
                else:
                    text = "Switching to " + source.get("displayname", source["sourceid"]).replace("▶", "").strip() + "…"
                    TV.change_source(source)
                    log(f"switched to {source['sourceid']} via /go")
            elif "browse" in query:
                text = "Opening the TV Browser…"
                browse(query["browse"])
                log("opened the TV Browser via /go")
            elif query.get("key") in ALLOWED_KEYS:
                # Menus open over the launcher, so go straight back to it (Home leaves the browser hidden, so it stays).
                TV.send_key(query["key"])
                log(f"sent {query['key']} via /go")
                text, wait = "", 300
            elif "takeover" not in query:
                error = "Unknown request."
        except Exception as e:
            log(f"/go failed: {e}")
            TV.close()
            error = "The TV did not respond."
        body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Opening</title>
<style>html,body{{margin:0;height:100%;background:#000;color:rgba(255,255,255,.7);font:300 40px sans-serif;
display:flex;align-items:center;justify-content:center}}</style></head><body>{html.escape(error or text)}
<script>
var back = {json.dumps(back)};
// If an app or input took over, this page is hidden; if not (or it failed), go back to the launcher.
setTimeout(function () {{ if (document.visibilityState === 'visible') location.replace(back); }}, {3000 if error else wait});
</script></body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._allowed():
            return
        path = urlsplit(self.path).path
        data = self._body()
        try:
            if path == "/api/launch":
                app = APPS.get(str(data.get("appId", "")))
                if not app:
                    self._json(404, {"ok": False, "error": "unknown app"})
                    return
                t = time.time()
                TV.launch(launch_target(app))
                HOME_WATCH.opened(str(app.get("appId")))
                log(f"launched {app.get('name')} in {int((time.time() - t) * 1000)} ms")
                self._json(200, {"ok": True})
                return
            if path == "/api/browse":
                browse(str(data.get("url", "")))
                log("opened the TV Browser")
                self._json(200, {"ok": True})
                return
            if path == "/api/key":
                key = str(data.get("key", ""))
                if key not in ALLOWED_KEYS:
                    self._json(400, {"ok": False, "error": "key not allowed"})
                    return
                TV.send_key(key)
                self._json(200, {"ok": True})
                return
            if path == "/api/remove":
                if data.get("restore_all"):
                    APPS.restore_all()
                else:
                    APPS.set_removed(str(data.get("appId", "")), bool(data.get("removed", True)))
                self._json(200, {"ok": True, "removed_count": len(APPS.removed())})
                return
            if path == "/report":
                # Probe pages (web/probe*.html) send what they found on the TV.
                reports = ROOT / "reports"
                reports.mkdir(exist_ok=True)
                text = json.dumps(data, indent=2, ensure_ascii=False)
                (reports / time.strftime("report-%Y%m%d-%H%M%S.json")).write_text(text, encoding="utf-8")
                (reports / "latest.json").write_text(text, encoding="utf-8")
                log(f"report from {self.client_address[0]}: {data.get('page', 'probe')}")
                self._json(200, {"ok": True})
                return
            if path == "/api/refresh":
                APPS.refresh()
                self._json(200, {"ok": True, "apps": len(APPS.apps)})
                return
        except Exception as e:
            log(f"{path} failed: {e}")
            TV.close()
            self._json(502, {"ok": False, "error": str(e)})
            return
        self.send_error(404)


def own_ip() -> str:
    """This computer's address on the TV's network."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((client.Session.load().host, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def check_own_address():
    """The Home tile carries this computer's address and a tile cannot be changed once added. If the address
    changed (the router handed out a new one), add a Home tile with the new address; the old one can be deleted."""
    url = CONFIG.get("launcher_url")
    if not url:
        return
    ip = own_ip()
    state_path = DATA / "helper_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if state.get("ip") == ip:
        return
    helper = f"http://{ip}:{CONFIG.get('port', 8765)}"
    app_id = "cythome" + ip.split(".")[-1]
    tile_url = url + "?helper=" + quote(helper, safe="")
    log(f"this computer's address changed ({state.get('ip')} -> {ip}); adding a Home tile for it")
    try:
        client.add_tile(client.Session.load(), app_id, "Home", tile_url, CONFIG.get("tile_icon", ""))
    except Exception as e:
        log(f"could not add the new Home tile: {e}")
        return
    log("NEW HOME TILE ADDED: delete the old Home tile on the TV")
    DATA.mkdir(exist_ok=True)
    state_path.write_text(json.dumps({"ip": ip, "tile": app_id}), encoding="utf-8")


# DIAL apps whose running state the TV reports, with the Origin each one requires.
DIAL_APPS = {"Netflix": None, "AmazonInstantVideo": None, "YouTube": "https://www.youtube.com"}


# TV app id -> DIAL name, so an app opened from the launcher is watched from the moment it opens.
APP_DIAL_NAMES = {"1": "Netflix", "2": "AmazonInstantVideo", "3": "YouTube"}


class HomeWatch(threading.Thread):
    """VIDAA has no setting for another home screen, and on this firmware it does not answer a "what is on screen"
    query. It does say when it comes on (its remote port answers again) and, over DIAL, whether Netflix, YouTube or
    Prime Video is running; when one of them closes the TV is back on VIDAA's home. At those moments this opens the
    launcher, served by this helper so it shows the live app list.

    The TV answers DIAL one question at a time, Netflix in milliseconds but YouTube and Prime Video in one to two
    seconds. So while an app is open only that app (and Netflix) is asked about, which notices it closing quickly;
    all three are asked about only while nothing is open, to notice one starting."""

    def __init__(self):
        super().__init__(daemon=True, name="home-watch")
        self.up = None
        self.down_since = 0.0
        self.dial = (None, None)   # (TV host, DIAL apps URL)
        self.open_apps: set = set()
        self.starting: dict = {}    # app -> until when it counts as open while it starts

    def dial_base(self, host: str) -> str:
        if self.dial[0] != host:
            base = f"http://{host}:3367/apps/"
            try:
                with urllib.request.urlopen(f"http://{host}:3366/dd.xml", timeout=3) as r:
                    base = r.headers.get("Application-URL") or base
            except OSError:
                pass
            self.dial = (host, base if base.endswith("/") else base + "/")
        return self.dial[1]

    def ask(self, host: str, apps) -> set:
        running = set()
        for app in apps:
            origin = DIAL_APPS[app]
            req = urllib.request.Request(self.dial_base(host) + app, headers={"Origin": origin} if origin else {})
            try:
                with urllib.request.urlopen(req, timeout=4) as r:
                    if b"<state>running</state>" in r.read():
                        running.add(app)
            except OSError:
                pass
        return running

    def opened(self, app_id: str):
        """The launcher just opened this app: watch it now rather than after the next full round."""
        if app_id in APP_DIAL_NAMES:
            # The TV reports it running only after a few seconds; until then it is starting, not closed.
            self.starting[APP_DIAL_NAMES[app_id]] = time.time() + 15
            self.open_apps.add(APP_DIAL_NAMES[app_id])

    @staticmethod
    def tv_up(host: str) -> bool:
        try:
            with socket.create_connection((host, client.MQTT_PORT), timeout=1.5):
                return True
        except OSError:
            return False

    @staticmethod
    def warm():
        """Keep the connection to the TV open, so opening the launcher does not wait ~3 s for a new one."""
        try:
            with TV.lock:
                TV._connect()
        except Exception:
            pass

    @staticmethod
    def open_launcher(why: str):
        if not takeover_on():
            return
        state_path = DATA / "helper_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        TV.launch({"appId": state.get("tile", "cythome"), "name": "Home",
                   "url": f"http://{own_ip()}:{CONFIG.get('port', 8765)}/"})
        log(f"opened the launcher ({why})")

    def run(self):
        last_warm = 0.0
        while True:
            try:
                self.step(time.time() - last_warm > 20)
                if time.time() - last_warm > 20:
                    last_warm = time.time()
            except Exception as e:
                log(f"home watch: {e}")
                TV.close()
            time.sleep(0.3)

    def step(self, warm_now: bool):
        host = client.Session.load().host
        if not self.tv_up(host):
            if self.up is not False:
                self.down_since = time.time()
            self.up, self.open_apps = False, set()
            return
        if self.up is False and time.time() - self.down_since > 20:
            # Back after a real absence (not a Wi-Fi blip): the TV was switched on.
            self.up = True
            threading.Thread(target=self.warm, daemon=True).start()
            time.sleep(CONFIG.get("power_on_delay_seconds", 5))
            self.open_apps = self.ask(host, ["Netflix"])
            if CONFIG.get("takeover_on_power_on", True) and not self.open_apps:
                self.open_launcher("the TV was switched on")
            return
        self.up = True
        if warm_now:
            threading.Thread(target=self.warm, daemon=True).start()
        if not self.open_apps:
            self.open_apps = self.ask(host, list(DIAL_APPS))
            return
        watched = set(self.open_apps) | {"Netflix"}
        now = self.ask(host, sorted(watched, key=lambda a: a != "Netflix"))
        for app, until in list(self.starting.items()):
            if app in now or time.time() > until:
                del self.starting[app]
            else:
                now.add(app)
        closed = self.open_apps - now
        if closed and not now:
            # A moment for a straight switch to Netflix to show; then the TV is on its home screen.
            time.sleep(0.8)
            now = self.ask(host, ["Netflix"] + sorted(closed))
            if not now:
                self.open_launcher(", ".join(sorted(closed)) + " closed")
        self.open_apps = now


HOME_WATCH = HomeWatch()


def main():
    try:
        APPS.refresh()
    except Exception as e:
        log(f"could not reach the TV yet ({e}); using the cached app list ({len(APPS.apps)} apps)")
    try:
        check_own_address()
    except Exception as e:
        log(f"address check failed: {e}")
    HOME_WATCH.start()
    if STREMIO.get("server", True) and STREMIO_APP:
        StremioServer().start()
    log("home takeover " + ("on" if takeover_on() else "off (VIDAA's own home)") +
        ": the launcher opens when the TV comes on or Netflix, YouTube or Prime Video closes")
    port = int(CONFIG.get("port", 8765))
    # On Windows, address reuse lets a second helper share the port with one still running; fail instead.
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    server = ThreadingHTTPServer(("0.0.0.0", port), partial(Handler, directory=str(WEB)))
    log(f"launcher on http://<this computer's ip>:{port}/  (serving {WEB})")
    server.serve_forever()


if __name__ == "__main__":
    main()
