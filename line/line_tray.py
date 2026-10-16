from __future__ import annotations

import argparse
import contextlib
import ctypes
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_support import production_update
from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.win32 import set_app_user_model_id
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
from shared_ui.foreground import bring_to_front
from shared_ui.preview import Preview, preview_of, taskbar_identity, window_title
from shared_ui.preview_icon import app_icon

from line import line_page

PROGRAM = Path(__file__).resolve()
CHECKOUT = PROGRAM.parent.parent
ICON = CHECKOUT / "icon.ico"
LINE_FILES = (PROGRAM, Path(line_page.__file__).resolve(), ICON)
WINDOW_SIZE = (1240, 860)
ASFW_ANY = -1
WEB_VIEW_ARGUMENTS = ["--webEngineArgs", "--disable-gpu"]
TASKBAR_IDENTITY = "Local.Line"
SERVER = "line_tray"
PREVIEW_SERVER = "line_tray_preview"
HAND_BACK_AFTER_MINUTES = 60
UPDATE_CHECK_MS = 60_000
_log = logging.getLogger("line")


@dataclass(frozen=True)
class LineCopy:
    title: str
    server_name: str
    app_user_model_id: str
    state: Path
    preview: Preview | None

    @property
    def page(self) -> Path:
        return self.state / "line.html"

    @property
    def storage(self) -> Path:
        return self.state / "line_storage"

    @property
    def log(self) -> Path:
        return self.state / "line.log"

    @property
    def copies_it_replaces(self) -> tuple[str, ...]:
        return (PREVIEW_SERVER,) if self.preview is None else (SERVER, PREVIEW_SERVER)

    def icon(self) -> QIcon:
        return app_icon(ICON, self.preview)


def this_copy(preview: Preview | None) -> LineCopy:
    state = CHECKOUT / "state"
    if preview is None:
        return LineCopy(line_page.APP_NAME, SERVER, TASKBAR_IDENTITY, state, None)
    return LineCopy(window_title(line_page.APP_NAME, preview), PREVIEW_SERVER,
                    taskbar_identity(TASKBAR_IDENTITY, preview), state, preview)


def title_until(copy: LineCopy, now: datetime) -> str:
    if copy.preview is None:
        return copy.title
    return f"{copy.title}, until {now + timedelta(minutes=HAND_BACK_AFTER_MINUTES):%H:%M}"


