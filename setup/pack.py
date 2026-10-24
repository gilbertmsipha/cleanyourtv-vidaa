"""Pack the helper for another computer: writes ../cyt-vidaa-kit.zip (cyt-vidaa + Sidee's core + the TV pairing).

The zip holds the TV pairing token: keep it private and delete it once the other computer is set up.
Usage: python setup/pack.py
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # cyt-vidaa
TV = ROOT.parent
OUT = TV / "cyt-vidaa-kit.zip"
SKIP = {"reports", ".venv", "__pycache__", "icons"}

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for base, prefix in ((ROOT, "cyt-vidaa"), (TV / "sidee" / "core", "sidee/core")):
        for f in base.rglob("*"):
            if f.is_file() and not SKIP & set(f.relative_to(base).parts) and not f.name.startswith("probe"):
                z.write(f, f"{prefix}/{f.relative_to(base).as_posix()}")
    z.write(TV / "sidee" / "sidee.py", "sidee/sidee.py")
    z.write(TV / "sidee" / "LICENSE", "sidee/LICENSE")
    session = Path.home() / ".sidee" / "session.json"
    if session.exists():
        z.write(session, "cyt-vidaa/setup/session.json")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
