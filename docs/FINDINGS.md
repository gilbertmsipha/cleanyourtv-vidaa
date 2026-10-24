# Findings: how a VIDAA TV can and can't be controlled

Everything below was measured on one TV: Hisense 50A5FE, VIDAA U9 (`Hisense_GetOSVersion` → `U09.09`, API 3.0.1), firmware V0000.09.09A.P0930, chipset MTK9602, October 2026. The probe pages in `web/probes/` reproduce each finding; please send results from other models.

## 1. Is it VIDAA, or Android underneath?

Some China-market sets report `VIDAA_TV` but run Android TV 11 (CleanYourTV has lists for them). A real VIDAA TV:

| Check | VIDAA (Linux) | Android / Google TV |
|---|---|---|
| `http://<tv>:7000/info` (AirPlay, binary plist) | `operatingSystem = Linux`, `model`, `firmwareRevision` | — |
| Port 36669 (remote-control MQTT/TLS) | open | — |
| Port 38400 (DLNA renderer, `/MediaServer/rendererdevicedesc.xml`, says `brand=his`, `vidaa_support=1`) | open | — |
| Ports 5555 (ADB), 8008/8009 (Cast), 6466 (Android TV Remote) | closed | open (5555 once enabled) |

## 2. The TV's web runtime

The launcher tile runs in VIDAA's browser: **Chrome 111** ("Odin" 111.5563), user agent `… Model/VIDAA-MT9602 VIDAA/9.0(Hisense;SmartTV;<model>;MTK9602/<firmware>;UHD;<model>;)`. Page is 1920×1080 at devicePixelRatio 1. CSS grid, gap, `backdrop-filter`, `filter: blur`, custom properties, `aspect-ratio`, ES2017+, `fetch`, `localStorage`, canvas all work, and the glass highlight runs smoothly. (Older VIDAA firmware ships Chrome 77, where [vidaa-launcher](https://github.com/Zfinix/vidaa-launcher) found layered CSS too slow and drew on one canvas.)

Remote keys arrive as normal `keydown` events: arrows 37–40, OK 13, Back 8, channel up/down 427/428. The remote's app buttons (Netflix, YouTube, Prime, Home) never reach the page; the system takes them. A `KeyboardEvent` the page dispatches stays inside the page.

### Globals the TV injects

