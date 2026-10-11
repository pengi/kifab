"""Run core work off the UI thread; progress arrives as Qt signals."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from kifab.core.issues import Issue
from kifab.core.reporter import Reporter

Task = Callable[[Reporter], object]


class Job(QThread):
    """Runs ``task(reporter)`` on its own thread.

    The job is the reporter: :meth:`step` and :meth:`issue` are called on the
    worker thread and re-emitted as signals, which Qt delivers to slots on the
    UI thread (queued connections).
    """

    stepped = Signal(str, str)
    found = Signal(object)  # Issue
    done = Signal(object)  # the task's return value
    failed = Signal(str)

    def __init__(self, title: str, task: Task) -> None:
        super().__init__()
        self.title = title
        self._task = task

    def run(self) -> None:
        try:
            result = self._task(self)
        except Exception as exc:  # shown to the user; the UI must survive it
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(result)

    # Reporter protocol (called on the worker thread)
    def step(self, name: str, detail: str) -> None:
        self.stepped.emit(name, detail)

    def issue(self, issue: Issue) -> None:
        self.found.emit(issue)
