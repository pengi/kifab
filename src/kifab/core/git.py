"""The few git operations kifab needs, behind a small interface."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class GitError(Exception):
    pass


class Git(Protocol):
    def toplevel(self, path: Path) -> Path | None:
        """The repository root containing ``path``, or ``None`` outside a repo."""

    def init(self, path: Path) -> None: ...

    def tracked(self, path: Path) -> set[str]:
        """Tracked files below ``path``, relative to ``path`` (posix)."""

    def untracked(self, path: Path) -> set[str]:
        """Untracked, not ignored files below ``path``, relative to ``path``."""

    def add(self, path: Path, files: Sequence[Path]) -> None: ...

    def describe(self, path: Path) -> str | None:
        """``git describe --tags --always --dirty``, or ``None``."""

    def head(self, path: Path) -> str | None:
        """The HEAD commit hash, or ``None`` (no repo or no commit yet)."""

    def head_time(self, path: Path) -> datetime | None:
        """The HEAD commit time (UTC)."""

    def tags_at_head(self, path: Path) -> list[str]: ...

    def tag(self, path: Path, name: str, message: str) -> None:
        """Create an annotated tag at HEAD."""


class GitCli:
    """:class:`Git` implemented with the ``git`` program."""

    def _run(self, path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError("git not found in PATH") from exc

    def _out(self, path: Path, *args: str) -> str | None:
        proc = self._run(path, *args)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def toplevel(self, path: Path) -> Path | None:
        out = self._out(path, "rev-parse", "--show-toplevel")
        return Path(out).resolve() if out else None

    def init(self, path: Path) -> None:
        proc = self._run(path, "init", "-q")
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or "git init failed")

    def _list(self, path: Path, *args: str) -> set[str]:
        proc = self._run(path, "ls-files", "-z", *args, "--", ".")
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or "git ls-files failed")
        return {p for p in proc.stdout.split("\0") if p}

    def tracked(self, path: Path) -> set[str]:
        return self._list(path)

    def untracked(self, path: Path) -> set[str]:
        return self._list(path, "--others", "--exclude-standard")

    def add(self, path: Path, files: Sequence[Path]) -> None:
        proc = self._run(path, "add", "--", *(str(f) for f in files))
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or "git add failed")

    def describe(self, path: Path) -> str | None:
        return self._out(path, "describe", "--tags", "--always", "--dirty")

    def head(self, path: Path) -> str | None:
        return self._out(path, "rev-parse", "--verify", "-q", "HEAD")

    def head_time(self, path: Path) -> datetime | None:
        out = self._out(path, "log", "-1", "--format=%ct")
        if not out:
            return None
        return datetime.fromtimestamp(int(out), tz=timezone.utc)

    def tags_at_head(self, path: Path) -> list[str]:
        out = self._out(path, "tag", "--points-at", "HEAD")
        return sorted(out.split()) if out else []

    def tag(self, path: Path, name: str, message: str) -> None:
        proc = self._run(path, "tag", "-a", name, "-m", message)
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or f"git tag {name} failed")
