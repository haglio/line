import os
import subprocess
import sys
from pathlib import Path

import pytest
from app_support.launch_smoke import (
    assert_an_unresolvable_import_is_caught,
    assert_every_import_resolves,
    assert_the_walk_reached,
    launch_imports,
)
from app_support.launcher import dry_run

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "line"
LAUNCHER = REPO_ROOT / "launch_line.vbs"
LAUNCH_FILES = (REPO_ROOT / PACKAGE / "__main__.py", REPO_ROOT / PACKAGE / "line_tray.py")

runs_the_launcher = pytest.mark.skipif(sys.platform != "win32", reason="the Windows script host")


def _launch_imports() -> list[str]:
    return launch_imports(PACKAGE, LAUNCH_FILES)


def _the_interpreter_that_will_run(named_by_the_launcher: str) -> str:
    return named_by_the_launcher if Path(named_by_the_launcher).is_file() else sys.executable


def _run_the_launchs_way(statements: list[str]) -> subprocess.CompletedProcess:
    report = dry_run(LAUNCHER)
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run([_the_interpreter_that_will_run(report.value("interpreter")), "-c", "\n".join(statements)],
                          cwd=report.value("directory"), env=env, capture_output=True, text=True, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@runs_the_launcher
def test_the_launch_imports_everything_it_names():
    assert_every_import_resolves(_run_the_launchs_way, _launch_imports())


def test_the_walk_reaches_the_page_and_the_window_the_tray_imports():
    assert_the_walk_reached(_launch_imports(), ("from line import line_page", "PyQt6.QtWebEngineWidgets"))


@runs_the_launcher
def test_a_launch_import_that_cannot_resolve_fails_here():
    assert_an_unresolvable_import_is_caught(_run_the_launchs_way, _launch_imports(), "line.line_page")
