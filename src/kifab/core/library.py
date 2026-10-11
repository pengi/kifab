"""Vendor fab parts into the project library (``lib/<fab>/``) and keep the lock.

Library files stay the fab's output, deterministic and sorted. Post-processing
is limited to known problems:

* easyeda2kicad marks some all-SMD footprints ``(attr through_hole)`` (seen on
  C0805 and a 3.2x1.5 mm crystal): KiCad's ``--smd-only`` position export would
  drop them and DRC reports a library mismatch. Fixed when no pad is drilled.
* 3D model paths are rewritten to ``${KIPRJMOD}/lib/<fab>/<fab>.3dshapes/...``
  (the tool writes absolute or cwd-relative paths).

Several parts can share a footprint name (all 0603 resistors -> ``R0603``) while
EasyEDA's copies differ in details such as the description. The first vendored
copy is kept; ``refresh`` replaces it.
"""

from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from kifab.core.config import CONFIG_NAME, ConfigError
from kifab.core.issues import Code, Issue, info, warning
from kifab.core.lockfile import (
    LockedFile,
    LockedPart,
    LockedSymbol,
    Lockfile,
    load_lockfile,
    sha256_file,
)
from kifab.core.project import Project
from kifab.core.reporter import NullReporter, Reporter
from kifab.fabs.base import Fab, FetchedPart, FetchError, PartSource
from kifab.kicad.libtable import (
    LibTable,
    LibTableEntry,
    TableKind,
    project_uri,
    read_lib_table,
)
from kifab.kicad.schematic import read_schematic, sorted_refs
from kifab.kicad.symbol_lib import (
    SYMBOL_LIB_VERSION,
    LibrarySymbol,
    SymbolLibrary,
    parse_symbol_library,
    read_symbol_library,
)

_MODEL = re.compile(r'(\(model\s+)"([^"]*)"')


def fix_attributes(text: str) -> str:
    """``(attr through_hole)`` -> ``(attr smd)`` for footprints without drilled pads."""
    if "(attr through_hole)" not in text:
        return text
    if re.search(r"\(pad\s+\S+\s+(thru_hole|np_thru_hole)\b", text) or "(drill" in text:
        return text
    return text.replace("(attr through_hole)", "(attr smd)", 1)


def rewrite_models(text: str, prefix: str) -> str:
    """Point every ``(model "...")`` at ``prefix + <file name>``."""

    def sub(m: re.Match[str]) -> str:
        name = re.split(r"[\\/]", m.group(2))[-1]
        return f'{m.group(1)}"{prefix}{name}"'

    return _MODEL.sub(sub, text)


@dataclass(frozen=True)
class LibraryPaths:
    nickname: str
    root: Path
    symbols: Path
    footprints: Path
    models: Path

    @classmethod
    def of(cls, project: Project, fab: Fab) -> LibraryPaths:
        name = fab.library_name
        root = project.lib_dir / name
        return cls(
            name,
            root,
            root / f"{name}.kicad_sym",
            root / f"{name}.pretty",
            root / f"{name}.3dshapes",
        )


@dataclass
class VendorResult:
    part_id: str
    source: str = ""
    symbols: list[str] = field(default_factory=list)  # lib ids, e.g. jlcpcb:X
    footprints: list[str] = field(default_factory=list)  # lib ids, e.g. jlcpcb:R0603
    written: list[str] = field(default_factory=list)  # project-relative paths
    kept: list[str] = field(default_factory=list)  # existing, different, kept
    issues: list[Issue] = field(default_factory=list)


def _store(
    path: Path, data: bytes, refresh: bool, result: VendorResult, rel: str
) -> None:
    if path.is_file():
        if path.read_bytes() == data:
            return
        if not refresh:
            result.kept.append(rel)
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    result.written.append(rel)


