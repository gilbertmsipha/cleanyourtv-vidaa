"""Features the helper adds on top of the TV: ad-free YouTube and free live TV channels.

YouTube: yt-dlp finds a video's streams (no ads are fetched: ads are separate videos the YouTube player asks for), and
ffmpeg joins the H.264 video and AAC audio into one fragmented MP4 that the TV's <video> plays. Nothing is re-encoded,
so the laptop's CPU barely notices. Seeking restarts the stream at the new time (?t=).

Live TV: the free, public channel lists of github.com/iptv-org/iptv (M3U, the same lists VLC opens). Streams are HLS;
the page plays them with hls.js through /proxy here, since most channel servers don't send CORS headers.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urljoin

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _get(url: str, timeout: float = 15, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- YouTube

class YouTube:
    def __init__(self, cfg: dict, data: Path, log):
        self.cfg, self.data, self.log = cfg, data, log
        self.max_height = int(cfg.get("max_height", 2160))
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()
        self.ffmpeg = shutil.which("ffmpeg")
        self._ydl = None

    def ydl(self, flat: bool = False):
        import yt_dlp  # imported late: the helper runs without it, just without YouTube
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                "js_runtimes": {"node": {}}}
        if flat:
            opts["extract_flat"] = "in_playlist"
        return yt_dlp.YoutubeDL(opts)

    @staticmethod
    def _card(e: dict) -> dict | None:
        vid = e.get("id")
        if not vid or len(vid) != 11 or e.get("live_status") == "is_upcoming":
            return None
        return {"id": vid, "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
                "duration": int(e.get("duration") or 0), "views": e.get("view_count"),
                "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"}

    def search(self, q: str, n: int = 30) -> list:
        with self.ydl(flat=True) as y:
            got = y.extract_info(f"ytsearch{n}:{q[:200]}", download=False)
        return [c for c in (self._card(e) for e in got.get("entries") or []) if c]

    def channel_feed(self, channel_id: str) -> list:
        """Recent uploads of a channel, from its RSS feed: quick, and no yt-dlp needed."""
        root = ET.fromstring(_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={quote(channel_id)}"))
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
              "m": "http://search.yahoo.com/mrss/"}
        out = []
        for e in root.findall("a:entry", ns):
            vid = e.findtext("yt:videoId", "", ns)
            out.append({"id": vid, "title": e.findtext("a:title", "", ns),
                        "channel": e.findtext("a:author/a:name", "", ns), "duration": 0,
                        "published": e.findtext("a:published", "", ns),
                        "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"})
        return out

    def home(self) -> list:
        """Rows for the start screen, kept for 30 minutes (building them takes ~10 s); refreshed in the background."""
        hit = getattr(self, "_home", None)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
        if hit:
            threading.Thread(target=self._build_home, daemon=True).start()
            return hit[1]
        return self._build_home()

    def _build_home(self) -> list:
        """Each configured channel's latest uploads, then configured searches."""
        rows = []
        for ch in self.cfg.get("channels", []):
            try:
                rows.append({"title": ch.get("name", "Channel"), "items": self.channel_feed(ch["id"])[:15]})
            except Exception as e:
                self.log(f"YouTube channel {ch.get('name')}: {e}")
        for q in self.cfg.get("home_searches", ["trending music videos", "news today", "comedy"]):
            try:
                rows.append({"title": q[:1].upper() + q[1:], "items": self.search(q, 15)})
            except Exception as e:
                self.log(f"YouTube search {q}: {e}")
        rows = [r for r in rows if r["items"]]
        if rows:
            self._home = (time.time(), rows)
        return rows

    def info(self, vid: str) -> dict:
        """The streams to play: the best H.264 video up to max_height, and AAC audio. Cached; YouTube's links last ~6 h."""
        if not re.fullmatch(r"[\w-]{11}", vid):
            raise ValueError("bad video id")
        with self.lock:
            hit = self.cache.get(vid)
            if hit and time.time() - hit[0] < 3 * 3600:
                return hit[1]
        with self.ydl() as y:
            got = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        fmts = got.get("formats") or []
        https = [f for f in fmts if f.get("protocol") == "https" and f.get("url")]
        video = [{"url": f["url"], "codec": (f.get("vcodec") or "").split(".")[0], "height": f.get("height") or 0,
                  "fps": f.get("fps") or 0, "tbr": f.get("tbr") or 0}
                 for f in https if f.get("acodec") in (None, "none") and (f.get("vcodec") or "none") != "none"]
        audio = [f for f in https if (f.get("acodec") or "").startswith("mp4a") and f.get("vcodec") in (None, "none")]
        audio.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0)
        muxed = [f for f in https if (f.get("vcodec") or "").startswith("avc1") and (f.get("acodec") or "").startswith("mp4a")]
        hls = [f for f in fmts if "m3u8" in (f.get("protocol") or "") and (f.get("vcodec") or "").startswith("avc1")]
        hls.sort(key=lambda f: f.get("height") or 0)
        out = {"id": vid, "title": got.get("title", ""), "channel": got.get("channel") or got.get("uploader", ""),
               "duration": int(got.get("duration") or 0), "live": bool(got.get("is_live")),
               "description": (got.get("description") or "")[:600], "videos": video,
               "audio": audio[-1]["url"] if audio else None, "muxed": muxed[-1]["url"] if muxed else None,
               "hls": [{"url": f["url"], "height": f.get("height") or 0} for f in hls],
               "chapters": [{"t": int(c.get("start_time") or 0), "title": c.get("title", "")} for c in got.get("chapters") or []]}
        with self.lock:
            self.cache[vid] = (time.time(), out)
            if len(self.cache) > 200:
                self.cache.pop(next(iter(self.cache)))
        return out

    def related(self, vid: str) -> list:
        """'Up next': more from the same channel and a search on the title, for the end of a video."""
        meta = self.info(vid)
        try:
            return [c for c in self.search(meta["title"][:80], 12) if c["id"] != vid]
        except Exception:
            return []

    def pick(self, vid: str, codecs: list, max_height: int) -> dict | None:
        """The best video stream the TV says it can decode (codecs, best first: av01, vp09, avc1), up to max_height.
        Height wins over codec: 4K VP9 beats 1080p H.264, but at equal height the more efficient codec is used."""
        rank = {c: i for i, c in enumerate(reversed(codecs))}
        ok = [v for v in self.info(vid)["videos"] if v["codec"] in rank and v["height"] <= max_height]
        ok.sort(key=lambda v: (v["height"], v["fps"], rank[v["codec"]], v["tbr"]))
        return ok[-1] if ok else None

    def public(self, vid: str, codecs: list, max_height: int) -> dict:
        meta = self.info(vid)
        v = self.pick(vid, codecs, max_height)
        return {k: meta[k] for k in ("id", "title", "channel", "duration", "live", "description", "chapters")} |             {"height": v["height"] if v else 360, "codec": v["codec"] if v else "avc1", "fps": v["fps"] if v else 30}

    def stream_command(self, vid: str, start: float, codecs: list | None = None, max_height: int = 0) -> list:
        """ffmpeg joining the video and audio streams, from `start` seconds, as a fragmented MP4 on stdout."""
        meta = self.info(vid)
        v = self.pick(vid, codecs or ["avc1"], max_height or self.max_height)
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is not installed")
        seek = ["-ss", f"{start:.2f}"] if start > 0 else []
        net = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "4", "-user_agent", UA]
        if meta["live"] and meta["hls"]:
            fit = [h for h in meta["hls"] if h["height"] <= (max_height or self.max_height)] or meta["hls"]
            inputs = net[:-2] + ["-i", fit[-1]["url"]]
            maps = []
        elif v and meta["audio"]:
            inputs = net + seek + ["-i", v["url"]] + net + seek + ["-i", meta["audio"]]
            maps = ["-map", "0:v:0", "-map", "1:a:0"]
        elif meta["muxed"]:
            inputs = net + seek + ["-i", meta["muxed"]]
            maps = []
        else:
            raise RuntimeError("no stream the TV can play")
        return [self.ffmpeg, "-hide_banner", "-loglevel", "error", *inputs, *maps, "-c", "copy",
                "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-frag_duration", "1000000", "pipe:1"]


