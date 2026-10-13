import ctypes
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import PyQt6
import pytest
from PyQt6.QtCore import QEvent, QLibraryInfo, Qt, QUrl
from PyQt6.QtGui import QIcon
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication
from shared_ui.foreground import bring_to_front
from shared_ui.palette import MAGENTA, PREVIEW_INK
from shared_ui.preview import Preview

from line import line_page, line_tray


@pytest.fixture(scope="module")
def offscreen_app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or line_tray.application(["test_line_tray"], line_tray.this_copy(None))
    assert app.platformName() == "offscreen"
    return app


def _pixels(icon: QIcon):
    return icon.pixmap(32, 32).toImage()


def _closed(view) -> None:
    view.close()
    _let_closed_windows_go()


def _let_closed_windows_go() -> None:
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self):
        for slot in self._slots:
            slot()


class _View:
    def __init__(self):
        self.state = Qt.WindowState.WindowNoState
        self.calls: list[str] = []
        self.destroyed = _Signal()

    def windowState(self):
        return self.state

    def setWindowState(self, state):
        self.state = state

    def show(self):
        self.calls.append("show")

    def close(self):
        self.calls.append("close")
        self.destroyed.emit()


def _window(tmp_path: Path, events: list[str]) -> tuple[line_tray.LineWindow, list[_View]]:
    opened: list[_View] = []

    def open_window(page: Path) -> _View:
        events.append(f"opened {page.name}")
        opened.append(_View())
        return opened[-1]

    window = line_tray.LineWindow(tmp_path / "line.html", open_window=open_window,
                                  write_now=lambda: events.append("written now"),
                                  write_in_background=lambda: events.append("written in background"),
                                  bring_to_front=lambda view: view.calls.append("brought to front"))
    return window, opened


def test_the_window_opens_in_front_on_a_freshly_written_page(tmp_path):
    events = []
    window, opened = _window(tmp_path, events)

    window.show()

    assert events == ["written now", "opened line.html"]
    assert opened[0].calls == ["show", "brought to front"]


def test_showing_it_again_brings_the_open_window_forward_instead_of_opening_another(tmp_path):
    events = []
    window, opened = _window(tmp_path, events)
    window.show()

    window.show()

    assert len(opened) == 1
    assert opened[0].calls == ["show", "brought to front"] * 2


def test_a_minimized_window_comes_back_as_it_was_maximized_included(tmp_path):
    events = []
    window, opened = _window(tmp_path, events)
    window.show()
    opened[0].state = Qt.WindowState.WindowMinimized | Qt.WindowState.WindowMaximized

    window.show()

    assert opened[0].state & Qt.WindowState.WindowMaximized
    assert not opened[0].state & Qt.WindowState.WindowMinimized


def test_a_window_he_closed_opens_again(tmp_path):
    events = []
    window, opened = _window(tmp_path, events)
    window.show()
    opened[0].close()

    window.show()

    assert events == ["written now", "opened line.html", "written now", "opened line.html"]


def test_quitting_closes_the_open_window_and_does_nothing_with_none_open(tmp_path):
    events = []
    window, opened = _window(tmp_path, events)
    window.close()
    window.show()

    window.close()
    window.close()

    assert [view.calls.count("close") for view in opened] == [1]


def test_the_page_is_kept_fresh_only_while_its_window_is_open(tmp_path):
    events = []
    window, opened = _window(tmp_path, events)

    window.refresh()
    window.show()
    window.refresh()
    opened[0].close()
    window.refresh()

    assert events == ["written now", "opened line.html", "written in background"]


LINES_OWN_FILES = ("line_tray.py", "line_page.py", "icon.ico")


def _line_as_it_started(folder: Path) -> None:
    for name in LINES_OWN_FILES:
        (folder / name).write_bytes(b"as it started")


def _running_copy(folder: Path, events: list[str]) -> line_tray.RunningCopy:
    return line_tray.RunningCopy(tuple(folder / name for name in LINES_OWN_FILES), show_the_window=lambda: events.append("window shown"),
                                 stop_listening=lambda: events.append("stopped listening"),
                                 start_a_fresh_copy=lambda: events.append("fresh copy started"),
                                 quit_this_copy=lambda: events.append("quit"))


def test_asked_to_open_with_its_files_unchanged_it_shows_its_window(tmp_path):
    _line_as_it_started(tmp_path)
    events = []
    running = _running_copy(tmp_path, events)

    running.open()

    assert events == ["window shown"]


