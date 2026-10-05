#!/usr/bin/env bash
set -euo pipefail

LOG="/var/log/arch-update.log"
STAMP="/var/lib/arch-update.stamp"
NEWS_STAMP="/var/lib/arch-update-news.stamp"
NEWS_FLAG="/var/lib/arch-update-news-pending"
NEWS_FEED="https://archlinux.org/feeds/news/"
MIN_INTERVAL_SECS=$((7 * 24 * 60 * 60))  # 7 days: Arch wiki recommends not going longer than this between full upgrades on a rolling release

# Always leave an explicit trace if this run doesn't reach its own normal
# RESULT line - covers a hang, OOM kill, systemd TimeoutStartSec kill, power
# loss, Ctrl-C, etc. Without this, an interrupted run just goes silent
# mid-stream in the log with no indication anything went wrong.
run_finished=0
on_exit() {
  local exit_code=$?
  if [ "$run_finished" -eq 0 ]; then
    {
      echo "RESULT: INTERRUPTED - run did not complete normally (exit code $exit_code). Check for a stale lock: /run/arch-update.lock, and a stale pacman db lock: /var/lib/pacman/db.lck"
      echo "────────────────────────────────────────────────────────────"
    } >> "$LOG" 2>&1
  fi
}
trap on_exit EXIT

{
  run_start_epoch=$(date +%s)
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  Run started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "════════════════════════════════════════════════════════════"

  # Prevent overlap
  exec 9>/run/arch-update.lock
  if ! flock -n 9; then
    echo "RESULT: skipped - another update is already running"
    run_finished=1
    exit 0
  fi

  # Shared throttle: skip if the last successful full update (from ANY trigger -
  # boot timer or daily/weekly timer) was less than 7 days ago.
  if [ -f "$STAMP" ]; then
    last=$(cat "$STAMP" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$((now - last))
    if [ "$age" -lt "$MIN_INTERVAL_SECS" ]; then
      remaining_h=$(( (MIN_INTERVAL_SECS - age) / 3600 ))
      echo "RESULT: skipped - last update was $((age / 3600))h ago, throttle allows one per 7d (~${remaining_h}h remaining)"
      run_finished=1
      exit 0
    fi
  fi

  # Best-effort: skip if offline
  if command -v ping >/dev/null 2>&1; then
    if ! ping -c 1 -W 1 1.1.1.1 >/dev/null 2>&1; then
      echo "RESULT: skipped - network appears down"
      run_finished=1
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
        if [ "$latest_epoch" -gt "$last_seen_epoch" ]; then
          echo "News: \"${latest_title:-<untitled>}\" ($latest_date) - not yet acknowledged"
          echo "RESULT: skipped - unacknowledged Arch news (acknowledge it in the Arch Update Status app)"
          echo "$latest_title ($latest_date)" > "$NEWS_FLAG"
          command -v notify-send >/dev/null 2>&1 && notify-send -u critical "Arch auto-update paused" "New Arch news: ${latest_title:-check archlinux.org/news}" || true
          run_finished=1
          exit 0
        fi
      fi
    fi
  fi
  rm -f "$NEWS_FLAG"

  echo "pacman: syncing + upgrading..."
  # Stream live (so the app's Live Update tab shows real-time output) while
  # still truncating the one line that's genuinely unreadable (the full
  # package-name dump) as it passes through.
  set +e
  pacman -Syu --noconfirm 2>&1 | tee "/tmp/arch-update-raw.$$" | \
    sed -E 's/^(Packages \([0-9]+\)) .*/\1 - full package names omitted from the log for readability (still shown live during the run; parsed list is in PACKAGES: below)/'
  pacman_status=${PIPESTATUS[0]}
  set -e

  raw_output="/tmp/arch-update-raw.$$"
  pkg_count=$(grep -oE '^Packages \([0-9]+\)' "$raw_output" | grep -oE '[0-9]+' | head -1)

  # Extract the actual package names (strip version/epoch suffixes) into a
  # machine-parseable block the app can read without re-deriving it from the
  # raw pacman text.
  pkg_line=$(grep -E '^Packages \([0-9]+\)' "$raw_output" | sed -E 's/^Packages \([0-9]+\) //')
  if [ -n "$pkg_line" ]; then
    echo "PACKAGES:"
    echo "$pkg_line" | tr -s ' ' '\n' | sed -E 's/-[0-9][^-]*(:[0-9][^-]*)?-[0-9]+$//' | sort -u | sed 's/^/  /'
  fi

  # Surface pacman's real error lines verbatim (not just the exit code) so a
  # failure is diagnosable from the parsed status alone.
  error_lines=$(grep -E '^error:' "$raw_output" || true)
  if [ -n "$error_lines" ]; then
    echo "ERRORS:"
    echo "$error_lines" | sed 's/^/  /'
  fi
  rm -f "$raw_output"

  if [ "$pacman_status" -eq 0 ]; then
    date +%s > "$STAMP"
    run_end_epoch=$(date +%s)
    echo "RESULT: success - ${pkg_count:-0} packages upgraded in $((run_end_epoch - run_start_epoch))s"
  else
    echo "RESULT: FAILED - pacman exited with status $pacman_status (see ERRORS above)"
  fi
  echo "────────────────────────────────────────────────────────────"
  run_finished=1
} >> "$LOG" 2>&1
