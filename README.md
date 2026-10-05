# arch-auto-update

Unattended `pacman -Syu` auto-updates for Arch Linux, done safely:

- **One shared 7-day throttle** across every trigger (boot + periodic timer), so a
  full upgrade can't fire more than once a week no matter which trigger wakes it.
- **Arch news guard**: before upgrading, checks https://archlinux.org/feeds/news/
  for a new post since the last check. If there's unread news, it refuses to
  upgrade and raises a flag instead of blindly running `-Syu` past a
  manual-intervention announcement (keyring resets, package splits, etc.).
- **KDE status app**: a Plasma-menu-launchable app (and login autostart popup)
  showing last-update time and any pending-news warning.

## Why

Arch is a rolling release. The wiki's own guidance is to update often but
*attentively* — the actual bricking risk isn't update frequency, it's running
`--noconfirm` blind past a change that needed a manual step first. This script
doesn't make unattended updates perfectly safe (nothing can), but it closes the
one gap that's actually fixable without ditching automation entirely: visibility
into Arch's own news feed before the upgrade runs.

## Install

```bash
./install.sh
```

Installs:
- `/usr/local/bin/arch-update.sh` — the update script (throttle + news guard + pacman -Syu)
- `/usr/local/bin/arch-update-status-popup.sh` — zenity status popup
- `/etc/systemd/system/arch-update.{service,timer}` — boot + 7-day periodic trigger
- `~/.config/autostart/arch-update-status.desktop` — shows status popup at login
- `~/.local/share/applications/arch-update-status-launcher.desktop` — launchable from the KDE app menu as "Arch Update Status"

Requires `zenity` for the popup (install with `sudo pacman -S zenity` if missing).

**Before the first run**, check https://archlinux.org/news/ by hand once — the
news guard only blocks starting from its *second* run, since the first run has
nothing to compare against yet.

## Files

```
bin/
  arch-update.sh                  # the actual update logic
  arch-update-status-popup.sh     # zenity popup, reads the stamp/flag files
systemd/
  arch-update.service             # oneshot, runs arch-update.sh
  arch-update.timer               # OnBootSec=5min, OnUnitActiveSec=7d
autostart/
  arch-update-status.desktop      # XDG autostart entry (login popup)
applications/
  arch-update-status-launcher.desktop  # XDG app-menu entry (manual launch)
install.sh
```

## State files (runtime, not in this repo)

- `/var/lib/arch-update.stamp` — epoch of last successful upgrade (shared throttle)
- `/var/lib/arch-update-news.stamp` — epoch of last-seen Arch news entry
- `/var/lib/arch-update-news-pending` — present + contains the pending news title/date when upgrades are paused
- `/var/log/arch-update.log` — run log the status popup tails

## License

MIT
