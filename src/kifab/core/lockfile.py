"""``kifab.lock``: every vendored part, the files it brought and their hashes.

The lock lets ``kifab check`` verify offline (in CI) that the vendored library is
exactly what was fetched. Footprints and 3D models can be shared by several
parts (all 0603 resistors use one footprint), and all symbols of a fab live in
one ``.kicad_sym``, so files and symbols are listed once each and parts refer to
them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kifab.core import toml

LOCK_VERSION = 2
#: Version 1 recorded the fab a part came from; the only fab then (JLCPCB)
#: vendored from EasyEDA, which version 2 records as the library source.
_V1_LIBRARY_SOURCE = {"jlcpcb": "easyeda"}


class LockError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class LockedPart:
    id: str  # the library source's part number, e.g. "C123456"
    library_source: str  # e.g. "easyeda"
    source: str  # the tool it was fetched with, e.g. "easyeda2kicad 1.0.1"
    symbols: tuple[str, ...] = ()
    files: tuple[str, ...] = ()  # project-relative posix paths


@dataclass(frozen=True)
class LockedFile:
    path: str  # project-relative posix path
    sha256: str


@dataclass(frozen=True)
class LockedSymbol:
    library: str  # project-relative path of the .kicad_sym
    name: str
    sha256: str  # LibrarySymbol.digest()


@dataclass(frozen=True)
class Lockfile:
    parts: tuple[LockedPart, ...] = ()
    files: tuple[LockedFile, ...] = ()
    symbols: tuple[LockedSymbol, ...] = ()

    def dumps(self) -> str:
        out = [
            "# kifab.lock: vendored parts and their file hashes. Written by kifab; "
            "do not edit.\n",
            toml.table(None, {"version": LOCK_VERSION}),
        ]
        for p in sorted(self.parts, key=lambda p: (p.library_source, p.id)):
            out.append(
                toml.array_table(
                    "part",
                    {
                        "id": p.id,
                        "library_source": p.library_source,
                        "source": p.source,
                        "symbols": sorted(p.symbols),
                        "files": sorted(p.files),
                    },
                )
            )
        for f in sorted(self.files, key=lambda f: f.path):
            out.append(toml.array_table("file", {"path": f.path, "sha256": f.sha256}))
        for s in sorted(self.symbols, key=lambda s: (s.library, s.name)):
            out.append(
                toml.array_table(
                    "symbol", {"library": s.library, "name": s.name, "sha256": s.sha256}
                )
            )
        return "\n".join(out)

    def save(self, path: Path) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8")


def _str(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise LockError(f"kifab.lock: {where} needs a string {key!r}")
    return value


def _strs(entry: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LockError(f"kifab.lock: {where} {key!r} must be a list of strings")
    return tuple(value)


def _tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise LockError(f"kifab.lock: [[{key}]] entries must be tables")
    return value


def parse_lockfile(text: str) -> Lockfile:
    try:
        data = toml.loads(text)
    except toml.TOMLDecodeError as exc:
        raise LockError(f"kifab.lock: {exc}") from exc
    version = data.get("version")
    if version not in (1, LOCK_VERSION):
        raise LockError(f"kifab.lock: unsupported version {version!r}")

    def library_source(entry: dict[str, Any]) -> str:
        if version == 1:
            fab = _str(entry, "fab", "part")
            return _V1_LIBRARY_SOURCE.get(fab, fab)
        return _str(entry, "library_source", "part")

    return Lockfile(
        parts=tuple(
            LockedPart(
                id=_str(e, "id", "part"),
                library_source=library_source(e),
                source=_str(e, "source", "part"),
                symbols=_strs(e, "symbols", "part"),
                files=_strs(e, "files", "part"),
            )
            for e in _tables(data, "part")
        ),
        files=tuple(
            LockedFile(_str(e, "path", "file"), _str(e, "sha256", "file"))
            for e in _tables(data, "file")
        ),
        symbols=tuple(
            LockedSymbol(
                _str(e, "library", "symbol"),
                _str(e, "name", "symbol"),
                _str(e, "sha256", "symbol"),
            )
            for e in _tables(data, "symbol")
        ),
    )


def load_lockfile(path: Path) -> Lockfile | None:
    if not Path(path).is_file():
        return None
    return parse_lockfile(Path(path).read_text(encoding="utf-8"))
