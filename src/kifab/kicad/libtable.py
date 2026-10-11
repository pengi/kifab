"""Project library tables: ``sym-lib-table`` and ``fp-lib-table``."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path, PurePosixPath

from kifab.kicad import sexpr

PROJECT_VAR = "${KIPRJMOD}"
TABLE_VERSION = "7"


class TableKind(Enum):
    SYMBOL = "sym"
    FOOTPRINT = "fp"

    @property
    def file_name(self) -> str:
        return f"{self.value}-lib-table"

    @property
    def root(self) -> str:
        return f"{self.value}_lib_table"


@dataclass(frozen=True)
class LibTableEntry:
    name: str
    uri: str
    type: str = "KiCad"
    options: str = ""
    descr: str = ""
    disabled: bool = False

    @property
    def project_relative(self) -> PurePosixPath | None:
        """The path below the project for ``${KIPRJMOD}/...`` URIs, else ``None``."""
        prefix = PROJECT_VAR + "/"
        if not self.uri.startswith(prefix):
            return None
        rel = PurePosixPath(self.uri[len(prefix) :])
        if rel.is_absolute() or ".." in rel.parts:
            return None
        return rel

    def resolve(self, project_dir: Path) -> Path | None:
        """Where the library lives, for project-relative entries only."""
        rel = self.project_relative
        return project_dir / rel if rel is not None else None


@dataclass(frozen=True)
class LibTable:
    kind: TableKind
    entries: tuple[LibTableEntry, ...] = ()
    version: str = TABLE_VERSION

    def get(self, name: str) -> LibTableEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def names(self) -> set[str]:
        return {e.name for e in self.entries if not e.disabled}

    def with_entry(self, entry: LibTableEntry) -> LibTable:
        """This table with ``entry`` added, or replacing the entry of the same name."""
        if self.get(entry.name) is not None:
            entries = tuple(entry if e.name == entry.name else e for e in self.entries)
        else:
            entries = (*self.entries, entry)
        return replace(self, entries=entries)

    def dumps(self) -> str:
        """KiCad's own layout: one ``(lib ...)`` per line, tab-indented."""
        q = sexpr.quote
        lines = [f"({self.kind.root}", f"\t(version {self.version})"]
        for e in self.entries:
            disabled = "(disabled)" if e.disabled else ""
            lines.append(
                f"\t(lib (name {q(e.name)})(type {q(e.type)})(uri {q(e.uri)})"
                f"(options {q(e.options)})(descr {q(e.descr)}){disabled})"
            )
        lines.append(")")
        return "\n".join(lines) + "\n"


def parse_lib_table(text: str, kind: TableKind) -> LibTable:
    tree = sexpr.parse(text)
    if sexpr.head(tree) != kind.root:
        raise sexpr.SexprError(f"not a {kind.file_name}")
    entries = []
    for lib in sexpr.children(tree, "lib"):
        entries.append(
            LibTableEntry(
                name=sexpr.atom(sexpr.first(lib, "name")) or "",
                uri=sexpr.atom(sexpr.first(lib, "uri")) or "",
                type=sexpr.atom(sexpr.first(lib, "type")) or "KiCad",
                options=sexpr.atom(sexpr.first(lib, "options")) or "",
                descr=sexpr.atom(sexpr.first(lib, "descr")) or "",
                disabled=sexpr.first(lib, "disabled") is not None,
            )
        )
    version = sexpr.atom(sexpr.first(tree, "version")) or TABLE_VERSION
    return LibTable(kind, tuple(entries), version)


def read_lib_table(project_dir: Path, kind: TableKind) -> LibTable | None:
    """The project's table of ``kind``, or ``None`` if the file does not exist."""
    path = Path(project_dir) / kind.file_name
    if not path.is_file():
        return None
    return parse_lib_table(path.read_text(encoding="utf-8"), kind)


def project_uri(project_dir: Path, target: Path) -> str:
    """``${KIPRJMOD}/...`` for ``target`` inside ``project_dir``."""
    rel = Path(target).resolve().relative_to(Path(project_dir).resolve())
    return f"{PROJECT_VAR}/{rel.as_posix()}"
