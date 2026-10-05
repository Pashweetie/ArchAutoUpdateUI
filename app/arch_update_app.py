#!/usr/bin/env python3
"""
Arch Update Status - a small PyQt6 app for the throttled, news-aware
Arch auto-update setup.

No args:    opens to the status view (last update, next due, pending-news flag).
--auto:     immediately runs the update (via pkexec) and streams live output.

Tabs: Status | Live Update | Arch News
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

STAMP = "/var/lib/arch-update.stamp"
NEWS_STAMP = "/var/lib/arch-update-news.stamp"
NEWS_FLAG = "/var/lib/arch-update-news-pending"
LOG_PATH = "/var/log/arch-update.log"
UPDATE_SCRIPT = "/usr/local/bin/arch-update.sh"
NEWS_FEED = "https://archlinux.org/feeds/news/"
MIN_INTERVAL_SECS = 7 * 24 * 60 * 60
PACMAN_PID_FILE = "/run/arch-update-pacman.pid"


def read_epoch(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def fmt_ago(epoch):
    if epoch == 0:
        return "never"
    secs = int(time.time()) - epoch
    days, rem = divmod(secs, 86400)
    hours = rem // 3600
    when = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
    if days > 0:
        return f"{when} ({days}d {hours}h ago)"
    return f"{when} ({hours}h ago)"


def tail_log(n=15):
    try:
        with open(LOG_PATH) as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        return lines[-n:]
    except FileNotFoundError:
        return []


def parse_last_run():
    """
    Parse the structured RESULT line from the most recent run block in the
    log, so the Status tab can show a real status instead of raw pacman
    output. Returns a dict: {when, outcome, detail} or None if no run has
    ever completed (e.g. log doesn't exist, or the only runs so far never
    reached a RESULT line - a currently-in-progress or crashed run).

    outcome is one of: success, failed, interrupted, skipped-throttle,
    skipped-news, skipped-lock, skipped-offline, unknown.
    """
    lines = tail_log(n=1500)
    if not lines:
        return None

    # Walk backwards to find the most recent run's header + RESULT line.
    last_result_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("RESULT:"):
            last_result_idx = i
            break
    if last_result_idx is None:
        return None

    result_line = lines[last_result_idx]
    run_start_idx = None
    when = None
    for i in range(last_result_idx, -1, -1):
        if lines[i].startswith("  Run started:"):
            when = lines[i].split("Run started:", 1)[1].strip()
            run_start_idx = i
            break

    detail = result_line.split("RESULT:", 1)[1].strip()
    if detail.startswith("success"):
        outcome = "success"
    elif detail.startswith("FAILED"):
        outcome = "failed"
    elif detail.startswith("ABORTED"):
        outcome = "aborted"
    elif detail.startswith("INTERRUPTED"):
        outcome = "interrupted"
    elif "throttle" in detail:
        outcome = "skipped-throttle"
    elif "news" in detail:
        outcome = "skipped-news"
    elif "already running" in detail:
        outcome = "skipped-lock"
    elif "offline" in detail or "network" in detail:
        outcome = "skipped-offline"
    else:
        outcome = "unknown"

    # Collect the PACKAGES: and ERRORS: blocks between the run header and the
    # RESULT line, if present - these are indented "  name" lines following
    # their own header line.
    packages, errors = [], []
    current_block = None
    scan_from = run_start_idx if run_start_idx is not None else 0
    for line in lines[scan_from:last_result_idx]:
        if line == "PACKAGES:":
            current_block = packages
            continue
        if line == "ERRORS:":
            current_block = errors
            continue
        if line.startswith("  ") and current_block is not None:
            current_block.append(line.strip())
        else:
            current_block = None

    return {
        "when": when,
        "outcome": outcome,
        "detail": detail,
        "packages": packages,
        "errors": errors,
    }


class NewsFetcher(QObject):
    finished = pyqtSignal(list, str)  # entries, error

    def run(self):
        try:
            req = urllib.request.Request(NEWS_FEED, headers={"User-Agent": "arch-update-status/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            entries = []
            for item in root.iter("item"):
                title = item.findtext("title", default="(untitled)")
                link = item.findtext("link", default="")
                pub = item.findtext("pubDate", default="")
                entries.append({"title": title, "link": link, "pubDate": pub})
            self.finished.emit(entries[:15], "")
        except Exception as e:  # noqa: BLE001 - surface any fetch error to the UI
            self.finished.emit([], str(e))


class UpdateRunner(QThread):
    line_ready = pyqtSignal(str)
    finished_run = pyqtSignal(int)

    def run(self):
        try:
            proc = subprocess.Popen(
                ["pkexec", UPDATE_SCRIPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            self.line_ready.emit(f"Failed to launch: {e}")
            self.finished_run.emit(-1)
            return

        for line in proc.stdout:
            self.line_ready.emit(line.rstrip("\n"))
        proc.wait()
        self.finished_run.emit(proc.returncode)

    def abort(self):
        """
        Send SIGINT to the actual pacman process (read from the PID file
        arch-update.sh writes), not to pkexec or this thread. Uses pkexec
        since the PID file and the pacman process are root-owned.

        Safe to call at any point while a run is active - verified against
        pacman's actual source (sighandler.c, libalpm trans.c/add.c), not
        inferred from log text:
          - pacman installs a dedicated SIGINT handler (soft_interrupt_handler)
            rather than dying on the default signal action.
          - Before a transaction has reached STATE_COMMITING (i.e. during
            dependency resolution and downloads), alpm_trans_interrupt()
            fails and pacman exits immediately and cleanly (observed exit
            code 128+SIGINT = 130, confirmed against this exact behavior).
          - Downloads themselves are written to randomly-named .part tempfiles
            and only atomically rename()'d into place on full success
            (dload.c) - an interrupted download never leaves a corrupt real
            package file behind.
          - Once committing has begun, _alpm_upgrade_packages only checks
            the interrupted flag BETWEEN whole packages (add.c) - a given
            package's extraction+scriptlets+db-write always runs to full
            completion once started; SIGINT just stops the loop before the
            NEXT package begins. There is no code path where a single
            package's install is partially applied by an abort.
        The practical effect of aborting mid-commit is a short delay (current
        package finishes, usually a few seconds) before pacman actually exits -
        not a corruption risk.
        """
        try:
            with open(PACMAN_PID_FILE) as f:
                pid = f.read().strip()
        except (FileNotFoundError, ValueError):
            return False, "No active pacman process found (nothing to abort)."

        try:
            result = subprocess.run(
                ["pkexec", "kill", "-SIGINT", pid],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to send abort signal: {e}"

        if result.returncode != 0:
            return False, result.stderr.strip() or "Abort signal failed (process may have already exited)."
        return True, None


class StatusTab(QWidget):
    def __init__(self, run_update_cb):
        super().__init__()
        layout = QVBoxLayout(self)

        self.last_update_label = QLabel()
        self.next_due_label = QLabel()
        self.news_flag_label = QLabel()
        self.last_run_label = QLabel()
        self.news_flag_label.setWordWrap(True)
        self.last_run_label.setWordWrap(True)

        for lbl in (self.last_update_label, self.next_due_label, self.news_flag_label, self.last_run_label):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(lbl)

        # Always-visible details for the last run: packages updated on
        # success, actual error text on failure. This is parsed/structured
        # output, not the raw log - it stays visible even when the raw log
        # below is collapsed.
        self.last_run_details = QTextEdit()
        self.last_run_details.setReadOnly(True)
        self.last_run_details.setMaximumHeight(120)
        self.last_run_details.setVisible(False)
        layout.addWidget(self.last_run_details)

        self.run_button = QPushButton("Run update now")
        self.run_button.clicked.connect(run_update_cb)
        layout.addWidget(self.run_button)

        # Raw log is debugging-only: collapsed by default, opt-in via toggle.
        self.log_toggle = QPushButton("Show raw log ▾")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        layout.addWidget(self.log_toggle)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFontFamily("monospace")
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view, stretch=1)

        layout.addStretch()
        self.refresh()

    def _toggle_log(self, checked):
        self.log_view.setVisible(checked)
        self.log_toggle.setText("Hide raw log ▴" if checked else "Show raw log ▾")
        if checked:
            self.log_view.setPlainText("\n".join(tail_log(n=1500)))

    def refresh(self):
        last_epoch = read_epoch(STAMP)
        self.last_update_label.setText(f"<b>Last successful update:</b> {fmt_ago(last_epoch)}")

        if last_epoch == 0:
            next_due = "as soon as it first runs"
        else:
            remaining = MIN_INTERVAL_SECS - (int(time.time()) - last_epoch)
            next_due = "now (overdue)" if remaining <= 0 else f"in {remaining // 3600}h"
        self.next_due_label.setText(f"<b>Next update due:</b> {next_due}")

        if os.path.exists(NEWS_FLAG):
            try:
                with open(NEWS_FLAG) as f:
                    pending = f.read().strip()
            except OSError:
                pending = "(unreadable)"
            self.news_flag_label.setText(
                f"<span style='color:#e05d44'><b>⚠ Auto-update paused — unread Arch news:</b> {pending}. "
                "Go to the <b>Arch News</b> tab to review and acknowledge it.</span>"
            )
        else:
            self.news_flag_label.setText("<span style='color:#5aa469'>✓ No pending news block.</span>")

        run = parse_last_run()
        if run is None:
            self.last_run_label.setText("<b>Last run:</b> no completed run found yet.")
            self.last_run_details.setVisible(False)
        else:
            colors = {
                "success": "#5aa469",
                "failed": "#e05d44",
                "aborted": "#e0a544",
                "interrupted": "#e0a544",
                "skipped-throttle": "#888888",
                "skipped-news": "#e0a544",
                "skipped-lock": "#888888",
                "skipped-offline": "#888888",
                "unknown": "#888888",
            }
            labels = {
                "success": "✓ Succeeded",
                "failed": "✗ Failed",
                "aborted": "⏹ Aborted by you (packages already written when aborted were completed cleanly; nothing partial)",
                "interrupted": "⚠ Interrupted (didn't finish cleanly)",
                "skipped-throttle": "– Skipped (too soon since last update)",
                "skipped-news": "⚠ Skipped (unacknowledged Arch news)",
                "skipped-lock": "– Skipped (another run was already in progress)",
                "skipped-offline": "– Skipped (network was down)",
                "unknown": "? Unrecognized result",
            }
            color = colors.get(run["outcome"], "#888888")
            label = labels.get(run["outcome"], run["detail"])
            when = run["when"] or "unknown time"
            self.last_run_label.setText(
                f"<b>Last run</b> ({when}): <span style='color:{color}'><b>{label}</b></span><br>"
                f"<span style='color:#888888'>{run['detail']}</span>"
            )

            if run["errors"]:
                self.last_run_details.setPlainText(
                    "Error details:\n" + "\n".join(run["errors"])
                )
                self.last_run_details.setVisible(True)
            elif run["packages"]:
                self.last_run_details.setPlainText(
                    f"Packages updated ({len(run['packages'])}):\n" + ", ".join(run["packages"])
                )
                self.last_run_details.setVisible(True)
            else:
                self.last_run_details.setVisible(False)

        if self.log_view.isVisible():
            self.log_view.setPlainText("\n".join(tail_log(n=1500)))


class LiveUpdateTab(QWidget):
    def __init__(self, run_cb, abort_cb):
        super().__init__()
        layout = QVBoxLayout(self)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFontFamily("monospace")
        layout.addWidget(self.output, stretch=1)
        self.status_label = QLabel("Idle.")
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.run_button = QPushButton("Run update now")
        self.run_button.clicked.connect(run_cb)
        button_row.addWidget(self.run_button)

        self.abort_button = QPushButton("Abort")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(abort_cb)
        button_row.addWidget(self.abort_button)
        layout.addLayout(button_row)

    def append_line(self, line):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(line + "\n")
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def set_status(self, text):
        self.status_label.setText(text)

    def set_abort_enabled(self, enabled):
        self.abort_button.setEnabled(enabled)

    def set_run_enabled(self, enabled):
        self.run_button.setEnabled(enabled)


class NewsTab(QWidget):
    def __init__(self, status_tab=None):
        super().__init__()
        self.status_tab = status_tab
        layout = QVBoxLayout(self)

        self.pending_banner = QLabel()
        self.pending_banner.setWordWrap(True)
        self.pending_banner.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.pending_banner)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        button_row = QHBoxLayout()
        self.ack_button = QPushButton("Acknowledge latest news & allow update")
        self.ack_button.clicked.connect(self.acknowledge_latest)
        button_row.addWidget(self.ack_button)
        layout.addLayout(button_row)

        self._latest_entry = None
        self.load()

    def load(self):
        self.list_widget.clear()
        self.list_widget.addItem("Loading Arch news feed...")
        self.refresh_banner()

        self.thread = QThread()
        self.fetcher = NewsFetcher()
        self.fetcher.moveToThread(self.thread)
        self.thread.started.connect(self.fetcher.run)
        self.fetcher.finished.connect(self.on_loaded)
        self.fetcher.finished.connect(self.thread.quit)
        self.thread.start()

    def refresh_banner(self):
        if os.path.exists(NEWS_FLAG):
            try:
                with open(NEWS_FLAG) as f:
                    pending = f.read().strip()
            except OSError:
                pending = "(unreadable)"
            self.pending_banner.setText(
                f"<span style='color:#e05d44'><b>⚠ Auto-update is paused on:</b> {pending}. "
                "Read it below (double-click to open), then click Acknowledge to let the update proceed.</span>"
            )
            self.ack_button.setEnabled(True)
        else:
            self.pending_banner.setText("<span style='color:#5aa469'>✓ No update is currently blocked on news.</span>")

    def on_loaded(self, entries, error):
        self.list_widget.clear()
        if error:
            self.error_label.setText(f"Failed to fetch news feed: {error}")
            return
        self.error_label.setText("")
        self._latest_entry = entries[0] if entries else None
        last_seen = read_epoch(NEWS_STAMP)
        for entry in entries:
            marker = ""
            try:
                from email.utils import parsedate_to_datetime

                pub_dt = parsedate_to_datetime(entry["pubDate"])
                if pub_dt and pub_dt.timestamp() > last_seen:
                    marker = "🆕 "
            except Exception:  # noqa: BLE001
                pass
            item = QListWidgetItem(f"{marker}{entry['title']}  —  {entry['pubDate']}")
            item.setData(Qt.ItemDataRole.UserRole, entry["link"])
            self.list_widget.addItem(item)
        self.list_widget.itemDoubleClicked.connect(self._open_link)

    def _open_link(self, item):
        link = item.data(Qt.ItemDataRole.UserRole)
        if link:
            import webbrowser

            webbrowser.open(link)

    def acknowledge_latest(self):
        if not self._latest_entry:
            return
        try:
            from email.utils import parsedate_to_datetime

            pub_dt = parsedate_to_datetime(self._latest_entry["pubDate"])
            epoch = int(pub_dt.timestamp()) if pub_dt else int(time.time())
        except Exception:  # noqa: BLE001
            epoch = int(time.time())

        # NEWS_STAMP and NEWS_FLAG are root-owned (written by arch-update.sh as
        # root too) - acknowledging from the GUI needs the same privilege.
        script = f"echo {epoch} > {NEWS_STAMP} && rm -f {NEWS_FLAG}"
        try:
            result = subprocess.run(
                ["pkexec", "bash", "-c", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as e:  # noqa: BLE001
            self.error_label.setText(f"Failed to acknowledge: {e}")
            return

        if result.returncode != 0:
            self.error_label.setText(f"Acknowledge failed: {result.stderr.strip() or 'unknown error'}")
            return

        self.error_label.setText("")
        self.refresh_banner()
        if self.status_tab:
            self.status_tab.refresh()


class MainWindow(QMainWindow):
    def __init__(self, auto=False):
        super().__init__()
        self.setWindowTitle("Arch Update Status")
        self.resize(560, 480)
        self.setMinimumSize(420, 320)

        self.tabs = QTabWidget()
        self.status_tab = StatusTab(self.run_update)
        self.live_tab = LiveUpdateTab(self.run_update, self.abort_update)
        self.news_tab = NewsTab(status_tab=self.status_tab)
        self.tabs.addTab(self.status_tab, "Status")
        self.tabs.addTab(self.live_tab, "Live Update")
        self.tabs.addTab(self.news_tab, "Arch News")
        self.setCentralWidget(self.tabs)

        self.runner = None

        if auto:
            last_epoch = read_epoch(STAMP)
            overdue = last_epoch == 0 or (int(time.time()) - last_epoch) >= MIN_INTERVAL_SECS
            if overdue and os.path.exists(NEWS_FLAG):
                self.tabs.setCurrentWidget(self.news_tab)
            elif overdue:
                QTimer.singleShot(200, self.run_update)

    def run_update(self):
        if self.runner and self.runner.isRunning():
            return
        self.tabs.setCurrentWidget(self.live_tab)
        self.live_tab.output.clear()
        self.live_tab.set_status("Running (you may see a polkit password prompt)...")
        self.status_tab.run_button.setEnabled(False)
        self.live_tab.set_run_enabled(False)
        self.live_tab.set_abort_enabled(True)

        self.runner = UpdateRunner()
        self.runner.line_ready.connect(self.live_tab.append_line)
        self.runner.finished_run.connect(self.on_update_finished)
        self.runner.start()

    def abort_update(self):
        if not self.runner or not self.runner.isRunning():
            return
        self.live_tab.set_status(
            "Sending abort signal - pacman will finish the package it's currently "
            "writing (if any), then stop before starting the next one..."
        )
        ok, error = self.runner.abort()
        if ok:
            self.live_tab.set_status("Abort signal sent - waiting for pacman to stop...")
        else:
            self.live_tab.set_status(f"Abort failed: {error}")

    def on_update_finished(self, code):
        self.live_tab.set_status(f"Finished (exit code {code}).")
        self.live_tab.set_abort_enabled(False)
        self.live_tab.set_run_enabled(True)
        self.status_tab.run_button.setEnabled(True)
        self.status_tab.refresh()


def main():
    auto = "--auto" in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName("Arch Update Status")
    window = MainWindow(auto=auto)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
