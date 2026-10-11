"""The ``.gitignore`` managed block: what never belongs in git.

kifab owns the lines between its markers; lines outside are the user's and kept.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from kifab.core import managed

MARKERS = managed.Markers(
    "# >>> kifab (managed block: kifab rewrites it; add your own lines outside)",
    "# <<< kifab",
)

#: Generated or machine-local files (patterns in .gitignore syntax).
IGNORED = (
    "build/",
    "kifab-backups/",
    "*-backups/",
    "~*.lck",
    "*.lck",
    "fp-info-cache",
    "*.kicad_prl",
    "_autosave-*",
    "*-bak",
    "*.bak",
    "\\#auto_saved_files#",
    ".history/",
    "*-save.kicad_pcb",
    "*-save.kicad_sch",
    "*-drc.json",
    "*-erc.json",
)


def block() -> str:
    return "\n".join(IGNORED) + "\n"


def update(text: str) -> str:
    """``text`` (a .gitignore, possibly empty) with the managed block current."""
    parts = managed.split(text, MARKERS)
    new = managed.render(MARKERS, block())
    if parts.block is None:
        prefix = text if not text or text.endswith("\n") else text + "\n"
        return prefix + ("\n" if prefix else "") + new
    return parts.before + new + parts.after


def is_current(text: str) -> bool:
    return managed.split(text, MARKERS).block == block()


def is_ignored(path: str) -> bool:
    """Whether a project-relative posix ``path`` matches the managed patterns."""
    parts = PurePosixPath(path).parts
    for pattern in IGNORED:
        pat = pattern.replace("\\#", "#")
        if pat.endswith("/"):
            if any(fnmatch.fnmatchcase(p, pat[:-1]) for p in parts[:-1]):
                return True
        elif any(fnmatch.fnmatchcase(p, pat) for p in parts):
            return True
    return False
