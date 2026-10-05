#!/usr/bin/env bash
# Installs arch-auto-update: throttled, news-aware pacman auto-update + KDE status app.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing system files (requires sudo)..."
sudo install -m 755 -o root -g root "$HERE/bin/arch-update.sh" /usr/local/bin/arch-update.sh
sudo install -m 644 -o root -g root "$HERE/systemd/arch-update.service" /etc/systemd/system/arch-update.service
sudo install -m 644 -o root -g root "$HERE/systemd/arch-update.timer" /etc/systemd/system/arch-update.timer

echo "Installing the status app (requires sudo for the shared /usr/local/share path)..."
sudo mkdir -p /usr/local/share/arch-update-status
sudo install -m 755 -o root -g root "$HERE/app/arch_update_app.py" /usr/local/share/arch-update-status/arch_update_app.py
sudo install -m 644 -o root -g root "$HERE/polkit/com.pashweetie.arch-update-status.policy" /usr/share/polkit-1/actions/com.pashweetie.arch-update-status.policy

echo "Installing the KDE app-menu launcher (manual review, no sudo)..."
mkdir -p ~/.local/share/applications
install -m 644 "$HERE/applications/arch-update-status-launcher.desktop" ~/.local/share/applications/arch-update-status-launcher.desktop

echo "Installing the user-level systemd timer that launches the app with --auto (no sudo)..."
mkdir -p ~/.config/systemd/user
install -m 644 "$HERE/systemd-user/arch-update-status.service" ~/.config/systemd/user/arch-update-status.service
install -m 644 "$HERE/systemd-user/arch-update-status.timer" ~/.config/systemd/user/arch-update-status.timer

echo "Reloading system systemd and enabling the headless update timer..."
sudo systemctl daemon-reload
sudo systemctl enable --now arch-update.timer

echo "Reloading user systemd and enabling the app-launch timer..."
systemctl --user daemon-reload
systemctl --user enable --now arch-update-status.timer

cat <<'EOF'

Requires python-pyqt6 - install it first if missing:
  sudo pacman -S python-pyqt6

Done. There are now two independent systemd timers:
  - arch-update.timer (system, root)       -> runs arch-update.sh headless at boot + every 7 days
  - arch-update-status.timer (user, you)   -> launches the GUI app with --auto at login + every 7 days

Both apply the same 7-day throttle (shared stamp file), so whichever fires
first in a given week does the actual pacman run; the other is a no-op that
quarter.

The news gate is fully manual now: arch-update.sh pauses on ANY Arch news
post newer than your last acknowledgement (including the very first run -
there's no time-based guessing). Open the app's "Arch News" tab, read the
pending entry, and click "Acknowledge latest news & allow update" to let
the next run proceed.

If you had an old update-on-shutdown.service/.sh from a previous setup, remove it:
  sudo systemctl disable --now update-on-shutdown.service
  sudo rm -f /etc/systemd/system/update-on-shutdown.service /usr/local/bin/update-on-shutdown.sh
  sudo systemctl daemon-reload

Launch the status app anytime from the KDE app menu ("Arch Update Status")
for manual review (no auto-update), or run directly:
  python3 /usr/local/share/arch-update-status/arch_update_app.py
EOF
