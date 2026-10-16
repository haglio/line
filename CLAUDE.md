# line — Project-Specific Instructions

Shared rules are in the global `~/.claude/CLAUDE.md`. This file contains only Line's own.

**Keep this file short.** One bullet per rule; remove or consolidate, never just append.

## What it is

- Line is his tray app for Fun Time's integration line: who holds the machine, who waits, and how each run ended. `line/line_tray.py` is the tray and its window; `line/line_page.py` writes the page the window shows.
- It shows exactly the runs Fun Time writes down in its production checkout's `state/integration_runs/` (the format is `app_support.integration_runs`), and names each row from the Claude transcripts under `~/.claude/projects`. It never looks at running programs to find a run. It writes only into its own checkout's `state/`: the page, what the page remembers, and its log.

## Test commands

```bash
.venv/Scripts/python.exe -m pytest -x
```

From a worktree, run the primary checkout's `.venv` with the worktree as the working directory.

## Judging a branch before it ships

- `launch_preview_branch.vbs` in a worktree runs that worktree's Line on the primary's venv, wearing the amber L and the session's name; it stands in for his Line and hands the work back when he quits it or after an hour.
- Hand it to him as `[▶ Launch the Line preview](http://127.0.0.1:41777/launch?t=a780245a4cdcfcb2a2e3b365&p=<the worktree's launch_preview_branch.vbs, percent-encoded>&b=<branch>)`.

## Shipping

- Through a pull request and the merge queue, as every Haglio repo: `gh pr create --fill --draft` as the preview goes out, `gh pr ready` on his word.
- Then nothing: app_support's production updater brings this checkout up to what shipped, the libraries it pins first, as soon as nothing of his runs from it, and every launcher here does the same just before it starts Line. So a change reaches him the next time he opens Line. Never pull the primary checkout or install into its venv yourself.
- Launchers are rendered from `pyproject.toml` by `python -m app_support.launcher --write`; the shortcuts are kept by `python -m app_support.windows_settings --write`: `Line.lnk` in this folder, which he pins to his taskbar and which opens Line's window, and the one that starts Line in the tray at sign-in.
- Every fixture is invented: a session name, a branch, a path. Never one copied from his machine.