def _merge_symbols(
    paths: LibraryPaths,
    fetched: SymbolLibrary,
    refresh: bool,
    result: VendorResult,
    rel: str,
) -> None:
    existing = (
        read_symbol_library(paths.symbols)
        if paths.symbols.is_file()
        else SymbolLibrary(symbols=())
    )
    merged = {s.name: s for s in existing.symbols}
    changed = False
    for sym in fetched.symbols:
        old = merged.get(sym.name)
        if old is not None and old.digest() == sym.digest():
            continue
        if old is not None and not refresh:
            result.kept.append(f"{rel}:{sym.name}")
            continue
        merged[sym.name] = sym
        changed = True
    if not changed:
        return
    # Keep the oldest format version present: easyeda2kicad writes 20211014
    # syntax, which a newer version header would claim is something else.
    versions = [fetched.version] + ([existing.version] if existing.symbols else [])
    library = SymbolLibrary(
        symbols=tuple(merged.values()),
        version=min(versions) if versions else SYMBOL_LIB_VERSION,
        generator="kifab",
    )
    paths.symbols.parent.mkdir(parents=True, exist_ok=True)
    paths.symbols.write_text(library.dumps(), encoding="utf-8")
    result.written.append(rel)


def ensure_library_tables(project: Project, fab: Fab) -> list[Issue]:
    """Register the fab library in the project tables if it is missing.

    Library tables are read when KiCad opens the project, so this needs KiCad
    closed (raises :class:`kifab.core.project.KicadOpen`).
    """
    paths = LibraryPaths.of(project, fab)
    issues: list[Issue] = []
    for kind, target in (
        (TableKind.SYMBOL, paths.symbols),
        (TableKind.FOOTPRINT, paths.footprints),
    ):
        table = read_lib_table(project.directory, kind) or LibTable(kind)
        if table.get(paths.nickname) is not None:
            continue
        project.ensure_closed()
        entry = LibTableEntry(
            paths.nickname,
            project_uri(project.directory, target),
            descr=f"{fab.title} parts (vendored by kifab)",
        )
        (project.directory / kind.file_name).write_text(
            table.with_entry(entry).dumps(), encoding="utf-8"
        )
        issues.append(
            info(
                Code.LIB_REGISTERED,
                f"registered library {paths.nickname!r} in {kind.file_name}",
                path=kind.file_name,
            )
        )
    return issues


def _lock_part(
    project: Project,
    fab: Fab,
    fetched: FetchedPart,
    paths_rel: list[str],
    symbols: list[str],
) -> None:
    lock = load_lockfile(project.lock_path) or Lockfile()
    lib_rel = project.rel(LibraryPaths.of(project, fab).symbols)
    part = LockedPart(
        id=fetched.part_id,
        fab=fab.name,
        source=fetched.source,
        symbols=tuple(sorted(symbols)),
        files=tuple(sorted(paths_rel)),
    )
    parts = [p for p in lock.parts if (p.fab, p.id) != (fab.name, fetched.part_id)]
    files = {f.path: f for f in lock.files}
    for rel in paths_rel:
        files[rel] = LockedFile(rel, sha256_file(project.directory / rel))
    sym_entries = {(s.library, s.name): s for s in lock.symbols}
    library = read_symbol_library(project.directory / lib_rel)
    for name in symbols:
        sym = library.get(name)
        if sym is not None:
            sym_entries[(lib_rel, name)] = LockedSymbol(lib_rel, name, sym.digest())
    Lockfile(
        parts=(*parts, part),
        files=tuple(files.values()),
        symbols=tuple(sym_entries.values()),
    ).save(project.lock_path)