`Hisense_Get*` (≈60 read functions: volume, network type, model, country, `Hisense_GetAdTargetEnable`, `Hisense_GetAdsID`, …), `vowOS` (`service`, `tvinfo`, `store`), `vowOSContext`, `omi_platform` / `opera_omi` (`sendPlatformMessage`), `oipfObjectFactory` (HbbTV/OIPF), `TvInfo_getParam/setParam`, `HiUtils_createRequest`, `KeyEvent.VK_*`. Not exposed to third-party pages: `Hisense_installApp`, `Hisense_uninstallApp`, `Hisense_getInstalledApps` (VIDAA's own pages use them).

### The local service bus

`vowOS.service` talks to the TV over **HTTP `localhost:9009/service/<name>`** (synchronous) and **WebSocket `wss://localhost:9888`** (`phoenix://service/<name>`), with an `identifier` header from `vowOSContext.getAppIdentifier()`. Both ports answer only on the TV itself. Only three services exist:

| Service | APIs seen | Use |
|---|---|---|
| `pkgmgr` | `getInstalledPkgs`, `install` | Lists system packages (`tv.vidaa.jsservice.*`, …) |
| `tvinfo` | one API per setting key (`soundVolume`, `kidsMode`, `brightness`, `initialData`, …); `setbiz` writes | Read and **write** TV settings: not used, don't experiment |
| `hiutils` | `getUIVersion`, `getOSVersion`, `getDabTool`, `setDabTool` | Version info |

No launch, key or app-manager API answered on any of the three (probe 5 asked for `launch`, `launchApp`, `startApp`, `sendKey`, `injectKey`, … with empty arguments).

## 3. Launching apps

| From | Method | Result |
|---|---|---|
| Our page | `omi_platform.sendPlatformMessage({type:"APPMessage", MsgType:"appControl", action:"startThirdApp", …})`, the exact message VIDAA's own web home ("V Home") sends | **Ignored**, silently: only VIDAA's own pages may |
| Our page | DIAL `POST :3367/apps/Netflix` | **403** for any page Origin, including `null` (DIAL's rule against web pages) |
| Our page | Load a web app's URL | **Works** for every web app (`storeType` 99 entries) |
| Another device | DIAL `POST :3367/apps/Netflix` with no Origin | Works (Netflix, `AmazonInstantVideo`; YouTube needs a YouTube Origin) |
| Another device | MQTT `ui_service/<client>/actions/launchapp` `{appId, name, url, urlType: 37, …}` | **Works for every app**, native and web |

So native apps need a second device on the network: the helper.

## 4. The remote-control channel (MQTT over TLS, port 36669)

Implemented by Sidee (`core/client.py`); this is what the helper relies on:

- **Pairing:** a 4-digit PIN on the TV. This TV refused the older "static" identity (`hisenseservice`) and paired with the "modern" one (credentials derived from the TV's clock, then access and refresh tokens).
- **App list:** `ui_service/.../actions/applist` → reply on `/remoteapp/mobile/<client>/ui_service/data/applist`. Entries have `appId`, `name`, `url` (a web address, or a native name such as `netflix`, `youtube`, `amazon`, `vidaa-plus`), `storeType` (98/99/100), `httpIcon` (`img.vidaahub.com`, 134–154 px squares, no CORS headers) and `isunInstalled` (true = removable).
- **Add a tile:** `ui_service/.../actions/uievent` with `{"type":"app_install","app_info":{Title, StoreType:99, Id, URL, Image, …}}`. **Tiles are immutable:** adding an existing id again is ignored, and a re-add can't rename or re-point a tile. The TV may store the URL as a placeholder (`HTTP_APP_DEFAULT_URL<id>`).
- **Launch:** `actions/launchapp` honours the `url` you send, so one tile can open different pages.
- **Inputs:** `actions/sourcelist` → `ui_service/data/sourcelist` (`sourceid`, `displayname`, `has_signal`); `actions/changesource {sourceid, sourcename}` switches.
- **Keys:** `/remoteapp/tv/remote_service/<client>/actions/sendkey` with `KEY_HOME`, `KEY_MENU`, … works.
- **Not answered for the modern identity:** `actions/gettvstate`, which [vidaa-launcher](https://github.com/Zfinix/vidaa-launcher) and hisensebar use to see "home screen" (`statetype: remote_launcher`). Nothing came back on any topic tried, so that home-detection trick doesn't work here.
- **Wildcard subscriptions deliver nothing** (`<client>/#`, `/remoteapp/mobile/#`): subscribe to exact topics.
- No uninstall action is known.
- A full connect costs ~3.2 s (TLS plus a clock lookup); keep one connection open.

## 5. DIAL (casting service)

`http://<tv>:3366/dd.xml` gives `Application-URL: http://<tv>:3367/apps/`. Known apps: `Netflix`, `AmazonInstantVideo`, `YouTube` (403 without `Origin: https://www.youtube.com`). Each answers `<state>running|stopped</state>`. **Netflix answers in 10–90 ms; YouTube in 1–2 s; Amazon in ~2 s**, and the server answers one request at a time. The helper's home takeover is built on this: watch only the open app, decide 0.8 s after it stops, about 2 s from Home pressed to launcher open.

## 6. Networking

- The TV does **not** resolve names (`<pc>.local`, plain NetBIOS names, `.lan`, `.home` all failed), so the helper's address in the tile must be an IP. That's why the helper detects a change of its own address and adds a new tile.
- The TV finds the helper by IP; the helper finds the TV by SSDP (`friendlyName`), so a new TV address is handled automatically.
- An https page can't `fetch` the http helper (mixed content), but can navigate to it. That gives the `/go?app=|source=|key=…&back=` round trip.
- Starlink (and any router without DHCP reservations) is fine: everything stays on the LAN; CGNAT doesn't matter.

## 7. Privacy readings

`Hisense_GetAdTargetEnable()` = 1 (interest-based ads on) and an advertising ID is present until switched off in the TV's privacy settings. The viewing-information (ACR) switch isn't exposed to pages, so it can't be checked from outside. `initialData` on `tvinfo` returns country, language, model, firmware, net type, kids/store/hotel mode. Identifiers were left out of all reports.

## 8. VIDAA's own web home ("V Home")

App `2562` in the app list is a web app at `tvmodules-vidaa.vidaahub.com/home-os/V1.0/index-home-os.html`. Its bundle is a good reference for VIDAA's internal APIs (the `appControl` message format, service hosts). Its server list is in [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md#1-block-ads-and-tracking-without-breaking-the-tv).
