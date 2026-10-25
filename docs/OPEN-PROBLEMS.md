# Open problems

Things this project could not solve, with what is known so far. Pull requests and findings welcome. Test on your own TV, change one thing at a time, and write down how to undo it.

## 1. Block ads and tracking without breaking the TV

**Wanted:** stop VIDAA's ads, interest-based advertising and viewing-information (ACR) tracking, as CleanYourTV does on Android by disabling packages.

**Why it's open:** VIDAA has no ADB, so the only lever is the network: DNS filtering or firewall rules. Blocking the wrong host can break the store, updates, the home screen or apps. One user reports that after Hisense's ACR servers were added to a popular blocklist, VIDAA TVs behind a Pi-hole stopped working ([Lemmy thread](https://lemmy.manganiello.tech/post/181114/42429)). Starlink's router also can't set a custom DNS for the home, so filtering means setting DNS on the TV itself (Network → IP settings → DNS manual) or running a DNS server on the helper.

**Known:** the servers VIDAA's own web home talks to (from its JavaScript bundle; the role is guessed from the name and **not verified**):

| Host | Likely role |
|---|---|
| `evt.vidaahub.com` | Event logging (telemetry) |
| `bas.vidaahub.com`, `geo-bas.vidaahub.com` | Base/analytics service and geolocation |
| `abtest-tv.vidaahub.com` | A/B tests |
| `recommend-ui.vidaahub.com` | Recommendations |
| `msg.vidaahub.com` | Push messages |
| `ssai.aniview.com` | Server-side ad insertion (third-party ad network) in VIDAA's free channels |
| `params-strm.vidaahub.com` | Streaming parameters |
| `ota-tv.vidaahub.com` | Firmware updates (keep) |
| `img.vidaahub.com` | App icons; **this launcher needs it** |
| `appstore-vidaa`, `app-tv`, `tvmodules-vidaa`, `odinstore-vidaa`, `oauth-auth`, `vidaa-base-auth` (`.vidaahub.com`) | Store, apps, home modules, sign-in (keep) |

In the TV: switch off the privacy options (Viewing Information Services, interest-based advertising). `Hisense_GetAdTargetEnable()` and `Hisense_GetAdsID()` from a probe page show the effect.

**A safe way forward, if you take it on:**
1. Run a logging DNS forwarder on the helper, set it as the TV's DNS with a public resolver as the second, and record what the TV looks up during a week of normal use.
2. Block one host at a time, then cold-boot the TV and check the store, updates, the home screen, casting and the main apps before the next one.
3. Publish the list that survived, with the firmware it was tested on.

## 2. Know when a web app closes

The helper reopens the launcher when Netflix, YouTube or Prime Video close (DIAL tells it), but a web app (Disney+, Stremio, Spotify…) or the TV Browser closing returns to VIDAA's home with no signal anywhere. That is why the Browser screen asks you to press Home and open the Home tile. Ideas not yet tried: MQTT topics this firmware publishes on its own (subscribe to exact names; wildcards deliver nothing); whether `gettvstate` answers the "static" identity on firmware that still accepts it; DLNA/UPnP events on port 38400.

## 3. Replace VIDAA's home outright

The Home button always goes to VIDAA's home; the takeover only follows it. Unknowns:
- Is there a hotel-mode or "start-up app" setting reachable through `tvinfo`? Writing settings there is risky. Read-only exploration first.
- VIDAA's newer web home ("V Home", app `2562`) is a web page. Can the system's home be pointed at a different one? Tiles are immutable over MQTT, so overwriting an existing app id doesn't work. Anything beyond that risks leaving the TV with no home: don't try it on a TV you can't factory-reset.

## 4. Launch native apps without a helper

The page can't: `sendPlatformMessage` is ignored for non-VIDAA pages, DIAL refuses web origins, and the local service bus has no launch API (see [FINDINGS.md](FINDINGS.md#3-launching-apps)). If a VIDAA web API that launches apps is found for third-party pages, the helper becomes optional.

## 5. Smaller ones

- **Detect that the TV was switched on** more reliably: if the TV keeps its network up in standby, port 36669 never closes and the power-on takeover doesn't fire.
- **Ad-free websites:** Brave Search's results have few ads, but the sites you open from them keep theirs; the TV Browser takes no extensions. A filtering proxy on the helper is possible in principle, but it would break logins and many sites, and setting a proxy on the TV affects every app.
- **Stremio's "error occurred while downloading"**, now and then during playback: most likely a torrent short of peers. Not yet traced in the server log.
- **Uninstall apps** from the launcher: no network API is known; removal only hides.
- **Stale hosted list:** `web/apps.json` on the static host is a snapshot; re-export after changing apps on the TV (the helper-served page is always live).
- **YouTube's web version** (`youtube.com/tv`) looks soft in VIDAA's browser; the helper opens the native app instead when it can.
- **Other VIDAA versions:** U5–U8 firmware may use Chrome 77, the static MQTT identity, and different service-bus services.
