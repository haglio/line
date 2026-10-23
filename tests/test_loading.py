import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from shared_ui import loading_process

from line import line_tray

STARTS_LINE_THROUGH_A_LOADING_SCREEN = """
import json, sys
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import QTimer
from shared_ui.loading_window import Loading, LoadingCanceled
from line import line_tray

events = []
cancel = sys.argv[3] == "cancel"

class Recording:
    def say(self, step):
        events.append(f"said {step}")
        if cancel:
            raise LoadingCanceled

    def done(self):
        events.append("done")

copy = line_tray.LineCopy("Line", sys.argv[1], "Local.Line.Test", Path(sys.argv[2]), None)
app = line_tray.application(["line_test"], copy)

def shown(window):
    events.append("window shown")
    QTimer.singleShot(0, app.quit)

with patch.object(Loading, "at_the_click", return_value=Recording()), patch.object(line_tray.LineWindow, "show", shown):
    code = line_tray.run_the_tray(app, copy, open_now=True)
(Path(sys.argv[2]) / "events.json").write_text(json.dumps(events), encoding="utf-8")
sys.exit(code)
"""


def _start_line(state: Path, how: str) -> list[str]:
    subprocess.run([sys.executable, "-c", STARTS_LINE_THROUGH_A_LOADING_SCREEN,
                    "line_tray_test_loading_" + str(os.getpid()), str(state), how],
                   cwd=line_tray.CHECKOUT, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True,
                   timeout=60, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads((state / "events.json").read_text(encoding="utf-8"))


def test_the_loading_screen_walks_lines_start_and_goes_before_its_window_opens(tmp_path):
    assert _start_line(tmp_path, "start") == [
        *(f"said {step}" for step, _weight in line_tray.STEPS), "done", "window shown"]


def test_esc_on_the_loading_screen_ends_the_start_before_any_window_opens(tmp_path):
    assert _start_line(tmp_path, "cancel") == [f"said {line_tray.STEPS[0][0]}"]


def test_the_loading_screen_runs_in_a_process_of_its_own_wearing_lines_taskbar_button():
    copy = line_tray.this_copy(None)

    with patch.object(loading_process.LoadingProcess, "open") as opened:
        line_tray.open_the_loading_screen(copy)

    opened.assert_called_once_with(
        caption="Line Loading", wordmark="Line", icon=line_tray.ICON, preview=None, steps=line_tray.STEPS,
        cancel_hint="Press Esc to cancel opening Line", app_id=copy.app_user_model_id)