@pytest.mark.parametrize("changed", LINES_OWN_FILES)
def test_asked_to_open_after_its_files_changed_it_stops_listening_starts_a_fresh_copy_and_quits(tmp_path, changed):
    _line_as_it_started(tmp_path)
    events = []
    running = _running_copy(tmp_path, events)
    (tmp_path / changed).write_bytes(b"changed since")

    running.open()

    assert events == ["stopped listening", "fresh copy started", "quit"]


def test_one_of_its_files_it_can_no_longer_read_counts_as_changed(tmp_path):
    _line_as_it_started(tmp_path)
    events = []
    running = _running_copy(tmp_path, events)
    (tmp_path / "line_page.py").unlink()

    running.open()

    assert events == ["stopped listening", "fresh copy started", "quit"]


def test_the_fresh_copy_runs_this_program_on_the_same_python_opening_its_window_with_no_console():
    started = []

    line_tray.start_a_fresh_copy(lambda command, **options: started.append((command, options)))

    assert started == [([sys.executable, "-m", "line", "--open"],
                        {"cwd": line_tray.CHECKOUT, "creationflags": subprocess.CREATE_NO_WINDOW})]


def test_the_window_is_titled_line_wears_its_icon_and_is_dark_before_its_page_loads(offscreen_app, tmp_path):
    page = tmp_path / "line.html"
    page.write_text("<!doctype html><title>Line</title>", encoding="utf-8")
    profile = line_tray.web_profile(tmp_path / "storage")
    view = line_tray.open_window(page, profile, line_tray.this_copy(None))
    loaded = QSignalSpy(view.loadFinished)
    view.show()
    try:
        assert view.windowTitle() == "Line"
        assert _pixels(view.windowIcon()) == _pixels(QIcon(str(line_tray.ICON)))
        assert view.page().backgroundColor().name() == line_page.DARK_BACKGROUND
        assert view.page().profile() is profile
        assert loaded.wait(10000)
        assert view.url() == QUrl.fromLocalFile(str(page))
    finally:
        _closed(view)


def test_the_window_keeps_what_its_page_remembers_and_its_cache_on_disk_where_it_is_told(offscreen_app, tmp_path):
    profile = line_tray.web_profile(tmp_path / "storage")

    assert not profile.isOffTheRecord()
    assert Path(profile.persistentStoragePath()) == tmp_path / "storage"
    assert Path(profile.cachePath()).is_relative_to(tmp_path / "storage")


def test_a_window_he_closes_is_gone_so_the_next_show_opens_a_fresh_one(offscreen_app, tmp_path):
    page = tmp_path / "line.html"
    page.write_text("<!doctype html><title>Line</title>", encoding="utf-8")
    profile = line_tray.web_profile(tmp_path / "storage")
    opened = []

    def open_window(shown: Path):
        opened.append(line_tray.open_window(shown, profile, line_tray.this_copy(None)))
        return opened[-1]

    window = line_tray.LineWindow(page, open_window=open_window, write_now=lambda: None,
                                  write_in_background=lambda: None, bring_to_front=bring_to_front)
    try:
        window.show()
        assert opened[0].isVisible()
        _closed(opened[0])
        assert not window.is_open

        window.show()

        _, reopened = opened
        assert reopened.isVisible()
    finally:
        window.close()
        _let_closed_windows_go()


def test_these_tests_run_the_window_on_the_qt_line_itself_runs_on(offscreen_app):
    qt_home = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PrefixPath)).resolve()

    assert qt_home.is_relative_to(Path(PyQt6.__file__).parent.resolve())


def _taskbar_identity() -> str:
    claimed = ctypes.c_void_p()
    ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(claimed))
    try:
        return ctypes.wstring_at(claimed.value) if claimed.value else ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(claimed)


def test_the_window_gets_a_taskbar_button_of_its_own_not_one_shared_with_other_python_programs(offscreen_app):
    assert _taskbar_identity() == "Local.Line"


def test_the_web_view_draws_without_the_graphics_card_so_a_fault_there_cannot_take_the_tray_down(offscreen_app):
    assert offscreen_app.arguments()[1:] == ["--webEngineArgs", "--disable-gpu"]


STATE = line_tray.CHECKOUT / "state"


def test_the_copy_he_uses_keeps_lines_name_its_place_and_its_taskbar_button():
    copy = line_tray.this_copy(None)

    assert (copy.title, copy.server_name, copy.app_user_model_id) == ("Line", "line_tray", "Local.Line")
    assert (copy.page, copy.storage) == (STATE / "line.html", STATE / "line_storage")


