import sys
from pathlib import Path

from line import ship_time_tray
from line.ship_time_tray import CHECKOUT, FinishedSessionMessages, OneAtATime
from line.ship_time_work import FinishedSession

SESSION = FinishedSession("2026-W44 crashes", "812181fb", "whole-id", "C:/made/up", "Work on app crashes", "Click")


class Started:
    def __init__(self, still_going=True):
        self.still_going = still_going

    def poll(self):
        return None if self.still_going else 0


def test_a_job_still_going_is_not_started_again_and_one_that_ended_is():
    started = []
    job = Started()

    def start(command, **kwargs):
        started.append((command, kwargs["cwd"]))
        return job

    one = OneAtATime(["a-made-up-command"], start=start)
    one.in_background()
    one.in_background()
    assert started == [(["a-made-up-command"], CHECKOUT)]

    job.still_going = False
    one.in_background()
    assert len(started) == 2


def test_the_click_starts_the_work_in_a_process_of_its_own():
    started = []

    ship_time_tray.start_the_work(Path("made-up-state"), "crashes", "2026-W44",
                                  start=lambda command, **kwargs: started.append((command, kwargs["cwd"])))

    assert started == [([sys.executable, "-m", "line.ship_time_work", "--state", "made-up-state",
                         "--number", "crashes", "--week", "2026-W44"], CHECKOUT)]


class FakeTray:
    def __init__(self):
        self.shown = []

    def showMessage(self, title, body, *_):
        self.shown.append((title, body))


def test_clicking_the_message_opens_the_session_it_told_about_once():
    tray, opened = FakeTray(), []
    messages = FinishedSessionMessages(tray, open_in_claude_desktop=lambda *args: opened.append(args),
                                       claude=lambda: "claude")

    messages.tell(SESSION)
    messages.clicked()
    messages.clicked()

    assert tray.shown == [("Work on app crashes", "Click")]
    assert opened == [("claude", "812181fb", "whole-id", "C:/made/up")]


def test_ship_time_is_measured_in_a_process_of_its_own_into_line_s_state():
    started = []

    ship_time_tray.measuring_ship_time(Path("made-up-state"), start=lambda command, **_: started.append(command) or Started()).in_background()

    assert started == [[sys.executable, "-m", "line.ship_time", "--out", str(Path("made-up-state") / "ship_time.json")]]


class FakeSignal:
    def __init__(self):
        self.connected = []

    def connect(self, slot):
        self.connected.append(slot)


class FakeTrayWithItsSignal(FakeTray):
    def __init__(self):
        super().__init__()
        self.messageClicked = FakeSignal()


def test_a_click_on_the_tray_s_message_is_listened_for(tmp_path):
    tray = FakeTrayWithItsSignal()

    watching = ship_time_tray.looking_after_sessions(tmp_path, tray)

    assert len(tray.messageClicked.connected) == 1
    watching.tick()
    assert tray.shown == []