# ---------------------------------------------------------------- Live TV

IPTV = "https://iptv-org.github.io/iptv/"
# Rows on the Live TV screen: these countries' channels first, then the channels in these languages by category.
DEFAULT_COUNTRIES = [{"name": "Zimbabwe", "code": "zw"}, {"name": "South Africa", "code": "za"}]
DEFAULT_LANGS = ["eng"]
CATEGORY_ORDER = ["News", "Movies", "Series", "Sports", "Entertainment", "Music", "Kids", "Documentary", "Comedy",
                  "Animation", "Lifestyle", "Cooking", "Travel", "Science", "Education", "Classic", "Business",
                  "Weather", "Family", "General", "Culture", "Outdoor", "Relax", "Religious", "Shop", "Undefined"]


def parse_m3u(text: str) -> list:
    out, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            cur = {"name": line.rsplit(",", 1)[-1].strip(), "logo": attrs.get("tvg-logo", ""),
                   "id": attrs.get("tvg-id", ""), "group": attrs.get("group-title", ""), "opts": {}}
        elif line.startswith("#EXTVLCOPT") and cur is not None:
            k, _, v = line[len("#EXTVLCOPT:"):].partition("=")
            if k in ("http-referrer", "http-user-agent"):
                cur["opts"][k] = v
        elif line and not line.startswith("#") and cur is not None:
            cur["url"] = line
            out.append(cur)
            cur = None
    return out