def test_a_preview_names_its_feature_and_keeps_out_of_the_way_of_the_copy_he_uses():
    copy = line_tray.this_copy(Preview("the amber L"))

    assert copy.title == "Line \u2014 preview of the amber L"
    assert (copy.server_name, copy.app_user_model_id) == ("line_tray_preview", "Local.Line.Preview")
    assert (copy.page, copy.storage) == (STATE / "line.html", STATE / "line_storage")


def _center(icon: QIcon):
    image = icon.pixmap(256, 256).toImage()
    color = image.pixelColor(50, 128)
    return color.red(), color.green(), color.blue()


def test_a_previews_l_is_amber_where_the_copy_he_uses_wears_the_pink_one(offscreen_app):
    assert _center(line_tray.this_copy(None).icon()) == MAGENTA
    assert _center(line_tray.this_copy(Preview("the amber L")).icon()) == PREVIEW_INK


def test_a_preview_window_keeps_its_title_after_its_page_names_itself(offscreen_app, tmp_path):
    page = tmp_path / "line.html"
    page.write_text("<!doctype html><title>Line</title>", encoding="utf-8")
    copy = line_tray.this_copy(Preview("the amber L"))
    profile = line_tray.web_profile(tmp_path / "storage")
    view = line_tray.open_window(page, profile, copy)
    loaded = QSignalSpy(view.loadFinished)
    view.show()
    try:
        assert loaded.wait(10000)
        QApplication.processEvents()
        assert view.windowTitle() == copy.title
        assert _center(view.windowIcon()) == PREVIEW_INK
    finally:
        _closed(view)


SENDS_A_REQUEST_AND_HANGS_UP = """
import sys
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtNetwork import QLocalSocket
app = QCoreApplication([])
sender = QLocalSocket()
sender.connectToServer(sys.argv[1])
assert sender.waitForConnected(5000)
sender.write(sys.argv[2].encode())
sender.waitForBytesWritten(5000)
"""


def _heard_from_a_launch(server, request: str, heard: list[str]) -> None:
    launch = subprocess.Popen([sys.executable, "-c", SENDS_A_REQUEST_AND_HANGS_UP, server.serverName(), request],
                              creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 10
    while not heard and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    launch.wait(timeout=10)


def test_an_open_sent_by_a_launch_that_hangs_up_at_once_is_still_heard(offscreen_app):
    heard = []
    server = line_tray.serve_requests("line_tray_test_" + str(os.getpid()), open_line=lambda: heard.append("open"),
                                      quit_line=lambda: heard.append("quit"))
    try:
        _heard_from_a_launch(server, "open", heard)

        assert heard == ["open"]
    finally:
        server.close()


def test_a_quit_sent_by_another_copy_of_line_quits_this_one(offscreen_app):
    heard = []
    server = line_tray.serve_requests("line_tray_test_quit_" + str(os.getpid()), open_line=lambda: heard.append("open"),
                                      quit_line=lambda: heard.append("quit"))
    try:
        _heard_from_a_launch(server, "quit", heard)

        assert heard == ["quit"]
    finally:
        server.close()


def test_a_preview_takes_over_from_the_line_he_uses_and_from_an_earlier_preview():
    assert line_tray.this_copy(Preview("the amber L")).copies_it_replaces == ("line_tray", "line_tray_preview")


def test_the_line_he_uses_takes_the_work_back_from_a_preview():
    assert line_tray.this_copy(None).copies_it_replaces == ("line_tray_preview",)


def test_a_preview_window_says_until_when_it_keeps_the_work_and_the_line_he_uses_says_nothing_of_it():
    preview = line_tray.this_copy(Preview("the amber L"))

    assert line_tray.title_until(preview, datetime(2026, 10, 12, 19, 58, tzinfo=UTC)) == preview.title + ", until 20:58"
    assert line_tray.title_until(line_tray.this_copy(None), datetime(2026, 10, 12, 19, 58, tzinfo=UTC)) == "Line"


def test_handing_back_starts_the_line_he_uses_in_the_tray_with_no_console():
    started = []

    line_tray.hand_back(lambda command, **options: started.append((command, options)))

    assert started == [([sys.executable, "-m", "line"],
                        {"cwd": line_tray.the_line_he_uses(), "creationflags": subprocess.CREATE_NO_WINDOW})]


def test_the_line_he_uses_runs_from_the_checkout_every_worktree_of_this_one_belongs_to():
    assert (line_tray.the_line_he_uses() / ".git").is_dir()


AT_ONCE_S = 2


def test_waiting_for_a_copy_that_is_not_running_ends_at_once(offscreen_app):
    started = time.monotonic()

    assert line_tray.wait_until_gone("line_tray_test_nobody_" + str(os.getpid()))
    assert time.monotonic() - started < AT_ONCE_S

