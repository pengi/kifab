"""Rendering results for the terminal (text) and for machines (``--json``)."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from typing import TextIO

from kifab.core.issues import Issue, Severity, sort_issues

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_BAD_INPUT = 2


def format_issue(issue: Issue) -> str:
    where = " ".join(x for x in (issue.path, issue.ref and f"[{issue.ref}]") if x)
    text = f"{issue.severity.value}: {issue.message}"
    if where:
        text += f"  ({where})"
    if issue.hint:
        text += f"\n    -> {issue.hint}"
    return text


def print_issues(issues: Iterable[Issue], out: TextIO | None = None) -> None:
    stream = out or sys.stdout
    for issue in sort_issues(issues):
        print(format_issue(issue), file=stream)


def summary_line(issues: Iterable[Issue]) -> str:
    counts = {s: 0 for s in Severity}
    for issue in issues:
        counts[issue.severity] += 1
    return (
        f"{counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), "
        f"{counts[Severity.INFO]} note(s)"
    )


def print_json(payload: Mapping[str, object], out: TextIO | None = None) -> None:
    print(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        file=out or sys.stdout,
    )


def issues_json(issues: Iterable[Issue]) -> list[dict[str, str]]:
    return [i.to_json() for i in sort_issues(issues)]


def error_line(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
