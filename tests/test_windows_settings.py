import tomllib
from pathlib import Path

from app_support.launcher import launchers

from line.line_tray import TASKBAR_IDENTITY

REPO_ROOT = Path(__file__).resolve().parents[1]
LISTED = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["haglio"]


def test_what_the_list_starts_is_one_of_this_checkouts_launchers():
    declared = {launcher.file for launcher in launchers(REPO_ROOT)}

    assert {spec["launcher"] for spec in LISTED["shortcuts"].values()} <= declared


def test_the_icon_each_shortcut_shows_is_in_this_checkout():
    for spec in LISTED["shortcuts"].values():
        assert (REPO_ROOT / spec["icon"]).is_file(), spec["icon"]


def test_each_shortcut_carries_the_identity_the_app_claims():
    assert {spec["app-id"] for spec in LISTED["shortcuts"].values()} == {TASKBAR_IDENTITY}


def _places_and_launcher(shortcut: str) -> tuple[list[str], str]:
    listed = LISTED["shortcuts"][shortcut]
    return listed["places"], listed["launcher"]


def test_line_starts_in_the_tray_when_he_signs_in():
    assert _places_and_launcher("Line in the tray") == (["startup"], "launch_line_in_the_tray.vbs")


def test_the_shortcut_he_pins_to_the_taskbar_from_lines_folder_opens_its_window():
    assert _places_and_launcher("Line") == (["taskbar", "checkout"], "launch_line.vbs")
