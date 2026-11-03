import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from line import ship_time_work
from line.ship_time import RECORD_NAME

BEFORE = {"week": "2026-W43", "median_minutes": 84}
LAST = {"week": "2026-W44", "median_minutes": 144}
FOLDER = Path("C:/made/up/workspace")


def state_with_weeks(place):
    (place / RECORD_NAME).write_text(json.dumps({"weeks": {row["week"]: row for row in (BEFORE, LAST)},
                                                 "taken_back": []}), encoding="utf-8")
    return place


def test_a_click_is_read_off_the_link_the_page_drew():
    assert ship_time_work.asked_for("https://line-work.invalid/median_minutes/2026-W44") == ("median_minutes",
                                                                                           "2026-W44")


def test_any_other_link_is_no_click():
    assert ship_time_work.asked_for("file:///made/up/line.html") is None
    assert ship_time_work.asked_for("https://line-work.invalid/not_a_number/2026-W44") is None
    assert ship_time_work.asked_for("https://line-work.invalid/median_minutes") is None


def test_the_click_opens_a_new_claude_desktop_session_in_the_folder_with_its_brief_typed_in(tmp_path):
    link = ship_time_work.new_session_link(state_with_weeks(tmp_path), "median_minutes", "2026-W44", FOLDER)

    parts = urlsplit(link)
    asked = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "claude://code/new"
    assert asked["folder"] == [str(FOLDER)]
    assert "was 144, worse than the 84 of the week before" in asked["q"][0]
    assert ship_time_work.WHERE_THE_TIME_GOES in asked["q"][0]


def test_a_week_with_nothing_before_it_opens_nothing(tmp_path):
    assert ship_time_work.new_session_link(state_with_weeks(tmp_path), "median_minutes", "2026-W43", FOLDER) is None


def test_no_record_opens_nothing(tmp_path):
    assert ship_time_work.new_session_link(tmp_path, "median_minutes", "2026-W44", FOLDER) is None


def test_each_number_s_brief_says_what_to_find():
    assert ship_time_work.WHAT_GOT_THROUGH in ship_time_work.session_brief("bug_reports", "2026-W44", 9, 3)
