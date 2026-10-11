"""Progress reporting protocol, so long-running work can drive any frontend."""

from __future__ import annotations

from typing import Protocol

from kifab.core.issues import Issue


class Reporter(Protocol):
    """Receives progress from the core while a command runs."""

    def step(self, name: str, detail: str) -> None:
        """A step finished (``name`` is short, e.g. "gerbers"; ``detail`` a summary)."""

    def issue(self, issue: Issue) -> None:
        """A finding was made (it is also part of the command's result)."""


class NullReporter:
    """Discards everything; the default when nobody is listening."""

    def step(self, name: str, detail: str) -> None:
        pass

    def issue(self, issue: Issue) -> None:
        pass


class RecordingReporter:
    """Keeps what it is told; handy for tests and for building reports."""

    def __init__(self) -> None:
        self.steps: list[tuple[str, str]] = []
        self.issues: list[Issue] = []

    def step(self, name: str, detail: str) -> None:
        self.steps.append((name, detail))

    def issue(self, issue: Issue) -> None:
        self.issues.append(issue)