class LiveTV:
    def __init__(self, cfg: dict, data: Path, log):
        self.cfg, self.log = cfg, log
        self.cache_file = data / "livetv.json"
        self.dead_file = data / "livetv-dead.json"
        self.lock = threading.Lock()
        self.groups: list = []
        self.loaded = 0.0
        self.dead: dict = json.loads(self.dead_file.read_text(encoding="utf-8")) if self.dead_file.exists() else {}
        self.cors_file = data / "livetv-cors.json"
        self.cors: set = set(json.loads(self.cors_file.read_text(encoding="utf-8"))) if self.cors_file.exists() else set()
        if self.cache_file.exists():
            saved = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self.groups, self.loaded = saved["groups"], saved["loaded"]

    def _list(self, path: str) -> list:
        chans = parse_m3u(_get(IPTV + path, 30).decode("utf-8", "replace"))
        seen, keep = set(), []
        for c in chans:
            if c["url"] in seen or "[Geo-blocked]" in c["name"]:
                continue
            seen.add(c["url"])
            c["name"] = re.sub(r"\s*\(\d+p\)", "", c["name"]).strip()
            keep.append(c)
        return keep

    def refresh(self):
        groups = []
        for country in self.cfg.get("countries", DEFAULT_COUNTRIES):
            try:
                groups.append({"name": country["name"], "channels": self._list(f"countries/{country['code']}.m3u")})
            except Exception as e:
                self.log(f"Live TV {country['name']}: {e}")
        by_cat: dict[str, list] = {}
        for lang in self.cfg.get("languages", DEFAULT_LANGS):
            try:
                for c in self._list(f"languages/{lang}.m3u"):
                    for cat in (c["group"] or "Undefined").split(";"):
                        by_cat.setdefault(cat.strip() or "Undefined", []).append(c)
            except Exception as e:
                self.log(f"Live TV language {lang}: {e}")
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        for cat in sorted(by_cat, key=lambda c: (order.get(c, len(order)), c)):
            groups.append({"name": "Other" if cat == "Undefined" else cat, "channels": by_cat[cat]})
        groups = [g for g in groups if g["channels"]]
        if groups:
            with self.lock:
                self.groups, self.loaded = groups, time.time()
            self.cache_file.write_text(json.dumps({"groups": groups, "loaded": self.loaded}), encoding="utf-8")
            self.log(f"Live TV: {sum(len(g['channels']) for g in groups)} channels in {len(groups)} rows")

    def get(self) -> list:
        if time.time() - self.loaded > 12 * 3600:
            if self.groups:
                threading.Thread(target=self.refresh, daemon=True).start()
            else:
                self.refresh()
        with self.lock:
            return [{"name": g["name"], "channels": [dict(c, dead=self.dead.get(c["url"], 0) >= 2, direct=c["url"] in self.cors)
                                                     for c in g["channels"]]} for g in self.groups]

    def sweep(self):
        """Check every channel once (its playlist answers, or not), so offline ones sink to the end before anyone
        tunes to them. Run daily; ~3,000 small requests, 12 at a time."""
        from concurrent.futures import ThreadPoolExecutor
        chans = {c["url"]: c for g in self.get() for c in g["channels"]}

        def check(c):
            o = c.get("opts") or {}
            ok, cors = probe(c["url"], o.get("http-referrer", ""), o.get("http-user-agent", ""))
            return c["url"], ok, cors

        session()       # fails here, before any channel is marked, if requests is missing
        t = time.time()
        with ThreadPoolExecutor(12) as pool:
            results = list(pool.map(check, chans.values()))
        with self.lock:
            for url, ok, cors in results:
                if ok:
                    self.dead.pop(url, None)
                else:
                    self.dead[url] = max(2, self.dead.get(url, 0))
            self.cors = {url for url, ok, cors in results if cors}
            self.dead_file.write_text(json.dumps(self.dead), encoding="utf-8")
            self.cors_file.write_text(json.dumps(sorted(self.cors)), encoding="utf-8")
        self.log(f"Live TV check: {sum(r[1] for r in results)} of {len(results)} channels on air, "
                 f"{len(self.cors)} playable directly ({int(time.time() - t)} s)")

    def sweeper(self):
        while True:
            try:
                self.sweep()
            except Exception as e:
                self.log(f"Live TV check: {e}")
            time.sleep(24 * 3600)

    def report(self, url: str, ok: bool):
        """The page says whether a channel played; one that fails twice in a row is listed last and dimmed."""
        with self.lock:
            if ok:
                self.dead.pop(url, None)
            else:
                self.dead[url] = self.dead.get(url, 0) + 1
            self.dead_file.write_text(json.dumps(self.dead), encoding="utf-8")


