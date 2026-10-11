"""Typed findings shared by every command and frontend.

The core never prints. Checks and pipelines return :class:`Issue` objects; the CLI
renders them as text or JSON, the GUI as a list. ``code`` values are stable so
CI scripts can match on them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """How bad a finding is. Errors make a command fail (exit code 1)."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 2, "warning": 1, "info": 0}[self.value]


class Code(Enum):
    """Stable identifiers of the findings kifab reports."""

    # input / project
    BAD_INPUT = "bad-input"
    KICAD_OPEN = "kicad-open"
    PARSE_ERROR = "parse-error"
    # self-containment
    LIB_TABLE_MISSING = "lib-table-missing"
    LIB_OUTSIDE_PROJECT = "lib-outside-project"
    LIB_MISSING = "lib-missing"
    SYMBOL_LIB_UNKNOWN = "symbol-lib-unknown"
    SYMBOL_NOT_IN_LIB = "symbol-not-in-lib"
    FOOTPRINT_LIB_UNKNOWN = "footprint-lib-unknown"
    FOOTPRINT_NOT_IN_LIB = "footprint-not-in-lib"
    FOOTPRINT_MISSING = "footprint-missing"
    MODEL_OUTSIDE_PROJECT = "model-outside-project"
    MODEL_MISSING = "model-missing"
    # schematic vs board
    FOOTPRINT_MISMATCH = "footprint-mismatch"
    NOT_ON_BOARD = "not-on-board"
    NOT_IN_SCHEMATIC = "not-in-schematic"
    # vendored libraries vs kifab.lock
    LOCK_FILE_MISSING = "lock-file-missing"
    LOCK_HASH_MISMATCH = "lock-hash-mismatch"
    LOCK_SYMBOL_MISSING = "lock-symbol-missing"
    UNLOCKED_FILE = "unlocked-file"
    UNLOCKED_SYMBOL = "unlocked-symbol"
    # design rules
    NO_CONFIG = "no-config"
    README_APPENDED = "readme-appended"
    RULES_MISSING = "rules-missing"
    RULES_DRIFT = "rules-drift"
    RULES_LOOSENED = "rules-loosened"
    # git hygiene
    NOT_A_GIT_REPO = "not-a-git-repo"
    GITIGNORE_OUTDATED = "gitignore-outdated"
    TRACKED_GENERATED = "tracked-generated"
    UNTRACKED_SOURCE = "untracked-source"
    # bundle
    TOOL_MISSING = "tool-missing"
    TOOL_FAILED = "tool-failed"
    DRC_VIOLATION = "drc-violation"
    DRC_UNCONNECTED = "drc-unconnected"
    DRC_PARITY = "drc-parity"
    OUTPUT_MISSING = "output-missing"
    NO_PART_NUMBER = "no-part-number"
    THROUGH_HOLE_LEFT_OUT = "through-hole-left-out"
    DNP = "dnp"
    CHECK_FAILED = "check-failed"
    # parts: vendoring, catalogue, orderability
    LIB_REGISTERED = "lib-registered"
    PART_KEPT = "part-kept"
    FETCH_FAILED = "fetch-failed"
    CATALOG_ERROR = "catalog-error"
    PART_NOT_FOUND = "part-not-found"
    OUT_OF_STOCK = "out-of-stock"
    LOW_STOCK = "low-stock"
    EXTENDED_PART = "extended-part"
    # order record
    BUNDLE_MISSING = "bundle-missing"
    BUNDLE_STALE = "bundle-stale"
    ORDER_EXISTS = "order-exists"


@dataclass(frozen=True)
class Issue:
    """One finding. ``path`` is relative to the project directory when possible."""

    severity: Severity
    code: Code
    message: str
    path: str | None = None
    ref: str | None = None
    hint: str | None = None

    def to_json(self) -> dict[str, str]:
        """JSON-ready form (only the fields that are set)."""
        out = {
            "severity": self.severity.value,
            "code": self.code.value,
            "message": self.message,
        }
        if self.path is not None:
            out["path"] = self.path
        if self.ref is not None:
            out["ref"] = self.ref
        if self.hint is not None:
            out["hint"] = self.hint
        return out


def error(
    code: Code,
    message: str,
    *,
    path: str | None = None,
    ref: str | None = None,
    hint: str | None = None,
) -> Issue:
    return Issue(Severity.ERROR, code, message, path, ref, hint)


def warning(
    code: Code,
    message: str,
    *,
    path: str | None = None,
    ref: str | None = None,
    hint: str | None = None,
) -> Issue:
    return Issue(Severity.WARNING, code, message, path, ref, hint)


def info(
    code: Code,
    message: str,
    *,
    path: str | None = None,
    ref: str | None = None,
    hint: str | None = None,
) -> Issue:
    return Issue(Severity.INFO, code, message, path, ref, hint)


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)


def worst(issues: Iterable[Issue]) -> Severity | None:
    """The most severe level among ``issues``, or ``None`` if there are none."""
    levels = [i.severity for i in issues]
    return max(levels, key=lambda s: s.rank) if levels else None


def sort_issues(issues: Iterable[Issue]) -> list[Issue]:
    """Stable, readable order: errors first, then by code, path and reference."""
    return sorted(
        issues,
        key=lambda i: (-i.severity.rank, i.code.value, i.path or "", i.ref or ""),
    )
