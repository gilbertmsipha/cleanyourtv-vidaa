#!/usr/bin/env bash
# Install the Clean Your TV helper as an always-on service on a Linux PC (tested target: Arch / Omarchy).
# Run from the unpacked kit:  bash cyt-vidaa/setup/linux/install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/../.." && pwd)"          # .../cyt-vidaa
KIT="$(dirname "$DIR")"                              # holds cyt-vidaa/ and sidee/
[ -d "$KIT/sidee/core" ] || { echo "sidee/core is missing next to cyt-vidaa/"; exit 1; }

echo "1/5  Python environment"
command -v python3 >/dev/null || sudo pacman -S --needed --noconfirm python
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q "paho-mqtt==2.1.0"

echo "2/5  TV pairing"
mkdir -p "$HOME/.sidee"
if [ -f "$DIR/setup/session.json" ] && [ ! -f "$HOME/.sidee/session.json" ]; then
  install -m 600 "$DIR/setup/session.json" "$HOME/.sidee/session.json"
  rm -f "$DIR/setup/session.json"
fi
[ -f "$HOME/.sidee/session.json" ] || echo "   No pairing found: pair once with  $DIR/.venv/bin/python $KIT/sidee/sidee.py pair <tv ip>"

echo "3/5  Service (starts at boot, restarts if it stops)"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/cyt-helper.service" <<UNIT
[Unit]
Description=Clean Your TV helper (VIDAA launcher)
After=network-online.target

[Service]
ExecStart=$DIR/.venv/bin/python -u $DIR/helper/cyt_helper.py
WorkingDirectory=$DIR
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now cyt-helper.service
# Keep user services running without anyone logged in.
loginctl enable-linger "$USER" || sudo loginctl enable-linger "$USER"

echo "4/5  Firewall: let the TV reach port 8765"
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow 8765/tcp comment "Clean Your TV helper"
fi

echo "5/5  Never sleep (it must be awake for the TV)"
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n' \
  | sudo tee /etc/systemd/logind.conf.d/cyt-no-sleep.conf >/dev/null
echo "   (the lid setting applies after a reboot)"

echo
echo "Done. Live log:  journalctl --user -u cyt-helper -f"
echo "If this PC has a new address, the helper adds a new Home tile on the TV; delete the old one there."