# One pooled HTTP session: channel servers keep the connection open, so each 2-6 s segment doesn't pay for a new
# TCP + TLS handshake (that was a good part of the pauses).
_session = None
_session_lock = threading.Lock()


def session():
    global _session
    with _session_lock:
        if _session is None:
            import requests  # comes with yt-dlp
            _session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=1)
            _session.mount("http://", adapter)
            _session.mount("https://", adapter)
        return _session


class SegmentCache:
    """Recent stream segments in memory. When a playlist passes through, its newest segments are fetched ahead, so
    the TV's next requests are answered from here at once instead of waiting on the channel's server."""

    def __init__(self, max_bytes: int = 96 * 1024 * 1024, ttl: float = 180):
        from collections import OrderedDict
        self.items, self.size, self.max, self.ttl = OrderedDict(), 0, max_bytes, ttl
        self.inflight = {}
        self.lock = threading.Lock()

    def get(self, url: str):
        with self.lock:
            hit = self.items.get(url)
            if hit and time.time() - hit[0] < self.ttl:
                self.items.move_to_end(url)
                return hit[1], hit[2]
        return None

    def put(self, url: str, ctype: str, body: bytes):
        with self.lock:
            if url in self.items:
                self.size -= len(self.items.pop(url)[2])
            self.items[url] = (time.time(), ctype, body)
            self.size += len(body)
            while self.size > self.max and self.items:
                self.size -= len(self.items.popitem(last=False)[1][2])

    def claim(self, url: str):
        """None if this caller should fetch; otherwise the event to wait on for another caller's fetch."""
        with self.lock:
            ev = self.inflight.get(url)
            if ev:
                return ev
            self.inflight[url] = threading.Event()
            return None

    def release(self, url: str):
        with self.lock:
            ev = self.inflight.pop(url, None)
        if ev:
            ev.set()


