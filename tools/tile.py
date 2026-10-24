"""Add, list and launch our own VIDAA launcher tiles, using Sidee's paired session.

Usage:
  python tools/tile.py list
  python tools/tile.py add APP_ID NAME URL [ICON_URL]
  python tools/tile.py launch APP_ID NAME URL

Pair first with Sidee (python sidee/sidee.py); the session lives in ~/.sidee/session.json.
Re-adding the same APP_ID replaces that tile rather than adding a second one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidee"))
from core import client  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    session = client.Session.load()
    cmd = argv[0]
    if cmd == "list":
        for a in client.list_tiles(session):
            print(f"{a.get('appId')} | {a.get('name')} | {a.get('url')}")
        return 0
    if cmd == "add" and len(argv) >= 4:
        app_id, name, url = argv[1:4]
        icon = argv[4] if len(argv) > 4 else ""
        apps = client.add_tile(session, app_id, name, url, icon)
        ok = any(client.tile_matches_request(a, app_id, url) for a in apps)
        print(("REGISTERED" if ok else "NOT confirmed by the TV") + f": {name} ({app_id}) -> {url}")
        return 0 if ok else 1
    if cmd == "launch" and len(argv) >= 4:
        client.launch_tile(session, *argv[1:4])
        print("launched")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
