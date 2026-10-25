"""Start the helper with its output in data/helper.log, for the Windows logon task (no console window).

The task runs:  conhost.exe --headless python.exe -u setup/windows/run_helper.py
"""
import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "data" / "helper.log"
PID = ROOT / "data" / "helper.pid"
LOG.parent.mkdir(exist_ok=True)

# Stopping the task ends conhost but can leave this python running, still serving the old code: replace it.
if PID.exists():
    old = PID.read_text().strip()
    if old.isdigit():
        found = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={old}').CommandLine"],
                               capture_output=True, text=True).stdout
        if "run_helper.py" in found:
            subprocess.run(["taskkill", "/F", "/PID", old], capture_output=True)
PID.write_text(str(os.getpid()))
if LOG.exists() and LOG.stat().st_size > 1_000_000:
    LOG.replace(LOG.with_suffix(".old.log"))
sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
sys.argv = [str(ROOT / "helper" / "cyt_helper.py")]
runpy.run_path(sys.argv[0], run_name="__main__")
