"""Write web/apps.json, the app list the launcher uses without the helper (static mode).

Static mode opens apps by loading their web address, so it keeps apps with an http(s) address, plus the native apps in
config native_apps, which it opens through the helper when the tile's address names one (?helper=). The browser on the TV may not
read VIDAA's icons (no CORS), so each icon's colours are worked out here once, through the running helper.

Usage: python tools/export_static.py        (the helper must be running: python helper/cyt_helper.py)
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
HELPER = f"http://127.0.0.1:{CONFIG.get('port', 8765)}"

# The same sampling as index.html's sample(): edge colour, a 2 x 5 backdrop from the icon's outer columns, the most vivid colour for the glow.
SAMPLE_JS = """async (ids) => {
  const out = {};
  for (const id of ids) {
    const img = new Image();
    img.src = '/icon/' + id;
    try { await img.decode(); } catch (e) { continue; }
    const c = document.createElement('canvas'); c.width = c.height = 24;
    const x = c.getContext('2d'); x.drawImage(img, 0, 0, 24, 24);
    const px = x.getImageData(0, 0, 24, 24).data;
    let er = 0, eg = 0, eb = 0, en = 0, vr = 0, vg = 0, vb = 0, vw = 0;
    for (let yy = 0; yy < 24; yy++) for (let xx = 0; xx < 24; xx++) {
      const o = (yy * 24 + xx) * 4, r = px[o], g = px[o + 1], b = px[o + 2], a = px[o + 3];
      if (a < 200) continue;
      if (xx === 0 || xx === 23) { er += r; eg += g; eb += b; en++; }
      const mx = Math.max(r, g, b), mn = Math.min(r, g, b), sat = mx ? (mx - mn) / mx : 0, w = sat * sat * (mx / 255);
      vr += r * w; vg += g * w; vb += b * w; vw += w;
    }
    const s = document.createElement('canvas'); s.width = 2; s.height = 5;
    const sx = s.getContext('2d'), iw = img.naturalWidth, ih = img.naturalHeight;
    sx.drawImage(img, 0, 0, 2, ih, 0, 0, 1, 5); sx.drawImage(img, iw - 2, 0, 2, ih, 1, 0, 1, 5);
    out[id] = {
      bg: en > 20 ? `rgb(${Math.round(er / en)},${Math.round(eg / en)},${Math.round(eb / en)})` : null,
      backdrop: s.toDataURL(),
      glow: vw > 2 ? [Math.round(vr / vw), Math.round(vg / vw), Math.round(vb / vw)] : [150, 150, 160],
    };
  }
  return out;
}"""


def icon_url(app):
    raw = app.get("httpIcon") or ""
    return raw[raw.find("http"):] if "http" in raw else ""


def main():
    apps = json.loads((ROOT / "data" / "apps.json").read_text(encoding="utf-8"))
    # A native app with a web version (config web_urls, e.g. YouTube's youtube.com/tv) is opened at that address.
    web_urls = CONFIG.get("web_urls", {})
    apps = [dict(a, url=web_urls[str(a.get("appId"))]) if str(a.get("appId")) in web_urls else a for a in apps]
    hidden = set(CONFIG.get("hidden", []))
    # Native apps listed in config native_apps are kept too, flagged native: the page shows them only when it has a
    # helper to open them (?helper= in the tile's address).
    native = [str(x) for x in CONFIG.get("native_apps", [])]
    # Opened through the helper when there is one: YouTube's native app, Stremio served with the streaming server.
    helper_first = [str(x) for x in CONFIG.get("prefer_native_with_helper", [])]
    if (CONFIG.get("stremio") or {}).get("app_id"):
        helper_first.append(str(CONFIG["stremio"]["app_id"]))
    with urllib.request.urlopen(HELPER + "/api/sources", timeout=20) as r:
        sources = [{"id": s["sourceid"], "name": s.get("displayname", s["sourceid"]).replace("▶", "").strip()}
                   for s in json.load(r)["sources"]]
    # The TV Browser becomes the Browser tile, which needs the helper to hand it a search.
    browser_ids = [str(a["appId"]) for a in apps if a.get("url") == "browser"]
    keep = [a for a in apps if str(a.get("appId")) not in hidden and not str(a.get("appId")).startswith("cyt") and
            (str(a.get("url", "")).startswith(("http://", "https://")) or str(a.get("appId")) in native + browser_ids)]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(HELPER + "/tile.jpg", wait_until="domcontentloaded")  # any same-origin page will do
        colours = page.evaluate(SAMPLE_JS, [str(a["appId"]) for a in keep])
        browser.close()

    out = []
    for a in keep:
        entry = {"appId": str(a["appId"]), "name": a.get("name", "").replace("▶", "").split(" : ")[0].strip(),
                 "icon": icon_url(a)}
        if str(a["appId"]) in browser_ids:
            entry.update(name="Browser", native=True, browser=True)
        elif str(a["appId"]) in native:
            entry["native"] = True
        elif str(a["appId"]) in helper_first:
            entry["helperFirst"] = True
            entry["url"] = a["url"]
        else:
            entry["url"] = a["url"]
        entry.update({k: v for k, v in colours.get(str(a["appId"]), {}).items() if v})
        out.append(entry)
    data = {"apps": out,
            "dock": [str(x) for x in CONFIG.get("static_dock", [])],
            "helper_dock": [str(x) for x in CONFIG.get("helper_dock", [])],
            "sources": sources, "browser": CONFIG.get("browser") or {},
            "greeting_name": CONFIG.get("greeting_name", ""), "clock_24h": CONFIG.get("clock_24h", False)}
    (ROOT / "web" / "apps.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"web/apps.json: {len(out)} apps, {sum(1 for e in out if e.get('native'))} of them native")
    skipped = [a.get("name") for a in apps if a not in keep and str(a.get("appId")) not in hidden]
    print("left out:", ", ".join(skipped))


if __name__ == "__main__":
    sys.exit(main())
