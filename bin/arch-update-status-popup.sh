#!/usr/bin/env bash
# Shows a login popup summarizing the arch-update.sh / arch-update-news check state.
set -uo pipefail

STAMP="/var/lib/arch-update.stamp"
NEWS_FLAG="/var/lib/arch-update-news-pending"
LOG="/var/log/arch-update.log"

fmt_ago() {
  local then="$1" now secs days hours
  now=$(date +%s)
  secs=$((now - then))
  days=$((secs / 86400))
  hours=$(((secs % 86400) / 3600))
  if [ "$days" -gt 0 ]; then
    echo "${days}d ${hours}h ago"
  else
    echo "${hours}h ago"
  fi
}

if [ -f "$STAMP" ]; then
  last_epoch=$(cat "$STAMP" 2>/dev/null || echo 0)
  last_human=$(date -d "@$last_epoch" '+%Y-%m-%d %H:%M')
  last_ago=$(fmt_ago "$last_epoch")
else
  last_human="never"
  last_ago="n/a"
fi

# Last 10 lines of real activity (skip blank lines) for a quick "what happened" glance.
recent_log=""
if [ -r "$LOG" ]; then
  recent_log=$(tail -n 40 "$LOG" 2>/dev/null | grep -v '^$' | tail -n 10)
fi

if [ -f "$NEWS_FLAG" ]; then
  news_line=$(cat "$NEWS_FLAG" 2>/dev/null)
  title="⚠️ Arch auto-update PAUSED — news pending"
  body="Auto-update is holding off because of unread Arch news:

<b>${news_line}</b>

Review https://archlinux.org/news/ before upgrading manually (pacman -Syu).

Last successful auto-update: ${last_human} (${last_ago})

Recent log:
${recent_log}"
  zenity --warning --title="$title" --width=480 --text="$body" --no-wrap &
else
  title="Arch auto-update status"
  body="Last successful auto-update: <b>${last_human}</b> (${last_ago})

Recent log:
${recent_log}"
  zenity --info --title="$title" --width=480 --text="$body" --no-wrap &
fi