def the_line_he_uses() -> Path:
    found = subprocess.run(["git", "-C", str(CHECKOUT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    return Path(found.stdout.strip()).parent


def hand_back(start: Callable[..., object] = subprocess.Popen) -> None:
    start([sys.executable, "-m", "line"], cwd=the_line_he_uses(), creationflags=subprocess.CREATE_NO_WINDOW)


def application(argv: list[str], copy: LineCopy) -> QApplication:
    with contextlib.suppress(OSError):
        set_app_user_model_id(copy.app_user_model_id)
    app = QApplication([*argv[:1], *WEB_VIEW_ARGUMENTS])
    app.setQuitOnLastWindowClosed(False)
    app.commitDataRequest.connect(lambda: _log.info("Windows announced that his session is ending"))
    return app


def web_profile(storage: Path) -> QWebEngineProfile:
    profile = QWebEngineProfile(line_page.APP_NAME)
    profile.setPersistentStoragePath(str(storage))
    profile.setCachePath(str(storage / "cache"))
    return profile


def open_window(page: Path, profile: QWebEngineProfile, copy: LineCopy) -> QWebEngineView:
    view = QWebEngineView(profile)
    view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    view.setWindowTitle(copy.title)
    view.setWindowIcon(copy.icon())
    view.titleChanged.connect(lambda _: view.setWindowTitle(copy.title))
    view.iconChanged.connect(lambda _: view.setWindowIcon(copy.icon()))
    view.resize(*WINDOW_SIZE)
    view.page().setBackgroundColor(QColor(line_page.DARK_BACKGROUND))
    view.load(QUrl.fromLocalFile(str(page)))
    return view


class LineWindow:
    def __init__(self, page: Path, *, open_window: Callable[[Path], QWidget], write_now: Callable[[], object],
                 write_in_background: Callable[[], object], bring_to_front: Callable[[QWidget], object]):
        self.page = page
        self._open_window = open_window
        self._write_now = write_now
        self._write_in_background = write_in_background
        self._bring_to_front = bring_to_front
        self._window: QWidget | None = None

    @property
    def is_open(self) -> bool:
        return self._window is not None

    @property
    def is_showing(self) -> bool:
        return self._window is not None and self._window.isVisible()

    def show(self) -> None:
        if not self.is_open:
            self._write_now()
            self._window = self._open_window(self.page)
            self._window.destroyed.connect(self._forget_the_closed_window)
        self._window.setWindowState((self._window.windowState() & ~Qt.WindowState.WindowMinimized)
                                    | Qt.WindowState.WindowActive)
        self._window.show()
        self._bring_to_front(self._window)

    def refresh(self) -> None:
        if self.is_open:
            self._write_in_background()

    def close(self) -> None:
        if self.is_open:
            self._window.close()

    def _forget_the_closed_window(self) -> None:
        self._window = None


class RunningCopy:
    def __init__(self, files: tuple[Path, ...], *, show_the_window: Callable[[], object],
                 stop_listening: Callable[[], object], start_a_fresh_copy: Callable[[], object],
                 quit_this_copy: Callable[[], object]):
        self._files = files
        self._show_the_window = show_the_window
        self._stop_listening = stop_listening
        self._start_a_fresh_copy = start_a_fresh_copy
        self._quit_this_copy = quit_this_copy
        self._started_with = self._line_files_now()

    def open(self) -> None:
        if self._line_files_now() == self._started_with:
            self._show_the_window()
            return
        self._stop_listening()
        self._start_a_fresh_copy()
        self._quit_this_copy()

    def _line_files_now(self) -> tuple[bytes | None, ...]:
        return tuple(_contents(path) for path in self._files)


def _contents(path: Path) -> bytes | None:
    with contextlib.suppress(OSError):
        return path.read_bytes()
    return None


def start_a_fresh_copy(start: Callable[..., object] = subprocess.Popen) -> None:
    start([sys.executable, "-m", "line", "--open"], cwd=CHECKOUT, creationflags=subprocess.CREATE_NO_WINDOW)


def watch_for_updates(copy: LineCopy, window: LineWindow, *,
                      quit_this_copy: Callable[[], object]) -> production_update.UpdateWatch | None:
    if copy.preview is not None:
        return None

    def start_again() -> None:
        production_update.start_again_after(os.getpid(), CHECKOUT / "launch_line_in_the_tray.vbs")
        quit_this_copy()

    return production_update.UpdateWatch(CHECKOUT, a_good_moment=lambda: not window.is_showing,
                                         start_again=start_again, logger=_log)


class Quitting:
    def __init__(self, *, preview: bool, quit_line: Callable[[], object], hand_back: Callable[[], object]):
        self._preview = preview
        self._quit_line = quit_line
        self._hand_back = hand_back

    def from_the_tray_menu(self) -> None:
        _log.info("asked to quit by the tray menu")
        if self._preview:
            self.and_hand_back()
        else:
            self._quit_line()

    def for_another_copy(self) -> None:
        _log.info("another copy of Line took over")
        self._quit_line()

    def and_hand_back(self) -> None:
        _log.info("handing back to the Line he uses")
        self._hand_back()
        self._quit_line()


class _PageWriter:
    def __init__(self, page: Path):
        self.page = page
        self._running: subprocess.Popen | None = None

    def now(self) -> None:
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(line_page.refresh_command(self.page, 1, line_page.WATCH_EVERY_S),
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=line_page.WATCH_EVERY_S * 4,
                           check=False)

    def in_background(self) -> None:
        if self._running is not None and self._running.poll() is None:
            return
        self._running = subprocess.Popen(line_page.refresh_command(self.page, 1, line_page.WATCH_EVERY_S),
                                         creationflags=subprocess.CREATE_NO_WINDOW)


def serve_requests(name: str, *, open_line: Callable[[], object], quit_line: Callable[[], object]) -> QLocalServer:
    server = QLocalServer()
    QLocalServer.removeServer(name)
    server.listen(name)

    def answer(connection: QLocalSocket) -> None:
        request = bytes(connection.readAll())
        if request == b"open":
            open_line()
        elif request == b"quit":
            quit_line()

    def asked() -> None:
        connection = server.nextPendingConnection()
        connection.readyRead.connect(lambda: answer(connection))
        connection.disconnected.connect(connection.deleteLater)

    server.newConnection.connect(asked)
    return server


def ask(name: str, request: bytes) -> bool:
    asking = QLocalSocket()
    asking.connectToServer(name)
    if not asking.waitForConnected(500):
        return False
    asking.write(request)
    asking.waitForBytesWritten(2000)
    asking.disconnectFromServer()
    return True


def wait_until_gone(name: str, seconds: float = 15) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        probe = QLocalSocket()
        probe.connectToServer(name)
        if not probe.waitForConnected(200):
            return True
        probe.abort()
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="line_tray")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    copy = this_copy(preview_of(CHECKOUT))
    app = application(sys.argv, copy)
    if copy.preview is None:
        if args.open:
            ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
        if ask(copy.server_name, b"open" if args.open else b""):
            return 0
    for name in copy.copies_it_replaces:
        if ask(name, b"quit"):
            wait_until_gone(name)
    return run_the_tray(app, copy, open_now=args.open)


def run_the_tray(app: QApplication, copy: LineCopy, *, open_now: bool) -> int:
    copy.state.mkdir(parents=True, exist_ok=True)
    install_exception_logging(configure_logging("line", copy.log))
    writer = _PageWriter(copy.page)
    profile = web_profile(copy.storage)
    shown_as = replace(copy, title=title_until(copy, datetime.now(UTC).astimezone()))
    window = LineWindow(copy.page, open_window=lambda page: open_window(page, profile, shown_as),
                        write_now=writer.now, write_in_background=writer.in_background,
                        bring_to_front=bring_to_front)
    app.aboutToQuit.connect(window.close)

    def show_the_window() -> None:
        window.show()
        _log.info("window shown: %s", "showing" if window.is_showing else "NOT showing")

    def restart() -> None:
        _log.info("Line's files changed on disk; starting a fresh copy")
        start_a_fresh_copy()

    server: QLocalServer | None = None

    def stop_listening() -> None:
        server.close()

    running = RunningCopy(LINE_FILES, show_the_window=show_the_window, stop_listening=stop_listening,
                          start_a_fresh_copy=restart, quit_this_copy=app.quit)

    def open_asked(how: str) -> None:
        _log.info("asked to open by %s", how)
        running.open()

    quitting = Quitting(preview=copy.preview is not None, quit_line=app.quit, hand_back=hand_back)
    server = serve_requests(copy.server_name, open_line=lambda: open_asked("a launch"),
                            quit_line=quitting.for_another_copy)
    tray = QSystemTrayIcon(copy.icon())
    tray.setToolTip(copy.title)
    menu = QMenu()
    menu.addAction("Open").triggered.connect(lambda: open_asked("the tray menu"))
    menu.addSeparator()
    menu.addAction("Quit").triggered.connect(quitting.from_the_tray_menu)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: open_asked("a click on the tray icon")
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    handing_back = QTimer()
    if copy.preview is not None:
        handing_back.setSingleShot(True)
        handing_back.timeout.connect(quitting.and_hand_back)
        handing_back.start(HAND_BACK_AFTER_MINUTES * 60_000)
    timer = QTimer()
    timer.timeout.connect(window.refresh)
    timer.start(line_page.WATCH_EVERY_S * 1000)
    looking_for_an_update = QTimer()
    if (updates := watch_for_updates(copy, window, quit_this_copy=app.quit)) is not None:
        looking_for_an_update.timeout.connect(updates.tick)
        looking_for_an_update.start(UPDATE_CHECK_MS)
    _log.info("started from %s", CHECKOUT)
    if open_now:
        show_the_window()
    return run_until_it_ends(app.exec)


def run_until_it_ends(event_loop: Callable[[], int]) -> int:
    code = event_loop()
    _log.info("exiting with code %d", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
