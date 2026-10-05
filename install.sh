#!/usr/bin/env bash
# Installs arch-auto-update: throttled, news-aware pacman auto-update + KDE status app.
#
# Checks and installs required dependencies first so a new user never has to
# guess what's missing - run this on a bare Arch+KDE install and it should
# just work (aside from yay, which needs its own one-time AUR bootstrap - see
# the warning below if arch-update-yay.service matters to you).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Checking dependencies ==="

missing_pkgs=()
command -v curl >/dev/null 2>&1 || missing_pkgs+=(curl)
command -v pkexec >/dev/null 2>&1 || missing_pkgs+=(polkit)
python3 -c "import PyQt6" 2>/dev/null || missing_pkgs+=(python-pyqt6)

if [ "${#missing_pkgs[@]}" -gt 0 ]; then
  echo "Installing missing packages: ${missing_pkgs[*]}"
  sudo pacman -S --needed --noconfirm "${missing_pkgs[@]}"
else
  echo "All required packages (curl, polkit, python-pyqt6) already present."
fi

if ! command -v notify-send >/dev/null 2>&1; then
  echo "NOTE: notify-send not found (usually provided by libnotify) - desktop"
  echo "      notifications for a paused/blocked update won't appear, but the"
  echo "      app and log file still work fine without it."
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "ERROR: 'systemctl --user' isn't working in this shell." >&2
  echo "       This usually means you're not in a real login session (e.g." >&2
  echo "       running this over a bare SSH connection with no systemd user" >&2
  echo "       instance active yet). Log into your KDE session and re-run" >&2
  echo "       this script from a terminal there." >&2
  exit 1
fi

if [ ! -d /run/systemd/system ]; then
  echo "ERROR: systemd is not PID 1 on this system - this project requires" >&2
  echo "       systemd (system and user instances) and won't work otherwise." >&2
  exit 1
fi

echo ""
echo "=== Installing system files (requires sudo) ==="
sudo install -m 755 -o root -g root "$HERE/bin/arch-update.sh" /usr/local/bin/arch-update.sh
sudo install -m 644 -o root -g root "$HERE/systemd/arch-update.service" /etc/systemd/system/arch-update.service
sudo install -m 644 -o root -g root "$HERE/systemd/arch-update.timer" /etc/systemd/system/arch-update.timer

echo ""
echo "=== Installing the status app (requires sudo for the shared /usr/local/share path) ==="
sudo mkdir -p /usr/local/share/arch-update-status
sudo install -m 755 -o root -g root "$HERE/app/arch_update_app.py" /usr/local/share/arch-update-status/arch_update_app.py
sudo install -m 644 -o root -g root "$HERE/polkit/com.pashweetie.arch-update-status.policy" /usr/share/polkit-1/actions/com.pashweetie.arch-update-status.policy

echo ""
echo "=== Installing the KDE app-menu launcher (manual review, no sudo) ==="
mkdir -p ~/.local/share/applications
install -m 644 "$HERE/applications/arch-update-status-launcher.desktop" ~/.local/share/applications/arch-update-status-launcher.desktop

echo ""
echo "=== Installing the user-level systemd timer that launches the app with --auto (no sudo) ==="
mkdir -p ~/.config/systemd/user
install -m 644 "$HERE/systemd-user/arch-update-status.service" ~/.config/systemd/user/arch-update-status.service
install -m 644 "$HERE/systemd-user/arch-update-status.timer" ~/.config/systemd/user/arch-update-status.timer

echo ""
echo "=== Reloading system systemd and enabling the headless update timer ==="
sudo systemctl daemon-reload
sudo systemctl enable --now arch-update.timer

echo ""
echo "=== Reloading user systemd and enabling the app-launch timer ==="
systemctl --user daemon-reload
systemctl --user enable --now arch-update-status.timer

echo ""
if ! command -v yay >/dev/null 2>&1; then
  echo "NOTE: yay is not installed. arch-update.service triggers"
  echo "      arch-update-yay.service (an AUR update step) after every pacman"
  echo "      run, but that unit isn't part of this project and isn't"
  echo "      installed by this script. If you don't use yay/AUR, ignore this."
  echo "      If you do, install yay yourself (it needs its own bootstrap from"
  echo "      the AUR) and set up arch-update-yay.service separately."
fi

if [ -f /etc/systemd/system/update-on-shutdown.service ]; then
  echo ""
  echo "Found an old update-on-shutdown.service from a previous setup."
  echo "This project intentionally does not run updates at shutdown (you'd"
  echo "never see the result). Remove it with:"
  echo "  sudo systemctl disable --now update-on-shutdown.service"
  echo "  sudo rm -f /etc/systemd/system/update-on-shutdown.service /usr/local/bin/update-on-shutdown.sh"
  echo "  sudo systemctl daemon-reload"
fi

cat <<'EOF'

=== Done ===

Two independent systemd timers are now enabled:
  - arch-update.timer (system, root)       -> runs arch-update.sh headless at boot + every 7 days
  - arch-update-status.timer (user, you)   -> launches the GUI app with --auto at login + every 7 days

Both apply the same 7-day throttle (shared stamp file), so whichever fires
first in a given week does the actual pacman run; the other is a no-op that
cycle.

The news gate is fully manual: arch-update.sh pauses on ANY Arch news post
newer than your last acknowledgement, including the very first run - there's
no time-based guessing, no step for you to do by hand ahead of time. Open the
app's "Arch News" tab, read the pending entry, and click "Acknowledge latest
news & allow update" to let the next run proceed.

Launch the status app anytime from the KDE app menu ("Arch Update Status")
for manual review (no auto-update), or run directly:
  python3 /usr/local/share/arch-update-status/arch_update_app.py
EOF
