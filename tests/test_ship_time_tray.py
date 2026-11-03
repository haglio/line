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
        Path("made-up-state"), start=lambda command, **_: started.append(command) or Started()).in_background()

    assert started == [[sys.executable, "-m", "line.ship_time", "--out", str(Path("made-up-state") / "ship_time.json")]]
