from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from app_support import haglio_settings, siblings
from app_support.subprocess_utils import hidden_subprocess_kwargs
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWidgets import QWidget

from line import ship_time, ship_time_work

CHECKOUT = Path(__file__).resolve().parent.parent
MEASURED_EVERY_MS = 60 * 60_000

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


def folder_the_checkouts_sit_in() -> Path:
    return siblings.project_dir("fun_time", haglio_settings.project_roots()).parent


def open_the_work(state: Path, number: str, week: str, open_link: Callable[[str], object]) -> None:
    _log.info("asked to work on %s for %s", number, week)
    if (link := ship_time_work.new_session_link(state, number, week, folder_the_checkouts_sit_in())) is not None:
        open_link(link)


def measuring_ship_time(state: Path, start: Callable[..., subprocess.Popen] = subprocess.Popen) -> OneAtATime:
    return OneAtATime([sys.executable, "-m", "line.ship_time", "--out", str(state / ship_time.RECORD_NAME)], start=start)