def vendor_part(
    project: Project,
    part_id: str,
    source: PartSource | None = None,
    *,
    refresh: bool = False,
) -> VendorResult:
    """Fetch ``part_id`` and vendor it into ``lib/<fab>/``; update ``kifab.lock``.

    Raises :class:`FetchError` when the part cannot be fetched and
    :class:`ConfigError` for a project without ``kifab.toml``.
    """
    fab = project.fab()
    if fab is None:
        raise ConfigError(f"no {CONFIG_NAME}: kifab does not know the fab")
    source = source or fab.part_source()
    paths = LibraryPaths.of(project, fab)
    result = VendorResult(part_id=part_id.strip().upper())
    result.issues += ensure_library_tables(project, fab)
    model_prefix = project_uri(project.directory, paths.models) + "/"
    with tempfile.TemporaryDirectory(prefix="kifab-fetch-") as tmp:
        fetched = source.fetch(result.part_id, Path(tmp))
        result.source = fetched.source
        files_rel: list[str] = []
        for model in fetched.models:
            target = paths.models / model.name
            rel = project.rel(target)
            _store(target, model.read_bytes(), refresh, result, rel)
            files_rel.append(rel)
        for fp in fetched.footprints:
            text = fp.read_text(encoding="utf-8")
            text = rewrite_models(fix_attributes(text), model_prefix)
            target = paths.footprints / fp.name
            rel = project.rel(target)
            _store(target, text.encode("utf-8"), refresh, result, rel)
            files_rel.append(rel)
            result.footprints.append(f"{paths.nickname}:{fp.stem}")
        symbols: list[str] = []
        if fetched.symbol_library is not None:
            lib = parse_symbol_library(fetched.symbol_library.read_text("utf-8"))
            _merge_symbols(paths, lib, refresh, result, project.rel(paths.symbols))
            symbols = sorted(lib.names())
            result.symbols = [f"{paths.nickname}:{n}" for n in symbols]
        _lock_part(project, fab, fetched, files_rel, symbols)
    for kept in result.kept:
        result.issues.append(
            info(
                Code.PART_KEPT,
                f"{kept} already exists with different content; kept the vendored "
                "copy (use --refresh to replace it)",
                ref=result.part_id,
            )
        )
    return result


# ---------------------------------------------------------------- sync


@dataclass
class SyncResult:
    vendored: list[VendorResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already locked
    failed: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def schematic_part_numbers(project: Project) -> tuple[dict[str, list[str]], list[str]]:
    """``{part number: references}`` used in the schematic, and refs without one."""
    sch = read_schematic(project.kicad.sch, project.name)
    numbers: dict[str, list[str]] = {}
    missing: list[str] = []
    for part in sch.parts():
        if part.lcsc:
            numbers.setdefault(part.lcsc, []).append(part.reference)
        elif part.on_board:
            missing.append(part.reference)
    return {k: sorted_refs(v) for k, v in sorted(numbers.items())}, sorted_refs(missing)


def sync_parts(
    project: Project,
    source: PartSource | None = None,
    *,
    refresh: bool = False,
    only: Iterable[str] = (),
    delay: float = 0.0,
    reporter: Reporter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SyncResult:
    """Vendor every part the schematic references (port of populate-jlc-library).

    Parts already in ``kifab.lock`` are skipped unless ``refresh``. Never edits
    the schematic or the board.
    """
    reporter = reporter or NullReporter()
    result = SyncResult()
    fab = project.fab()
    if fab is None:
        raise ConfigError(f"no {CONFIG_NAME}: kifab does not know the fab")
    numbers, missing = schematic_part_numbers(project)
    if missing:
        issue = warning(
            Code.NO_PART_NUMBER,
            "parts without a part number cannot be vendored",
            ref=",".join(missing),
        )
        result.issues.append(issue)
        reporter.issue(issue)
    wanted = {o.strip().upper() for o in only if o.strip()}
    lock = load_lockfile(project.lock_path) or Lockfile()
    locked = {p.id for p in lock.parts if p.fab == fab.name}
    todo = [n for n in numbers if not wanted or n in wanted]
    first = True
    for number in todo:
        refs = ",".join(numbers[number])
        if number in locked and not refresh:
            result.skipped.append(number)
            reporter.step(number, f"already vendored ({refs})")
            continue
        if not first and delay:
            sleep(delay)  # be polite to the service
        first = False
        try:
            vendored = vendor_part(project, number, source, refresh=refresh)
        except FetchError as exc:
            result.failed.append(number)
            issue = warning(Code.FETCH_FAILED, str(exc), ref=refs)
            result.issues.append(issue)
            reporter.issue(issue)
            reporter.step(number, "FAILED")
            continue
        result.vendored.append(vendored)
        for issue in vendored.issues:
            result.issues.append(issue)
            reporter.issue(issue)
        reporter.step(number, ", ".join(vendored.footprints) or "symbol only")
    return result
