"""Start the helper with its output in data/helper.log, for the Windows logon task (no console window).

The task runs:  conhost.exe --headless python.exe -u setup/windows/run_helper.py
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "data" / "helper.log"
LOG.parent.mkdir(exist_ok=True)
if LOG.exists() and LOG.stat().st_size > 1_000_000:
    LOG.replace(LOG.with_suffix(".old.log"))
sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
sys.argv = [str(ROOT / "helper" / "cyt_helper.py")]
runpy.run_path(sys.argv[0], run_name="__main__")
