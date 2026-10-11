"""Managed blocks: kifab-owned regions inside files that users also edit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Markers:
    begin: str
    end: str


@dataclass(frozen=True)
class Split:
    """A file cut at its managed block. ``block`` excludes the marker lines."""

    before: str
    block: str | None
    after: str


def split(text: str, markers: Markers) -> Split:
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.rstrip("\r\n") == markers.begin]
    ends = [i for i, ln in enumerate(lines) if ln.rstrip("\r\n") == markers.end]
    if not starts or not ends or ends[0] < starts[0]:
        return Split(text, None, "")
    b, e = starts[0], next(i for i in ends if i > starts[0])
    return Split("".join(lines[:b]), "".join(lines[b + 1 : e]), "".join(lines[e + 1 :]))


def render(markers: Markers, block: str) -> str:
    body = block if block.endswith("\n") or not block else block + "\n"
    return f"{markers.begin}\n{body}{markers.end}\n"
