from __future__ import annotations

import json


class GitHubDidNotAnswer(Exception):
    pass


def answered(answer) -> str:
    if answer.returncode:
        raise GitHubDidNotAnswer(answer.stderr)
    return answer.stdout


def ask_github(fields: str, run) -> dict:
    stdout = answered(run(["gh", "api", "graphql", "-f", f"query={{{fields}}}"], capture_output=True, text=True))
    return json.loads(stdout or "{}").get("data") or {}
