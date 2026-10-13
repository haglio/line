import re
from pathlib import Path

from app_support.dependencies import declared_dependencies

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

_JOB = re.compile(r'  "?([A-Za-z0-9_-]+)"?:\s*(#.*)?')
_CLOCK = re.compile(r'    "?timeout-minutes"?:\s*"?[1-9][0-9]*"?\s*(#.*)?')
_THE_FAMILYS_GATE = re.compile(r'    "?uses"?:\s*"?haglio/\.github/\.github/workflows/[a-z-]+\.yml@\S+"?\s*(#.*)?')


def _jobs_without_a_clock(workflow: str) -> list[str]:
    unclocked: list[str] = []
    current: str | None = None
    in_jobs = False
    for line in workflow.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if not line.startswith(" "):
            break
        named = _JOB.fullmatch(line)
        if named:
            current = named.group(1)
            unclocked.append(current)
        elif current in unclocked and (_CLOCK.fullmatch(line) or _THE_FAMILYS_GATE.fullmatch(line)):
            unclocked.remove(current)
    return unclocked


def test_every_test_runs_under_its_own_clock(pytestconfig):
    addopts = pytestconfig.getini("addopts")

    assert "--timeout=120" in addopts
    assert "--timeout-method=thread" in addopts


def test_every_workflow_job_runs_under_its_own_clock():
    unclocked = {path.name: _jobs_without_a_clock(path.read_text(encoding="utf-8"))
                 for path in sorted(WORKFLOWS.glob("*.y*ml"))}

    assert unclocked, f"no workflows found in {WORKFLOWS}"
    assert {name: jobs for name, jobs in unclocked.items() if jobs} == {}


def test_the_plugin_that_keeps_the_clock_is_declared_where_ci_installs_it():
    assert "pytest-timeout" in declared_dependencies(PYPROJECT)
