import ctypes
import gc
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import PyQt6
import pytest
from app_support.testing import start_again_stand_in, update_waiting_for
from PyQt6.QtCore import QEvent, QLibraryInfo, Qt, QUrl
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from shared_ui.foreground import bring_to_front
from shared_ui.palette import MAGENTA, PREVIEW_INK
from shared_ui.preview import Preview

from line import line_page, line_tray


@pytest.fixture(scope="module")
def offscreen_app(tmp_path_factory):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or line_tray.application(["test_line_tray"], line_tray.this_copy(None))
    assert app.platformName() == "offscreen"
    _start_the_web_engine(tmp_path_factory.mktemp("web-engine"))
    return app


def _start_the_web_engine(place: Path) -> None:
    page = place / "line.html"
    page.write_text("<!doctype html><title>Line</title>", encoding="utf-8")
    profile = line_tray.web_profile(place / "storage")
    view = line_tray.open_window(page, profile, line_tray.this_copy(None), _nothing_asked)
    view.show()
    try:
        assert _page_named(view, "Line", seconds=120), "the web engine never loaded its first page"
    finally:
        _closed(view)


def _page_named(view, title: str, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while view.title() != title and time.monotonic() < deadline:
        QApplication.processEvents()
    return view.title() == title


def _nothing_asked(_number, _week):
    raise AssertionError("no button was pressed")


def _pixels(icon: QIcon):
    return icon.pixmap(32, 32).toImage()


def _closed(view) -> None:
    view.close()
    _let_closed_windows_go()


def _let_closed_windows_go() -> None:
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


@pytest.fixture(autouse=True)
def _each_test_s_web_profiles_go_with_it():
    yield
    _let_closed_windows_go()
    gc.collect()
    _let_closed_windows_go()


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


class _Window:
    def __init__(self, *, showing):
        self.is_showing = showing


@pytest.fixture
def an_update_waiting():
    events = []
    with update_waiting_for(line_tray.CHECKOUT), \
            start_again_stand_in(side_effect=lambda pid, launcher: events.append((pid, launcher))):
        yield events


def test_an_update_waiting_for_line_starts_it_again_in_the_tray_once_its_window_is_closed(an_update_waiting):
    watch = line_tray.watch_for_updates(line_tray.this_copy(None), _Window(showing=False),
                                        quit_this_copy=lambda: an_update_waiting.append("quit"))

    watch.tick()

    assert an_update_waiting == [(os.getpid(), line_tray.CHECKOUT / "launch_line_in_the_tray.vbs"), "quit"]


def test_an_update_waits_while_lines_window_is_showing(an_update_waiting):
    watch = line_tray.watch_for_updates(line_tray.this_copy(None), _Window(showing=True),
                                        quit_this_copy=lambda: an_update_waiting.append("quit"))

    watch.tick()

    assert an_update_waiting == []


def test_a_preview_keeps_no_watch_for_updates():
    preview = line_tray.this_copy(Preview(feature="a new column"))

    assert line_tray.watch_for_updates(preview, _Window(showing=False), quit_this_copy=lambda: None) is None


def test_the_fresh_copy_runs_this_program_on_the_same_python_opening_its_window_with_no_console():
    started = []

    line_tray.start_a_fresh_copy(lambda command, **options: started.append((command, options)))

    assert started == [([sys.executable, "-m", "line", "--open"],
                        {"cwd": line_tray.CHECKOUT, "creationflags": subprocess.CREATE_NO_WINDOW})]


def test_the_window_is_titled_line_wears_its_icon_and_is_dark_before_its_page_loads(offscreen_app, tmp_path):
    page = tmp_path / "line.html"
    page.write_text("<!doctype html><title>Line</title>", encoding="utf-8")
    profile = line_tray.web_profile(tmp_path / "storage")
    view = line_tray.open_window(page, profile, line_tray.this_copy(None), _nothing_asked)
    view.show()
    try:
        assert view.windowTitle() == "Line"
        assert _pixels(view.windowIcon()) == _pixels(QIcon(str(line_tray.ICON)))
        assert view.page().backgroundColor().name() == line_page.DARK_BACKGROUND
        assert view.page().profile() is profile
        assert _page_named(view, "Line")
        assert view.url() == QUrl.fromLocalFile(str(page))
    finally:
        _closed(view)


def test_pressing_work_on_this_asks_for_that_number_and_week_and_leaves_the_page_where_it_is(offscreen_app, tmp_path):
    page = tmp_path / "line.html"
    page.write_text("<!doctype html><title>Line</title><a id=b href='https://line-work.invalid/crashes/2026-W44'>Work on this</a>",
                    encoding="utf-8")
    asked = []
    profile = line_tray.web_profile(tmp_path / "storage")
    view = line_tray.open_window(page, profile, line_tray.this_copy(None),
                                 lambda number, week: asked.append((number, week)))
    view.show()
    try:
        assert _page_named(view, "Line")
        view.page().runJavaScript("document.getElementById('b').click()")
        deadline = time.monotonic() + 10
        while not asked and time.monotonic() < deadline:
            QApplication.processEvents()
        assert asked == [("crashes", "2026-W44")]
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
        opened.append(line_tray.open_window(shown, profile, line_tray.this_copy(None), _nothing_asked))
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
    copy = line_tray.this_copy(Preview("the yellow L"))

    assert copy.title == "Line \u2014 preview of the yellow L"
    assert (copy.server_name, copy.app_user_model_id) == ("line_tray_preview", "Local.Line.Preview")
    assert (copy.page, copy.storage) == (STATE / "line.html", STATE / "line_storage")


def _center(icon: QIcon):
    image = icon.pixmap(256, 256).toImage()
    color = image.pixelColor(50, 128)
    return color.red(), color.green(), color.blue()


def test_a_previews_l_is_yellow_where_the_copy_he_uses_wears_the_pink_one(offscreen_app):
    assert _center(line_tray.this_copy(None).icon()) == MAGENTA
    assert _center(line_tray.this_copy(Preview("the yellow L")).icon()) == PREVIEW_INK


def test_a_preview_window_keeps_its_title_after_its_page_names_itself(offscreen_app, tmp_path):
    page = tmp_path / "line.html"
    page.write_text("<!doctype html><title>Line</title>", encoding="utf-8")
    copy = line_tray.this_copy(Preview("the yellow L"))
    profile = line_tray.web_profile(tmp_path / "storage")
    view = line_tray.open_window(page, profile, copy, _nothing_asked)
    view.show()
    try:
        assert _page_named(view, "Line")
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
    assert line_tray.this_copy(Preview("the yellow L")).copies_it_replaces == ("line_tray", "line_tray_preview")


def test_the_line_he_uses_takes_the_work_back_from_a_preview():
    assert line_tray.this_copy(None).copies_it_replaces == ("line_tray_preview",)


def test_a_preview_window_says_until_when_it_keeps_the_work_and_the_line_he_uses_says_nothing_of_it():
    preview = line_tray.this_copy(Preview("the yellow L"))

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


def _quitting(events: list[str], *, preview: bool) -> line_tray.Quitting:
    return line_tray.Quitting(preview=preview, quit_line=lambda: events.append("quit"),
                              hand_back=lambda: events.append("handed back"))


def test_a_preview_hands_the_work_back_when_its_hour_is_up():
    events, asked = [], []

    _quitting(events, preview=True).hand_back_after_the_hour(lambda ms, then: asked.append((ms, then)))

    [(ms, then)] = asked
    assert ms == line_tray.HAND_BACK_AFTER_MINUTES * 60_000
    then()
    assert events == ["handed back", "quit"]


def test_the_line_he_uses_never_hands_anything_back():
    asked = []

    _quitting([], preview=False).hand_back_after_the_hour(lambda ms, then: asked.append((ms, then)))

    assert asked == []


def test_quit_from_the_tray_menu_is_written_down_and_quits_the_line_he_uses(caplog):
    caplog.set_level(logging.INFO, logger="line")
    events = []

    _quitting(events, preview=False).from_the_tray_menu()

    assert (caplog.messages, events) == (["asked to quit by the tray menu"], ["quit"])


def test_a_preview_quit_from_its_tray_menu_hands_the_work_back_before_it_quits(caplog):
    caplog.set_level(logging.INFO, logger="line")
    events = []

    _quitting(events, preview=True).from_the_tray_menu()

    assert caplog.messages == ["asked to quit by the tray menu", "handing back to the Line he uses"]
    assert events == ["handed back", "quit"]


def test_a_copy_another_takes_over_from_says_so_and_quits_without_handing_anything_back(caplog):
    caplog.set_level(logging.INFO, logger="line")
    events = []

    _quitting(events, preview=True).for_another_copy()

    assert (caplog.messages, events) == (["another copy of Line took over"], ["quit"])


def test_windows_announcing_the_end_of_his_session_is_written_down(offscreen_app, caplog):
    caplog.set_level(logging.INFO, logger="line")

    offscreen_app.commitDataRequest.emit(None)

    assert caplog.messages == ["Windows announced that his session is ending"]


AN_EXIT_CODE = 3


def test_the_last_line_line_writes_gives_the_code_it_exits_with(caplog):
    caplog.set_level(logging.INFO, logger="line")

    assert line_tray.run_until_it_ends(lambda: AN_EXIT_CODE) == AN_EXIT_CODE
    assert caplog.messages == [f"exiting with code {AN_EXIT_CODE}"]


RUNS_LINE_AND_QUITS_IT_FROM_ITS_TRAY_MENU = """
import sys
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMenu
from line import line_tray
copy = line_tray.LineCopy("Line", sys.argv[1], "Local.Line.Test", Path(sys.argv[2]), None)
app = line_tray.application(["line_test"], copy)

def quit_from_the_tray_menu():
    menus = [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QMenu)]
    if not menus:
        QTimer.singleShot(10, quit_from_the_tray_menu)
        return
    [quit_item] = [action for action in menus[0].actions() if action.text() == "Quit"]
    quit_item.trigger()

QTimer.singleShot(0, quit_from_the_tray_menu)
sys.exit(line_tray.run_the_tray(app, copy, open_now=False))
"""


def _what_line_wrote(state: Path) -> list[str]:
    return [line.partition(": ")[2] for line in (state / "line.log").read_text(encoding="utf-8").splitlines()]


def test_quit_from_the_tray_menu_is_the_last_thing_line_writes_before_its_exit_code(tmp_path):
    subprocess.run([sys.executable, "-c", RUNS_LINE_AND_QUITS_IT_FROM_ITS_TRAY_MENU,
                    "line_tray_test_menu_" + str(os.getpid()), str(tmp_path)],
                   cwd=line_tray.CHECKOUT, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True,
                   timeout=60, check=False, creationflags=subprocess.CREATE_NO_WINDOW)

    assert _what_line_wrote(tmp_path)[-2:] == ["asked to quit by the tray menu", "exiting with code 0"]



def test_the_page_is_written_now_by_a_hidden_console_child(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(line_tray.subprocess, "run", lambda command, **options: ran.append((command, options)))

    line_tray._PageWriter(tmp_path / "line.html").now()

    (command, options), = ran
    assert command == line_page.refresh_command(tmp_path / "line.html", 1, line_page.WATCH_EVERY_S)
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert options["startupinfo"].wShowWindow == 0


def test_the_page_is_written_in_the_background_by_one_hidden_child_at_a_time(tmp_path):
    started = []

    class _StillWriting:
        def __init__(self, command, **options):
            started.append((command, options))

        def poll(self):
            return None

    writer = line_tray._PageWriter(tmp_path / "line.html", start=_StillWriting)

    writer.in_background()
    writer.in_background()

    (command, options), = started
    assert command == line_page.refresh_command(tmp_path / "line.html", 1, line_page.WATCH_EVERY_S)
    assert options["startupinfo"].wShowWindow == 0