SEGMENTS = SegmentCache()
PREFETCH = 6   # segments fetched ahead from each live playlist


def _headers(referer: str, ua: str) -> dict:
    h = {"User-Agent": ua or UA}
    if referer:
        h["Referer"] = referer
    return h


def _fetch_segment(url: str, headers: dict, timeout: float):
    """A segment, from the cache, from a fetch already under way, or fetched now. Returns (ctype, bytes)."""
    for _ in range(2):
        hit = SEGMENTS.get(url)
        if hit:
            return hit
        ev = SEGMENTS.claim(url)
        if ev is None:
            break
        ev.wait(timeout)
    else:
        hit = SEGMENTS.get(url)
        if hit:
            return hit
        SEGMENTS.claim(url)
    try:
        r = session().get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        out = (r.headers.get("Content-Type", "video/mp2t").split(";")[0], r.content)
        SEGMENTS.put(url, *out)
        return out
    finally:
        SEGMENTS.release(url)


def _prefetch(urls: list, headers: dict):
    for u in urls:
        if SEGMENTS.get(u) is None:
            threading.Thread(target=_safe, args=(_fetch_segment, u, headers, 20), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


def proxy_fetch(url: str, referer: str = "", ua: str = "", timeout: float = 15):
    """Fetch a stream file for the page: (status, content type, bytes or an iterator of chunks).
    Playlists are rewritten so every link inside comes back through /proxy; segments are cached and prefetched."""
    headers = _headers(referer, ua)
    hit = SEGMENTS.get(url)
    if hit:
        return 200, hit[0], hit[1]
    if SEGMENTS.inflight.get(url):          # being prefetched right now: wait for it rather than fetch twice
        ctype, body = _fetch_segment(url, headers, timeout)
        return 200, ctype, body
    r = session().get(url, headers=headers, timeout=timeout, stream=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    final = r.url
    if "mpegurl" in ctype or final.split("?")[0].lower().endswith(".m3u8") or url.split("?")[0].lower().endswith(".m3u8"):
        text = r.content.decode("utf-8", "replace")
        extra = (f"&ref={quote(referer, safe='')}" if referer else "") + (f"&ua={quote(ua, safe='')}" if ua else "")
        segs = []

        def wrap(u: str, seg: bool = False) -> str:
            full = urljoin(final, u)
            if seg:
                segs.append(full)
            return "/proxy?u=" + quote(full, safe="") + extra

        lines, after_inf = [], False
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                lines.append(wrap(s, after_inf and ".m3u8" not in s.lower()))
                after_inf = False
            elif 'URI="' in s:
                lines.append(re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + wrap(m.group(1)) + '"', line))
            else:
                after_inf = after_inf or s.startswith("#EXTINF")
                lines.append(line)
        if segs:
            _prefetch(segs[-PREFETCH:], headers)
        return 200, "application/vnd.apple.mpegurl", "\n".join(lines).encode()
    size = int(r.headers.get("Content-Length") or 0)
    if 0 < size <= 32 * 1024 * 1024:
        body = r.content
        SEGMENTS.put(url, ctype or "video/mp2t", body)
        return 200, ctype or "video/mp2t", body

    def chunks():  # an endless stream (some channels are plain MPEG-TS or audio): pass it on as it comes
        try:
            for b in r.iter_content(65536):
                yield b
        finally:
            r.close()
    return 200, ctype or "application/octet-stream", chunks()


def probe(url: str, referer: str = "", ua: str = "", timeout: float = 8):
    """(answers, allows other sites): whether a channel's playlist loads, and whether its server sends CORS headers,
    in which case the TV can fetch it directly instead of through this computer."""
    s = session()   # an ImportError here is ours, not the channel's: let it stop the check
    try:
        r = s.get(url, headers=_headers(referer, ua), timeout=timeout, stream=True)
        ok = r.status_code < 400
        cors = r.headers.get("Access-Control-Allow-Origin", "") == "*"
        r.close()
        return ok, ok and cors
    except Exception:
        return False, False
