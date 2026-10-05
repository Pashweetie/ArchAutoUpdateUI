# arch-auto-update

Unattended `pacman -Syu` auto-updates for Arch Linux, done safely:

- **One shared 7-day throttle** across every trigger, so a full upgrade can't
  fire more than once a week no matter which trigger wakes it.
- **Arch news guard, fully manual gate**: before upgrading, checks
  https://archlinux.org/feeds/news/. Any post newer than your last
  acknowledgement — including the very first run ever — pauses the upgrade.
  There's no time-based "probably safe" guessing: you review the entry in the
  app and click **Acknowledge** to let the next run proceed.
- **PyQt6 status app**: a real KDE-menu-launchable application with three tabs
  — current status, a live-streaming update log, and the Arch news feed (with
  the Acknowledge button). Launching from the KDE menu opens status-only, no
  side effects. Launching with `--auto` immediately runs the update if it's
  due and the news gate is clear, with live output.
- **Two independent systemd timers, both 7-day throttled**:
  - `arch-update.timer` (system/root) — runs the headless update script at
    boot + every 7 days, with or without you logged in.
  - `arch-update-status.timer` (user) — launches the GUI app with `--auto` at
    login + every 7 days, so you actually *see* what's happening on your own
    cadence, independent of whether the headless timer already ran.

  Both share the same throttle stamp, so whichever fires first in a given
  week does the real `pacman -Syu`; the other is a no-op that cycle. A file
  lock in `arch-update.sh` makes concurrent firing safe either way.

## Why

Arch is a rolling release. The wiki's own guidance is to update often but
*attentively* — the actual bricking risk isn't update frequency, it's running
`--noconfirm` blind past a change that needed a manual step first. This setup
doesn't make unattended updates perfectly safe (nothing can), but it closes
the one gap that's actually fixable without ditching automation entirely:
guaranteed human visibility into Arch's own news feed before any upgrade runs,
plus an actual UI to review and act on it instead of a webpage you have to
remember to check.

## Install

```bash
./install.sh
```

Installs:
- `/usr/local/bin/arch-update.sh` — the update script (throttle + news guard + pacman -Syu)
- `/usr/local/share/arch-update-status/arch_update_app.py` — the PyQt6 status/update app
- `/usr/share/polkit-1/actions/com.pashweetie.arch-update-status.policy` — polkit action for update/acknowledge actions
- `/etc/systemd/system/arch-update.{service,timer}` — system timer, headless update, boot + 7-day
- `~/.config/systemd/user/arch-update-status.{service,timer}` — user timer, launches the app with `--auto`, login + 7-day
- `~/.local/share/applications/arch-update-status-launcher.desktop` — KDE app-menu entry, manual review only

Requires `python-pyqt6`: `sudo pacman -S python-pyqt6`

No manual pre-check of archlinux.org/news is needed — the news gate blocks on
its own from the very first run, and you clear it from inside the app.

## Files

```
bin/
  arch-update.sh                  # the actual update logic (throttle + news guard + pacman -Syu)
app/
  arch_update_app.py              # PyQt6 app: Status / Live Update / Arch News tabs
polkit/
  com.pashweetie.arch-update-status.policy  # lets update/acknowledge actions prompt cleanly via pkexec
systemd/
  arch-update.service             # system oneshot, runs arch-update.sh
  arch-update.timer                # system timer, OnBootSec=5min, OnUnitActiveSec=7d
systemd-user/
  arch-update-status.service      # user oneshot, launches the app with --auto
  arch-update-status.timer         # user timer, OnStartupSec=2min, OnUnitActiveSec=7d
applications/
  arch-update-status-launcher.desktop  # KDE app-menu entry, status view only (manual, no --auto)
install.sh
```

## State files (runtime, not in this repo)

- `/var/lib/arch-update.stamp` — epoch of last successful upgrade (shared throttle)
- `/var/lib/arch-update-news.stamp` — epoch of last-acknowledged Arch news entry (written only by the app's Acknowledge button)
- `/var/lib/arch-update-news-pending` — present + contains the pending news title/date when upgrades are paused
- `/var/log/arch-update.log` — run log the status app tails

## License

MIT
