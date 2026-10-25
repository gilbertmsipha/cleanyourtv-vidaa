# Challenges, and how they were solved

What stood in the way while building this, in the order we ran into it, with what we tried and what worked. Read it before changing how something works: most odd-looking choices in the code are the answer to one of these. The measurements behind them are in [FINDINGS.md](FINDINGS.md); what is still unsolved is in [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md).

## 1. The TV isn't Android

**Problem:** [CleanYourTV](https://github.com/Gathin23/cleanyourtv) cleans Android TVs over ADB. The first step was checking whether this Hisense was one of the China-market sets that say "VIDAA" but run Android underneath.

**Finding:** it is real VIDAA (Linux). AirPlay reports `operatingSystem = Linux`; the remote-control port 36669 and VIDAA's DLNA renderer are open; ADB, Cast and the Android TV remote ports are closed. Nothing from CleanYourTV's Android approach applies.

**Solution:** keep CleanYourTV's idea and look (a calm launcher of your own apps) and rebuild it from what VIDAA allows: a web page as the launcher, and a small helper on a computer for what a page can't do.

**Also asked:** can Android TV run from a flash disk alongside VIDAA? No. The TV boots only Hisense-signed firmware, and flashing anything else risks a brick. A Google TV dongle on an HDMI port gives a real Android TV safely, and CleanYourTV works on it.

## 2. Getting our page onto the TV

**Problem:** VIDAA has no sideloading and its store is closed.

**Solution:** [Sidee](https://github.com/Empi9245/Sidee) pairs with the TV over its remote-control channel (MQTT over TLS, a 4-digit PIN) and adds web-app tiles. This TV refused the older "static" pairing identity and accepted the "modern" one.

**Catches found on the way:**
- **Tiles can't be changed.** Adding the same app id again is silently ignored, so a tile can't be renamed or re-pointed. The old "CYT Probe" tile can only be deleted on the TV. Choose the tile's address carefully the first time.
- **The TV doesn't resolve names** (`.local`, NetBIOS, `.lan` all failed), so the Home tile carries the helper's IP address. If the router hands the computer a new address, the helper notices on start and adds a new Home tile.
- **One tile can open many pages:** the URL sent with MQTT `launchapp` is honoured, which later made the Stremio and Browser features possible.

## 3. A web page can't open native apps

**Problem:** Netflix, Prime Video and the TV's inputs are native, and a page has no way to start them.

**Tried, from the page:** `omi_platform.sendPlatformMessage` with the exact `startThirdApp` message VIDAA's own home sends (ignored for third-party pages); DIAL `POST :3367/apps/Netflix` (403 for any page Origin); the local service bus on `localhost:9009/9888` (only `pkgmgr`, `tvinfo` and `hiutils`, no launch or key API).

**Solution:** the helper, a Python program on a computer on the same network, sends MQTT `launchapp`, `changesource` and `sendkey`. A cloud server can't do this because it can't reach the TV, so the helper has to be on the home network.

## 4. The hosted page can't talk to the helper

**Problem:** the launcher is hosted on Vercel over https so that it works when the computer is off. An https page may not `fetch` a plain-http address on the home network (mixed content).

**Solution:** the page *navigates* to the helper instead: `http://<pc>:8765/go?app=…&back=<launcher>`. The helper acts, shows a black "Opening…" page, and sends the browser back if nothing took over the screen. The same round trip later carried inputs, keys, the VIDAA switch and the Browser (`/go?browse=`). The helper also serves the same launcher live over http, where it can be called directly.

**Related:** VIDAA's icon server sends no CORS headers, so the page can't sample icon colours for the glow. The helper serves icons from its own address, and `tools/export_static.py` works the colours out once for the hosted list.

## 5. Making the launcher the home screen

**Problem:** the Home button always goes to VIDAA's home, and nothing tells a page or the helper that it was pressed.

**Tried:** MQTT `gettvstate`, which other projects use to detect the home screen. This firmware doesn't answer it for the modern identity. Wildcard MQTT subscriptions deliver nothing at all.

**Solution:** the helper's HomeWatch opens the launcher when the TV comes on (the remote port comes back) and when Netflix, YouTube or Prime Video stop. DIAL reports their running state; YouTube's needs `Origin: https://www.youtube.com`. Netflix answers in 10 to 90 ms, YouTube and Amazon in about 2 s, and DIAL answers one request at a time, so the helper watches only the app that is open. The VIDAA pill turns this takeover off for anyone who wants VIDAA's own home back.

**Still open:** web apps (Disney+, Spotify, the TV Browser) give no signal when they close.

## 6. Remote quirks

- The remote's app buttons (Netflix, YouTube, Prime, Home) never reach the page; only arrows, OK, Back and channel keys do.
- **Hold OK** for the tile menu had to work without relying on `keyup`, because some remotes send none. A second `keydown` while waiting means the key is repeating, which counts as a hold.

## 7. Ads and tracking

**Problem:** VIDAA shows ads and has interest-based advertising and viewing-information tracking. On VIDAA the only lever is the network (DNS or firewall), and blocking the wrong host can break the store, updates or the home screen. Users report VIDAA TVs that stopped working behind a Pi-hole.

**Decision:** we don't block anything on the TV. Its own privacy options (Viewing Information Services, interest-based advertising) can be switched off in its settings, and the server list we found is documented for anyone who wants to try carefully ([OPEN-PROBLEMS.md](OPEN-PROBLEMS.md)). The Browser feature reduces ads in a safe place, the search results (Brave), without touching the TV's network.

## 8. Windows: two helpers at once

**Problem:** after an update the launcher kept getting 404s for a page that existed. Stopping the scheduled task kills its console but leaves `python.exe` running. Windows also lets a second program bind a port that is in use (`SO_REUSEADDR` behaves differently there), so the old helper with old code was still answering on 8765.

**Solution:** `setup/windows/run_helper.py` writes `data/helper.pid` and, on start, ends the previous helper if that PID is still a helper. The helper turns address reuse off on Windows, so a second copy fails loudly instead of sharing the port.

## 9. Stremio: torrents, addons and transcoding

The TV's Stremio is the community build [Stremio for VIDAA](https://github.com/NoobyGains/stremio-vidaa-tv). Torrents and transcoding need Stremio's streaming server on a computer, and four things stood between the two:

| Problem | Cause | Solution |
|---|---|---|
| "Streaming server unavailable" | The app is served over https, and a page served over https may not call `http://<pc>:11470` | The helper serves its own copy of the app over http (`/stremio/`): fetched from the app's site, cached, refreshed when it changes, and still available offline |
| The server ignores the TV | Stremio's server only answers pages from Stremio's own sites. Its https address needs your account's key | The helper runs the server with `NO_CORS=1`, using Stremio desktop's runtime when it is installed, or Node.js otherwise. If Stremio desktop starts first, its own locked-down server holds the port; the helper warns and takes over once it is closed |
| Addons missing | Addons come with the Stremio account, and the helper's copy keeps its own sign-in (browser storage belongs to each address) | Sign in once on the TV with the QR code |
| "Transcoding" errors, video won't start | The TV's player asks for every audio track of a video at once (five on the test episode). The server transcodes one track of each kind at a time by default, so each request cancelled the one before | The helper sets the server's `transcodeTrackConcurrency` to 8 and checks it every 30 seconds. Video is repackaged, not re-encoded, so it is light on the computer |

**Still seen:** an occasional "error occurred while downloading", usually a torrent short of peers. Another source of the same title usually plays.

## 10. A browser on a TV that can't install one

**Problem:** we wanted Brave on the TV for better search and fewer ads. VIDAA can't install other browsers, and a website opened as an app, the way the launcher opens pages, gets only arrow-key focus: you can't click links on an ordinary website.

**How we found the way:** the app list showed the built-in TV Browser as app `16` (`url: "browser"`). The question was whether it would open an address we chose. We couldn't see the TV screen from the computer, so we ran a small web server that logs each visitor, and asked the TV Browser to open it. The TV loaded it seconds later, reporting itself as plain Chrome 111. So the TV Browser opens any address over MQTT, and it has a pointer.

**Solution:** the launcher does the typing (an on-screen keyboard built for the remote, suggestions, recent searches, quick links), and the helper opens the result in the TV Browser.

**Smaller problems along the way:**
- **Brave's suggestion feed** answers only Brave's own pages (CORS), and it returned *429 Too Many Requests* to Python and to servers while still answering curl, which suggests it looks at the client fingerprint. Suggestions come from DuckDuckGo's feed instead (same format, dependable), through the helper or a small Vercel function. Searches still go to Brave.
- **American suggestions:** "weather joh" suggested Johnstown, PA before Johannesburg. DuckDuckGo's region code (`kl=za-en`) fixed it, and `browser.region` sets it.
- **A crowded header:** a fifth header button pushed long dates ("Wednesday 30 September") under the buttons. The Search button became a round icon, and the date shortens to "Wed 30 Sep" only when it would collide. The first measurement ran before the Outfit font loaded and shortened every date, so the fit is measured again once fonts are ready.
- **Getting back:** the TV gives no signal when its browser closes, so the launcher can't reappear by itself. You press Home, then open the Home tile.

## 11. Working without seeing the screen

Most of this was built from a computer that can't see the TV. What made it workable:
- **Probe pages** (`web/probes/`) run checks on the TV and send their findings back to the helper (`reports/`).
- **Logging servers:** to learn whether the TV opened something, give it an address on the computer and watch for the request.
- **Headless Chromium at 1920×1080** (Playwright) for every launcher change: drive it with arrow keys, take screenshots, and intercept the final step so tests don't take over the real TV.
- **Logs:** `data/helper.log` and `data/stremio-server.log` answered most "it doesn't work" reports. The transcoding bug came straight from the server log.

## 12. Deploying without a command line

There was no Vercel CLI on the computer. The site is deployed through the Vercel API with each file sent in full, and the tile image referenced by its hash. After each deployment, the live files are compared with the local ones (`diff <(curl -s <site>/) web/index.html`), so what the TV loads is exactly what was tested.
