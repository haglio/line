import sys
from pathlib import Path

import pytest
from app_support.launcher import assert_launchers_match_their_specs, dry_run, launchers

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "launch_line.vbs"
IN_THE_TRAY = REPO_ROOT / "launch_line_in_the_tray.vbs"
PREVIEW = REPO_ROOT / "launch_preview_branch.vbs"

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="the Windows script host")


def test_every_launcher_is_what_its_spec_renders():
    assert_launchers_match_their_specs(REPO_ROOT)


@on_windows
def test_the_launch_link_opens_lines_window_from_this_checkout_on_its_venv():
    report = dry_run(LAUNCHER)

    assert Path(report.value("interpreter")).parent == REPO_ROOT / ".venv" / "Scripts"
    assert Path(report.value("directory")) == REPO_ROOT
    assert report.value("arguments") == "-m line --open"


@on_windows
def test_at_sign_in_line_starts_in_the_tray_without_opening_its_window():
    report = dry_run(IN_THE_TRAY)

    assert Path(report.value("directory")) == REPO_ROOT
    assert report.value("arguments") == "-m line"


@pytest.mark.parametrize("launcher", launchers(REPO_ROOT), ids=lambda launcher: launcher.file)
def test_every_launcher_starts_line_through_its_log_never_straight_into_a_hidden_first_window(launcher):
    assert launcher.log


@on_windows
def test_a_preview_runs_its_own_checkout_on_the_primarys_venv():
    report = dry_run(PREVIEW)

    assert Path(report.value("directory")) == REPO_ROOT
    assert Path(report.value("interpreter")) == Path(report.value("primary")) / ".venv" / "Scripts" / "python.exe"
    assert report.value("arguments") == "-m line --open"
