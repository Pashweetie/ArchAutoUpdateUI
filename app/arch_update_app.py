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


class StatusTab(QWidget):
    def __init__(self, run_update_cb):
        super().__init__()
        layout = QVBoxLayout(self)

        self.last_update_label = QLabel()
        self.next_due_label = QLabel()
        self.news_flag_label = QLabel()
        self.news_flag_label.setWordWrap(True)

        for lbl in (self.last_update_label, self.next_due_label, self.news_flag_label):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(lbl)

        layout.addWidget(QLabel("<b>Recent log:</b>"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)

        self.run_button = QPushButton("Run update now")
        self.run_button.clicked.connect(run_update_cb)
        layout.addWidget(self.run_button)

        self.refresh()

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

        self.log_view.setPlainText("\n".join(tail_log()))


class LiveUpdateTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFontFamily("monospace")
        layout.addWidget(self.output, stretch=1)
        self.status_label = QLabel("Idle.")
        layout.addWidget(self.status_label)

    def append_line(self, line):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(line + "\n")
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def set_status(self, text):
        self.status_label.setText(text)


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
        self.live_tab = LiveUpdateTab()
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

        self.runner = UpdateRunner()
        self.runner.line_ready.connect(self.live_tab.append_line)
        self.runner.finished_run.connect(self.on_update_finished)
        self.runner.start()

    def on_update_finished(self, code):
        self.live_tab.set_status(f"Finished (exit code {code}).")
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
