# Guidelines

The ground rules for changing this project, and for running it on your own TV. They come from what went wrong, or nearly did; [CHALLENGES.md](CHALLENGES.md) has the stories.

## Keep the TV safe

- **Never risk the TV.** No firmware flashing, no writing TV settings through the service bus (`tvinfo` `setbiz`), and no experiments with VIDAA's own home. A TV you can't factory-reset is a TV you can lose.
- **Don't block the TV's network traffic** (DNS or firewall) as part of this project. Blocking the wrong host can break the store, updates or the home screen. If you take it on anyway, follow the one-host-at-a-time method in [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md#1-block-ads-and-tracking-without-breaking-the-tv) and write down how to undo each step.
- **Back up first.** Save the app list (`python tools/tile.py list > apps-backup.txt`, kept outside the repository) before changing tiles or removing apps.
- **Tiles are permanent.** A tile's name and address can't be changed after it is added. Check the address (with the helper's IP) before you add one.
- **Test anything new on the TV through the helper,** and keep a way back. The VIDAA pill returns to VIDAA's own home and turns the takeover off.

## Keep it private

- **Never commit or post:** the TV's IP, MAC or serial number, the pairing (`setup/session.json`, `~/.sidee/session.json`), Wi-Fi names, or Stremio account details.
- `config.json`, `data/`, `reports/`, `web/apps.json`, `setup/session.json` and `*.zip` are git-ignored for that reason; keep them ignored. Share `config.example.json` instead.
- **The kit zip (`setup/pack.py`) contains the pairing token.** Keep it off public places, and copy it only to your own machines.
- **The repository is public.** Check every commit for the items above before pushing.
- Screenshots for issues: crop out the network panel and anything with an address in it.

## How the pieces fit

- **The page (`web/index.html`)** is one file with no build step. It must run on Chrome 111 (VIDAA U9) and degrade gracefully on older VIDAA (Chrome 77): ES5-style code, `var`, no frameworks, no optional chaining.
- **Two modes:** served live by the helper (it can call `/api/...`), or hosted on Vercel over https with `web/apps.json` (it must *navigate* to the helper's `/go`, never `fetch` it). Every feature needs to work, or be hidden, in both.
- **The helper (`helper/cyt_helper.py`)** uses only Python's standard library plus Sidee's core. It must keep running when the TV, the internet or Stremio is away: catch, log, retry.
- **Remote first:** everything must be reachable with arrows, OK and Back. No mouse, no hover, no key the remote doesn't send (see [FINDINGS.md](FINDINGS.md#2-the-tvs-web-runtime)).
- **Sizes** are for a 1920×1080 page at 1:1, and text must be readable from a sofa (24 px minimum, 30 px and up for anything you act on).

## Making a change

1. **Back up**, then change one thing at a time.
2. **Check it in headless Chromium at 1920×1080** (Playwright is installed with the export tool): drive it with the keyboard, take screenshots, and stub the final `/api/launch`, `/api/browse` or `/go` so the test doesn't take over the TV.
3. **Restart the helper** after editing it (Windows: end and run the "Clean Your TV helper" task; Linux: `systemctl --user restart cyt-helper`). `data/helper.pid` makes sure only one helper runs.
4. **Try it on the TV** from the Home tile. If the TV is in use, check that nothing is playing first (`data/stremio-server.log`, `data/helper.log`).
5. **Update the hosted page** if `web/` changed: `python tools/export_static.py`, then publish `index.html`, `apps.json`, `tile.jpg`, `vercel.json` and `api/suggest.js` (from `hosting/`). Compare the live files with your local ones afterwards.
6. **Write it down:** the README for how to use it, [FINDINGS.md](FINDINGS.md) for anything learned about the TV, [CHALLENGES.md](CHALLENGES.md) for what was hard, [OPEN-PROBLEMS.md](OPEN-PROBLEMS.md) for what is left.
7. **Rebuild the kit** (`python setup/pack.py`) if the Linux computer is to get the change.

## Writing for this project

- Code reads like the code around it: plain names, a short comment that says *why* where it isn't obvious, no dead code.
- Docs say what happens and what to do, in plain words, and give the number or message you will see. Mark guesses as guesses ("likely", "not verified").
- Credit the projects this builds on (see the README's Credits), and keep their licences.
