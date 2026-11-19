import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from line import ship_time_tray
from line.ship_time_tray import CHECKOUT, OneAtATime


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


def test_the_click_opens_a_claude_desktop_session_in_the_folder_the_checkouts_sit_in(tmp_path):
    (tmp_path / "ship_time.json").write_text(
        '{"weeks": {"2026-W43": {"week": "2026-W43", "crashes": 1}, "2026-W44": {"week": "2026-W44", "crashes": 2}},'
        ' "taken_back": []}', encoding="utf-8")
    opened = []

    ship_time_tray.open_the_work(tmp_path, "crashes", "2026-W44", opened.append)

    [link] = opened
    assert link.startswith("claude://code/new?folder=")
    assert quote(str(ship_time_tray.folder_the_checkouts_sit_in()), safe="") in link
    assert ship_time_tray.folder_the_checkouts_sit_in().name != "fun_time"


def test_a_click_with_nothing_to_compare_opens_nothing(tmp_path):
    opened = []

    ship_time_tray.open_the_work(tmp_path, "crashes", "2026-W44", opened.append)

    assert opened == []


def test_ship_time_is_measured_in_a_process_of_its_own_into_line_s_state():
    started = []

    ship_time_tray.measuring_ship_time(
        Path("made-up-state"), start=lambda command, **_: started.append(command) or Started(),
        session_is_live=lambda: False).in_background()

    assert started == [[sys.executable, "-m", "line.ship_time", "--out", str(Path("made-up-state") / "ship_time.json")]]


def test_ship_time_waits_while_a_session_of_his_is_live_and_measures_once_none_is():
    started, live = [], [True]
    measuring = ship_time_tray.measuring_ship_time(
        Path("made-up-state"), start=lambda command, **_: started.append(command) or Started(still_going=False),
        session_is_live=lambda: live[0])

    measuring.in_background()
    live[0] = False
    measuring.in_background()

    assert len(started) == 1


def test_ship_time_is_measured_below_normal_priority_so_what_he_is_using_comes_first():
    flags = []

    ship_time_tray.measuring_ship_time(
        Path("made-up-state"), start=lambda command, **kwargs: flags.append(kwargs["creationflags"]) or Started(),
        session_is_live=lambda: False).in_background()

    assert flags[0] & subprocess.BELOW_NORMAL_PRIORITY_CLASS


class FakeRequest:
    def __init__(self, url):
        self._url = url
        self.rejected = False

    def url(self):
        return self

    def toString(self):
        return self._url

    def reject(self):
        self.rejected = True


def test_a_press_of_work_on_this_is_answered_and_kept_off_the_page():
    asked, request = [], FakeRequest("https://line-work.invalid/crashes/2026-W44")

    ship_time_tray.answer_a_press_of_work_on_this(request, lambda number, week: asked.append((number, week)))

    assert asked == [("crashes", "2026-W44")]
    assert request.rejected


def test_any_other_link_is_left_to_the_page():
    request = FakeRequest("file:///made/up/line.html")

    ship_time_tray.answer_a_press_of_work_on_this(request, lambda *_: None)

    assert not request.rejected
