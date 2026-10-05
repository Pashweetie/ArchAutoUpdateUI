#!/usr/bin/env bash
# Installs arch-auto-update: throttled, news-aware pacman auto-update + KDE status app.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing system files (requires sudo)..."
sudo install -m 755 -o root -g root "$HERE/bin/arch-update.sh" /usr/local/bin/arch-update.sh
sudo install -m 755 -o root -g root "$HERE/bin/arch-update-status-popup.sh" /usr/local/bin/arch-update-status-popup.sh
sudo install -m 644 -o root -g root "$HERE/systemd/arch-update.service" /etc/systemd/system/arch-update.service
sudo install -m 644 -o root -g root "$HERE/systemd/arch-update.timer" /etc/systemd/system/arch-update.timer

echo "Installing user files (no sudo)..."
mkdir -p ~/.config/autostart ~/.local/share/applications
install -m 644 "$HERE/autostart/arch-update-status.desktop" ~/.config/autostart/arch-update-status.desktop
install -m 644 "$HERE/applications/arch-update-status-launcher.desktop" ~/.local/share/applications/arch-update-status-launcher.desktop

echo "Reloading systemd and enabling timer..."
sudo systemctl daemon-reload
sudo systemctl enable --now arch-update.timer

cat <<'EOF'

Done. Before the first run, manually check https://archlinux.org/news/ once -
the news-check only blocks starting from its SECOND run (the first run just
records the current latest post as "seen").

If you had an old update-on-shutdown.service/.sh from a previous setup, remove it:
  sudo systemctl disable --now update-on-shutdown.service
  sudo rm -f /etc/systemd/system/update-on-shutdown.service /usr/local/bin/update-on-shutdown.sh
  sudo systemctl daemon-reload

Launch the status app anytime from the KDE app menu ("Arch Update Status"),
or run directly: /usr/local/bin/arch-update-status-popup.sh
EOF
