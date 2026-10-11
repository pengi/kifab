"""Vendor parts into the project library (``lib/<name>/``) and keep the lock.

The files come from the project's library source (EasyEDA via easyeda2kicad
today); the library's nickname is the fab's by default (``jlcpcb``) or
``[parts] library`` in ``kifab.toml``.

Library files stay the source's output, deterministic and sorted. Post-processing
is limited to known problems:

* easyeda2kicad marks some all-SMD footprints ``(attr through_hole)`` (seen on
  C0805 and a 3.2x1.5 mm crystal): KiCad's ``--smd-only`` position export would
  drop them and DRC reports a library mismatch. Fixed when no pad is drilled.
* 3D model paths are rewritten to ``${KIPRJMOD}/lib/<name>/<name>.3dshapes/...``
  (the tool writes absolute or cwd-relative paths).
* Pin names that only repeat the pin number ("1", "2" on a resistor) are
  dropped; with no real names left, pin names are hidden, and for two-pin
  parts the pin numbers too (as KiCad's own ``Device:R``).
* A symbol without a description gets the catalogue's description of the part,
  so KiCad's symbol chooser and the placed symbol show it; likewise ``MPN``
  and ``Manufacturer`` fields, which fabs buying from distributors order by.

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
from kifab.core.partref import (
    MANUFACTURER_FIELD,
    MPN_FIELD,
    PartRef,
    field_specs,
    part_ref,
)
from kifab.core.parts import CatalogError, PartHit
from kifab.core.sourcing import Sourcing
from kifab.libraries.base import (
    CachingPartSource,
    FetchedPart,
    FetchError,
    PartSource,
)
from kifab.suppliers.base import Catalog
from kifab.kicad.libtable import (
    LibTable,
    LibTableEntry,
    TableKind,
    project_uri,
    read_lib_table,
)
from kifab.kicad.libid import LibId
from kifab.kicad.schematic import read_schematic, sorted_refs
from kifab.kicad.symbol_lib import (
    SYMBOL_LIB_VERSION,
    LibrarySymbol,
    SymbolLibrary,
    parse_symbol_library,
    read_symbol_library,
)

#: Fields kifab writes into vendored symbols (besides the description).
PART_FIELDS = (MPN_FIELD, MANUFACTURER_FIELD)
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
    def of(cls, project: Project) -> LibraryPaths:
        name = project.library_name
        if name is None:
            raise ConfigError(f"no {CONFIG_NAME}: kifab does not know the library")
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
        if old is not None:
            # kifab added these fields: compare (and refresh) with them kept
            if old.description and not sym.description:
                sym = sym.with_description(old.description, existing.version)
            for name in PART_FIELDS:
                if old.field_value(name) and not sym.field_value(name):
                    sym = sym.with_property(
                        name, old.field_value(name), existing.version
                    )
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


def _annotate(
    paths: LibraryPaths,
    names: list[str],
    hit: PartHit,
    result: VendorResult,
    rel: str,
) -> None:
    """Fill in what the named symbols lack of the catalogue's facts.

    The description (KiCad's symbol chooser shows it), and the MPN and
    manufacturer: fabs that buy from distributors order by them.
    """
    if not paths.symbols.is_file():
        return
    library = read_symbol_library(paths.symbols)
    changed = False
    symbols = []
    for sym in library.symbols:
        if sym.name in names:
            if hit.description and not sym.description:
                sym = sym.with_description(hit.description, library.version)
                changed = True
            for name, value in (
                (MPN_FIELD, hit.mpn),
                (MANUFACTURER_FIELD, hit.manufacturer),
            ):
                if value and not sym.field_value(name):
                    sym = sym.with_property(name, value, library.version)
                    changed = True
        symbols.append(sym)
    if not changed:
        return
    library = SymbolLibrary(tuple(symbols), library.version, library.generator)
    paths.symbols.write_text(library.dumps(), encoding="utf-8")
    if rel not in result.written:
        result.written.append(rel)


def _catalog_hit(
    catalog: Catalog, part_id: str, result: VendorResult
) -> PartHit | None:
    try:
        return catalog.lookup(part_id)
    except CatalogError as exc:
        result.issues.append(
            info(Code.CATALOG_ERROR, f"no description: {exc}", ref=part_id)
        )
        return None


def relink_footprints(
    library: SymbolLibrary, nickname: str, footprints: Iterable[str]
) -> SymbolLibrary:
    """Point the symbols' Footprint fields at ``nickname`` for fetched footprints.

    The library source names its own library (easyeda2kicad: ``jlcpcb:R0603``);
    the project's may be called differently.
    """
    names = set(footprints)
    symbols = []
    for sym in library.symbols:
        lib_id = LibId.parse(sym.field_value("Footprint"))
        if lib_id.name in names and lib_id.nickname != nickname:
            sym = sym.with_field_value("Footprint", f"{nickname}:{lib_id.name}")
        symbols.append(sym)
    return SymbolLibrary(tuple(symbols), library.version, library.generator)


def tidy_pins(library: SymbolLibrary) -> SymbolLibrary:
    """:meth:`LibrarySymbol.with_tidy_pins` for every symbol of a fetched library."""
    return SymbolLibrary(
        tuple(s.with_tidy_pins(library.version) for s in library.symbols),
        library.version,
        library.generator,
    )


def _sourcing(project: Project) -> Sourcing:
    sourcing = project.sourcing()
    if sourcing is None:
        raise ConfigError(f"no {CONFIG_NAME}: kifab does not know the fab")
    return sourcing


def ensure_library_tables(project: Project) -> list[Issue]:
    """Register the parts library in the project tables if it is missing.

    Library tables are read when KiCad opens the project, so this needs KiCad
    closed (raises :class:`kifab.core.project.KicadOpen`).
    """
    paths = LibraryPaths.of(project)
    title = _sourcing(project).fab.title
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
            descr=f"{title} parts (vendored by kifab)",
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
    library_source: str,
    fetched: FetchedPart,
    paths_rel: list[str],
    symbols: list[str],
) -> None:
    lock = load_lockfile(project.lock_path) or Lockfile()
    lib_rel = project.rel(LibraryPaths.of(project).symbols)
    part = LockedPart(
        id=fetched.part_id,
        library_source=library_source,
        source=fetched.source,
        symbols=tuple(sorted(symbols)),
        files=tuple(sorted(paths_rel)),
    )
    key = (library_source, fetched.part_id)
    parts = [p for p in lock.parts if (p.library_source, p.id) != key]
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
    hit: PartHit | None = None,
    catalog: Catalog | None = None,
    fresh: bool | None = None,
) -> VendorResult:
    """Fetch ``part_id`` and vendor it into ``lib/<name>/``; update ``kifab.lock``.

    What ``hit`` (the selected search result; else ``catalog``'s entry for the
    part) says of the part goes into symbols that lack it: description, MPN
    and manufacturer. Vendoring an already vendored part again fills them in. ``fresh`` (default: ``refresh``) bypasses the part source's cache.
    Raises :class:`FetchError` when the part cannot be fetched and
    :class:`ConfigError` for a project without ``kifab.toml``.
    """
    sourcing = _sourcing(project)
    source = source or sourcing.part_source()
    paths = LibraryPaths.of(project)
    result = VendorResult(part_id=part_id.strip().upper())
    if (refresh if fresh is None else fresh) and isinstance(source, CachingPartSource):
        source.forget(result.part_id)
    result.issues += ensure_library_tables(project)
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
            lib = relink_footprints(
                tidy_pins(
                    parse_symbol_library(fetched.symbol_library.read_text("utf-8"))
                ),
                paths.nickname,
                (fp.stem for fp in fetched.footprints),
            )
            _merge_symbols(paths, lib, refresh, result, project.rel(paths.symbols))
            symbols = sorted(lib.names())
            result.symbols = [f"{paths.nickname}:{n}" for n in symbols]
            if hit is None and catalog is not None:
                hit = _catalog_hit(catalog, result.part_id, result)
            if hit is not None:
                _annotate(paths, symbols, hit, result, project.rel(paths.symbols))
        _lock_part(project, sourcing.library.name, fetched, files_rel, symbols)
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


def _resolve_mpns(
    project: Project,
    sourcing: Sourcing,
    catalog: Catalog,
    result: SyncResult,
    reporter: Reporter,
) -> tuple[dict[str, list[str]], set[str]]:
    """``{library part number: references}`` for the parts known by MPN only,
    and the references looked up (found or reported as not found)."""
    out: dict[str, list[str]] = {}
    looked_up: set[str] = set()
    for ref, refs in schematic_mpn_parts(project, sourcing.library.sku_kind).items():
        try:
            hit = catalog.lookup_mpn(ref.mpn, ref.manufacturer)
        except CatalogError as exc:
            issue = warning(Code.CATALOG_ERROR, str(exc), ref=",".join(refs))
            result.issues.append(issue)
            reporter.issue(issue)
            break
        looked_up.update(refs)
        if hit is None:
            issue = warning(
                Code.FETCH_FAILED,
                f"no {sourcing.library.title} part found for MPN {ref.mpn}",
                ref=",".join(refs),
            )
            result.issues.append(issue)
            reporter.issue(issue)
            continue
        reporter.step(ref.mpn, f"is {hit.id}")
        out.setdefault(hit.id, []).extend(refs)
    return out, looked_up


def schematic_mpn_parts(project: Project, kind: str) -> dict[PartRef, list[str]]:
    """Parts on the board with an MPN but no ``kind`` number: ``{ref: references}``."""
    sch = read_schematic(project.kicad.sch, project.name)
    specs = field_specs()
    found: dict[PartRef, list[str]] = {}
    for part in sch.parts():
        if not part.on_board or part.is_power:
            continue
        ref = part_ref(part.properties, specs)
        if ref.mpn and not ref.sku(kind):
            found.setdefault(ref, []).append(part.reference)
    return {k: sorted_refs(v) for k, v in sorted(found.items(), key=lambda i: i[0].mpn)}


def resolve_part(sourcing: Sourcing, text: str) -> tuple[str, PartHit | None]:
    """The library source's part number for ``text``: itself, or by MPN.

    An MPN is looked up in the catalogue of a supplier that sells by the
    library source's numbers (LCSC numbers for EasyEDA). Raises
    :class:`FetchError` when nothing matches or no such supplier is configured.
    """
    own = sourcing.library.parse_id(text)
    if own is not None:
        return own, None
    catalog = sourcing.library_catalog()
    if catalog is None:
        raise FetchError(
            f"{text!r} is not a {sourcing.library.title} part number, and no "
            f"configured supplier sells by {sourcing.library.title}'s numbers"
        )
    try:
        hit = catalog.lookup_mpn(text)
    except CatalogError as exc:
        raise FetchError(f"{text}: {exc}") from exc
    if hit is None:
        raise FetchError(f"no part with MPN {text!r} found")
    return hit.id, hit


def sync_parts(
    project: Project,
    source: PartSource | None = None,
    *,
    refresh: bool = False,
    only: Iterable[str] = (),
    delay: float = 0.0,
    reporter: Reporter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    catalog: Catalog | None = None,
) -> SyncResult:
    """Vendor every part the schematic references (port of populate-jlc-library).

    Parts already in ``kifab.lock`` are skipped unless ``refresh``. With a
    ``catalog`` (of a supplier selling by the library source's numbers), parts
    that only have an MPN are looked up there by it. Never edits the
    schematic or the board.
    """
    reporter = reporter or NullReporter()
    result = SyncResult()
    sourcing = _sourcing(project)
    library_source = sourcing.library.name
    numbers, missing = schematic_part_numbers(project)
    if catalog is not None:
        by_mpn, looked_up = _resolve_mpns(project, sourcing, catalog, result, reporter)
        for number, mpn_refs in by_mpn.items():
            numbers[number] = sorted_refs([*numbers.get(number, []), *mpn_refs])
        missing = [r for r in missing if r not in looked_up]
        numbers = dict(sorted(numbers.items()))
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
    locked = {p.id for p in lock.parts if p.library_source == library_source}
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
            vendored = vendor_part(
                project, number, source, refresh=refresh, catalog=catalog
            )
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
