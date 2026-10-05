#!/usr/bin/env bash
set -euo pipefail

LOG="/var/log/arch-update.log"
STAMP="/var/lib/arch-update.stamp"
NEWS_STAMP="/var/lib/arch-update-news.stamp"
NEWS_FLAG="/var/lib/arch-update-news-pending"
NEWS_FEED="https://archlinux.org/feeds/news/"
MIN_INTERVAL_SECS=$((7 * 24 * 60 * 60))  # 7 days: Arch wiki recommends not going longer than this between full upgrades on a rolling release

{
  echo "=== Arch update (pacman) started: $(date -Is) ==="

  # Prevent overlap
  exec 9>/run/arch-update.lock
  if ! flock -n 9; then
    echo "Another update is already running; exiting."
    exit 0
  fi

  # Shared throttle: skip if the last successful full update (from ANY trigger -
  # boot timer or daily/weekly timer) was less than 7 days ago.
  if [ -f "$STAMP" ]; then
    last=$(cat "$STAMP" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$((now - last))
    if [ "$age" -lt "$MIN_INTERVAL_SECS" ]; then
      remaining=$(( (MIN_INTERVAL_SECS - age) / 3600 ))
      echo "Last update was ${age}s ago (< 7d); skipping. ~${remaining}h remaining."
      exit 0
    fi
  fi

  # Best-effort: skip if offline
  if command -v ping >/dev/null 2>&1; then
    if ! ping -c 1 -W 1 1.1.1.1 >/dev/null 2>&1; then
      echo "Network appears down; skipping update."
      exit 0
    fi
  fi

  # Arch news check: refuse to auto-upgrade past an unread news post.
  # Arch's maintainers use the news feed for manual-intervention announcements
  # (keyring resets, package splits requiring a manual step, etc.) - these are
  # exactly the kind of unattended -Syu that bricks a rolling-release system.
  if command -v curl >/dev/null 2>&1; then
    latest_entry=$(curl -fsS --max-time 10 "$NEWS_FEED" 2>/dev/null || true)
    if [ -n "$latest_entry" ]; then
      # First <updated> or <pubDate> in the feed is the most recent entry.
      latest_date=$(printf '%s' "$latest_entry" | grep -o -m1 -E '<updated>[^<]+</updated>|<pubDate>[^<]+</pubDate>' | sed -E 's/<[^>]+>//g')
      latest_title=$(printf '%s' "$latest_entry" | grep -o -m1 -E '<title>[^<]+</title>' | tail -n +2 | head -n1 | sed -E 's/<[^>]+>//g')
      if [ -n "$latest_date" ]; then
        latest_epoch=$(date -d "$latest_date" +%s 2>/dev/null || echo 0)
        last_seen_epoch=0
        [ -f "$NEWS_STAMP" ] && last_seen_epoch=$(cat "$NEWS_STAMP" 2>/dev/null || echo 0)
        if [ "$latest_epoch" -gt "$last_seen_epoch" ] && [ "$last_seen_epoch" -ne 0 ]; then
          echo "New Arch news since last check: \"${latest_title:-<untitled>}\" ($latest_date)"
          echo "Refusing to auto-upgrade - review https://archlinux.org/news/ first."
          echo "$latest_title ($latest_date)" > "$NEWS_FLAG"
          command -v notify-send >/dev/null 2>&1 && notify-send -u critical "Arch auto-update paused" "New Arch news: ${latest_title:-check archlinux.org/news}" || true
          exit 0
        fi
        # No new entry (or this is the first run ever) - record it as seen and proceed.
        echo "$latest_epoch" > "$NEWS_STAMP"
      fi
    fi
  fi
  rm -f "$NEWS_FLAG"

  echo "[pacman] syncing + upgrading..."
  pacman -Syu --noconfirm

  date +%s > "$STAMP"
  echo "=== Arch update (pacman) finished: $(date -Is) ==="
  echo
} >> "$LOG" 2>&1
