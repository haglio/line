from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from app_support import haglio_settings, siblings
from app_support.subprocess_utils import hidden_subprocess_kwargs

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


def answer_a_press_of_work_on_this(request, work_asked: Callable[[str, str], object]) -> None:
    asked = ship_time_work.asked_for(request.url().toString())
    if asked is not None:
        request.reject()
        work_asked(*asked)


def folder_the_checkouts_sit_in() -> Path:
    return siblings.project_dir("fun_time", haglio_settings.project_roots()).parent


def open_the_work(state: Path, number: str, week: str, open_link: Callable[[str], object]) -> None:
    _log.info("asked to work on %s for %s", number, week)
    if (link := ship_time_work.new_session_link(state, number, week, folder_the_checkouts_sit_in())) is not None:
        open_link(link)


def measuring_ship_time(state: Path, start: Callable[..., subprocess.Popen] = subprocess.Popen) -> OneAtATime:
    return OneAtATime([sys.executable, "-m", "line.ship_time", "--out", str(state / ship_time.RECORD_NAME)], start=start)
