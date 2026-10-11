"""What the vendored library holds, where each part is used, and removing parts.

Usage comes from the schematic (symbols carrying the part number, or placed
from one of the part's symbols) and the board (footprints from one of the
part's footprints). Footprints and models can be shared by several parts, so
removing a part deletes only what no other locked part still lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kifab.core.config import CONFIG_NAME, ConfigError
from kifab.core.lockfile import LockedPart, Lockfile, load_lockfile
from kifab.core.project import Project
from kifab.fabs.base import Fab
from kifab.kicad.board import read_board
from kifab.kicad.libid import LibId
from kifab.kicad.schematic import read_schematic, sorted_refs
from kifab.kicad.symbol_lib import SymbolLibrary, read_symbol_library

FOOTPRINT_SUFFIX = ".kicad_mod"


class RemoveError(Exception):
    """The part cannot be removed (not vendored, or still in use)."""


@dataclass(frozen=True)
class LibraryPart:
    """One vendored part and where the project uses it."""

    part: LockedPart
    footprints: tuple[str, ...]  # footprint names in the fab library
    models: tuple[str, ...]  # project-relative paths
    schematic_refs: tuple[str, ...] = ()
    board_refs: tuple[str, ...] = ()  # board footprints from the part's footprints
    description: str = ""  # from the part's vendored symbol

    @property
    def id(self) -> str:
        return self.part.id

    @property
    def used(self) -> bool:
        return bool(self.schematic_refs or self.board_refs)

    @property
    def usages(self) -> int:
        """Number of references using the part (schematic and board together)."""
        return len(set(self.schematic_refs) | set(self.board_refs))


@dataclass(frozen=True)
class MissingPart:
    """A part number the schematic uses that is not vendored."""

    id: str
    refs: tuple[str, ...]


@dataclass(frozen=True)
class LibraryContents:
    parts: tuple[LibraryPart, ...]
    not_vendored: tuple[MissingPart, ...] = ()
    without_number: tuple[str, ...] = ()  # schematic refs without a part number

    def get(self, part_id: str) -> LibraryPart | None:
        wanted = part_id.strip().upper()
        return next((p for p in self.parts if p.id == wanted), None)


def _footprint_names(part: LockedPart) -> tuple[str, ...]:
    return tuple(
        sorted(Path(f).stem for f in part.files if f.endswith(FOOTPRINT_SUFFIX))
    )


def _fab(project: Project) -> Fab:
    fab = project.fab()
    if fab is None:
        raise ConfigError(f"no {CONFIG_NAME}: kifab does not know the fab")
    return fab


def library_contents(project: Project) -> LibraryContents:
    """The parts in ``kifab.lock`` with their usage in the schematic and board."""
    fab = project.fab()
    nickname = fab.library_name if fab else ""
    lock = load_lockfile(project.lock_path) or Lockfile()
    sch = read_schematic(project.kicad.sch, project.name)
    by_number: dict[str, set[str]] = {}
    by_symbol: dict[str, set[str]] = {}
    without: list[str] = []
    for placed in sch.parts():
        if placed.lcsc:
            by_number.setdefault(placed.lcsc, set()).add(placed.reference)
        elif placed.on_board:
            without.append(placed.reference)
        if placed.lib_id.nickname == nickname:
            by_symbol.setdefault(placed.lib_id.name, set()).add(placed.reference)
    by_footprint: dict[str, set[str]] = {}
    if project.kicad.pcb.is_file():
        for fp in read_board(project.kicad.pcb).footprints:
            if fp.is_virtual or fp.lib_id.nickname != nickname:
                continue
            by_footprint.setdefault(fp.lib_id.name, set()).add(fp.reference)
    symbols: dict[str, str] = {}
    if fab is not None:
        sym_path = project.lib_dir / nickname / f"{nickname}.kicad_sym"
        try:
            library = read_symbol_library(sym_path) if sym_path.is_file() else None
        except (OSError, ValueError):
            library = None  # reported by check
        if library is not None:
            symbols = {s.name: s.description for s in library.symbols}
    parts = []
    for locked in sorted(lock.parts, key=lambda p: (p.fab, p.id)):
        footprints = _footprint_names(locked)
        sch_refs = set(by_number.get(locked.id, set()))
        for name in locked.symbols:
            sch_refs |= by_symbol.get(name, set())
        board_refs: set[str] = set()
        for name in footprints:
            board_refs |= by_footprint.get(name, set())
        # a shared footprint counts for this part only where the schematic
        # does not say the reference is another part
        other = {
            ref
            for number, refs in by_number.items()
            if number != locked.id
            for ref in refs
        }
        board_refs -= other
        parts.append(
            LibraryPart(
                part=locked,
                footprints=footprints,
                models=tuple(
                    sorted(f for f in locked.files if not f.endswith(FOOTPRINT_SUFFIX))
                ),
                schematic_refs=tuple(sorted_refs(sch_refs)),
                board_refs=tuple(sorted_refs(board_refs)),
                description=next(
                    (symbols[n] for n in locked.symbols if symbols.get(n)), ""
                ),
            )
        )
    locked_ids = {p.id for p in lock.parts}
    missing = tuple(
        MissingPart(number, tuple(sorted_refs(refs)))
        for number, refs in sorted(by_number.items())
        if number not in locked_ids
    )
    return LibraryContents(tuple(parts), missing, tuple(sorted_refs(without)))


@dataclass
class RemoveResult:
    part_id: str
    deleted: list[str] = field(default_factory=list)  # project-relative paths
    symbols: list[str] = field(default_factory=list)  # removed, lib ids
    kept: list[str] = field(default_factory=list)  # shared with other parts
    used_by: list[str] = field(default_factory=list)  # refs (forced removal)


def remove_part(project: Project, part_id: str, *, force: bool = False) -> RemoveResult:
    """Remove a vendored part: its lock entry, and files and symbols it alone uses.

    Refuses (:class:`RemoveError`) while the schematic or board uses the part,
    unless ``force``. Never edits the schematic or the board.
    """
    fab = _fab(project)
    wanted = part_id.strip().upper()
    lock = load_lockfile(project.lock_path) or Lockfile()
    part = next((p for p in lock.parts if (p.fab, p.id) == (fab.name, wanted)), None)
    if part is None:
        raise RemoveError(f"{wanted} is not vendored in this project")
    entry = library_contents(project).get(wanted)
    refs = sorted_refs(
        set(entry.schematic_refs) | set(entry.board_refs) if entry else set()
    )
    if refs and not force:
        raise RemoveError(
            f"{wanted} is used by {', '.join(refs)}; remove it from the schematic "
            "and board first (or force the removal)"
        )
    result = RemoveResult(part_id=wanted, used_by=refs)
    others = [p for p in lock.parts if p is not part]
    other_files = {f for p in others for f in p.files}
    other_symbols = {s for p in others if p.fab == fab.name for s in p.symbols}
    for rel in sorted(part.files):
        if rel in other_files:
            result.kept.append(rel)
            continue
        path = project.directory / rel
        if path.is_file():
            path.unlink()
        result.deleted.append(rel)
    drop = sorted(set(part.symbols) - other_symbols)
    lib_path = project.lib_dir / fab.library_name / f"{fab.library_name}.kicad_sym"
    lib_rel = project.rel(lib_path)
    if drop and lib_path.is_file():
        library = read_symbol_library(lib_path)
        remaining = tuple(s for s in library.symbols if s.name not in drop)
        if len(remaining) != len(library.symbols):
            lib_path.write_text(
                SymbolLibrary(
                    remaining, version=library.version, generator=library.generator
                ).dumps(),
                encoding="utf-8",
            )
    result.symbols = [str(LibId(fab.library_name, name)) for name in drop]
    deleted = set(result.deleted)
    Lockfile(
        parts=tuple(others),
        files=tuple(f for f in lock.files if f.path not in deleted),
        symbols=tuple(
            s for s in lock.symbols if not (s.library == lib_rel and s.name in drop)
        ),
    ).save(project.lock_path)
    return result
