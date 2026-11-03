from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from app_support import claude_sessions
from app_support.subprocess_utils import hidden_subprocess_kwargs
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWidgets import QSystemTrayIcon, QWidget

from line import ship_time, ship_time_work
from line.ship_time_work import FinishedSession

CHECKOUT = Path(__file__).resolve().parent.parent
MEASURED_EVERY_MS = 60 * 60_000
LOOKED_FOR_FINISHED_SESSIONS_EVERY_MS = 60_000

_log = logging.getLogger("line")


class OneAtATime:
    def __init__(self, command: list[str], *, start: Callable[..., subprocess.Popen] = subprocess.Popen):
        self._command = command
        self._start = start
        self._has_ended: Callable[[], bool] = lambda: True

    def in_background(self) -> None:
        if self._has_ended():
            job = self._start(self._command, cwd=CHECKOUT, **hidden_subprocess_kwargs())
            self._has_ended = lambda: job.poll() is not None


class PageAskingForWork(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent: QWidget, work_asked: Callable[[str, str], object]):
        super().__init__(profile, parent)
        self._work_asked = work_asked

    def acceptNavigationRequest(self, url, _kind, _is_main_frame) -> bool:
        asked = ship_time_work.asked_for(url.toString())
        if asked is None:
            return True
        self._work_asked(*asked)
        return False


def start_the_work(state: Path, number: str, week: str, start: Callable[..., object] = subprocess.Popen) -> None:
    _log.info("asked to work on %s for %s", number, week)
    start([sys.executable, "-m", "line.ship_time_work", "--state", str(state), "--number", number, "--week", week],
          cwd=CHECKOUT, **hidden_subprocess_kwargs())


def measuring_ship_time(state: Path, start: Callable[..., subprocess.Popen] = subprocess.Popen) -> OneAtATime:
    return OneAtATime([sys.executable, "-m", "line.ship_time", "--out", str(state / ship_time.RECORD_NAME)], start=start)


class FinishedSessionMessages:
    def __init__(self, tray: QSystemTrayIcon, *, open_in_claude_desktop: Callable[..., object],
                 claude: Callable[[], str]):
        self._show_message = tray.showMessage
        self._open = open_in_claude_desktop
        self._claude = claude
        self._open_the_last_one_told_about: Callable[[], object] = lambda: None

    def tell(self, session: FinishedSession) -> None:
        self._open_the_last_one_told_about = lambda: self._open(self._claude(), session.short_id, session.session_id,
                                                                session.cwd)
        self._show_message(session.title, session.body, QSystemTrayIcon.MessageIcon.Information, 0)

    def clicked(self) -> None:
        open_it, self._open_the_last_one_told_about = self._open_the_last_one_told_about, lambda: None
        open_it()


def looking_after_sessions(state: Path, tray: QSystemTrayIcon) -> ship_time_work.WatchForFinishedSessions:
    messages = FinishedSessionMessages(tray, open_in_claude_desktop=claude_sessions.open_in_claude_desktop,
                                       claude=claude_sessions.claude_command)
    tray.messageClicked.connect(messages.clicked)
    claude = claude_sessions.claude_command()
    return ship_time_work.WatchForFinishedSessions(state / ship_time_work.SESSIONS_NAME,
                                                   background=lambda: claude_sessions.background_sessions(claude),
                                                   tell=messages.tell)
