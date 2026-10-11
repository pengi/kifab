"""``kifab check``: is the project self-contained, in sync with its rules, clean in git?

Runs offline on any KiCad project (with or without ``kifab.toml``). Every finding
is an :class:`Issue`; nothing is printed or modified.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from kifab.core import gitignore
from kifab.core.config import CONFIG_NAME
from kifab.core.design_rules import check_rules
from kifab.core.git import Git, GitError
from kifab.core.issues import Code, Issue, error, info, warning
from kifab.core.lockfile import LockError, load_lockfile, sha256_file
from kifab.core.project import BACKUP_DIR, LOCK_NAME, Project
from kifab.core.reporter import NullReporter, Reporter
from kifab.kicad import sexpr
from kifab.kicad.board import Board, Footprint, read_board, read_footprint_file
from kifab.kicad.libid import LibId
from kifab.kicad.libtable import PROJECT_VAR, LibTable, TableKind, read_lib_table
from kifab.kicad.project import locked_files
from kifab.kicad.schematic import Schematic, read_schematic, sorted_refs
from kifab.kicad.symbol_lib import SymbolLibrary, read_symbol_library

MODEL_SUFFIXES = (".step", ".stp", ".wrl", ".STEP", ".STP", ".WRL")
#: Files kifab expects in git (patterns on project-relative paths).
SOURCE_PATTERNS = (
    "*.kicad_pro",
    "*.kicad_sch",
    "*.kicad_pcb",
    "*.kicad_dru",
    "sym-lib-table",
    "fp-lib-table",
    CONFIG_NAME,
    LOCK_NAME,
    ".gitignore",
    "*.pretty/*.kicad_mod",
    "*.kicad_sym",
)


@dataclass
class CheckResult:
    issues: list[Issue] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)  # what ran


@dataclass
class _Libraries:
    """Project library tables, resolved; ``None`` entries are outside the project."""

    symbols: dict[str, Path | None] = field(default_factory=dict)
    footprints: dict[str, Path | None] = field(default_factory=dict)
    symbol_cache: dict[Path, SymbolLibrary | None] = field(default_factory=dict)

    def symbol_library(self, path: Path) -> SymbolLibrary | None:
        if path not in self.symbol_cache:
            try:
                self.symbol_cache[path] = read_symbol_library(path)
            except (OSError, sexpr.SexprError):
                self.symbol_cache[path] = None
        return self.symbol_cache[path]


class Checker:
    def __init__(self, project: Project, git: Git | None, reporter: Reporter) -> None:
        self.project = project
        self.git = git
        self.reporter = reporter
        self.issues: list[Issue] = []
        self.libs = _Libraries()
        self._grouped: dict[Issue, list[str]] = {}

    def add(self, issue: Issue) -> None:
        """Record ``issue``; the same finding for several references becomes one."""
        if issue.ref is None:
            self._emit(issue)
            return
        self._grouped.setdefault(replace(issue, ref=None), []).append(issue.ref)

    def _emit(self, issue: Issue) -> None:
        self.issues.append(issue)
        self.reporter.issue(issue)

    def flush(self) -> None:
        for issue, refs in self._grouped.items():
            self._emit(replace(issue, ref=",".join(sorted_refs(refs))))
        self._grouped.clear()

    def rel(self, path: Path) -> str:
        return self.project.rel(path)

    # ------------------------------------------------------------ libraries

    def lib_tables(self) -> None:
        directory = self.project.directory
        for kind, target in (
            (TableKind.SYMBOL, self.libs.symbols),
            (TableKind.FOOTPRINT, self.libs.footprints),
        ):
            try:
                table = read_lib_table(directory, kind)
            except sexpr.SexprError as exc:
                self.add(error(Code.PARSE_ERROR, str(exc), path=kind.file_name))
                continue
            if table is None:
                self.add(
                    error(
                        Code.LIB_TABLE_MISSING,
                        f"no project {kind.file_name}: every library must be "
                        "registered in the project, not globally",
                        path=kind.file_name,
                    )
                )
                continue
            self._table_entries(table, target)

    def _table_entries(self, table: LibTable, target: dict[str, Path | None]) -> None:
        for entry in table.entries:
            if entry.disabled:
                continue
            where = entry.resolve(self.project.directory)
            target[entry.name] = where
            if where is None:
                self.add(
                    error(
                        Code.LIB_OUTSIDE_PROJECT,
                        f"library {entry.name!r} points outside the project: "
                        f"{entry.uri}",
                        path=table.kind.file_name,
                        hint=f"vendor it below the project and use {PROJECT_VAR}/...",
                    )
                )
            elif not where.exists():
                self.add(
                    error(
                        Code.LIB_MISSING,
                        f"library {entry.name!r} does not exist: {entry.uri}",
                        path=table.kind.file_name,
                    )
                )

    # ------------------------------------------------------------ schematic

    def schematic(self, sch: Schematic) -> None:
        for name in sch.missing_sheets:
            self.add(error(Code.PARSE_ERROR, f"sub-sheet {name} not found"))
        seen: set[tuple[str, LibId]] = set()
        for sym in sch.symbols:
            key = (sym.reference, sym.lib_id)
            if key in seen:
                continue
            seen.add(key)
            self._symbol_ref(
                sym.lib_id, sym.reference, self.rel(sym.sheet), sym.is_power
            )
            fp = sym.footprint_id
            if fp is not None and not sym.is_virtual:
                self._footprint_ref(fp, sym.reference, self.rel(sym.sheet))

    def _symbol_ref(self, lib_id: LibId, ref: str, where: str, power: bool) -> None:
        if lib_id.nickname not in self.libs.symbols:
            hint = (
                "use the power symbols of the project's 'kifab' library"
                if power
                else "vendor the part into a project library"
            )
            self.add(
                error(
                    Code.SYMBOL_LIB_UNKNOWN,
                    f"symbol {lib_id} comes from library {lib_id.nickname!r}, "
                    "which is not a project library",
                    path=where,
                    ref=ref,
                    hint=hint,
                )
            )
            return
        path = self.libs.symbols[lib_id.nickname]
        if path is None or not path.is_file():
            return  # reported with the table
        lib = self.libs.symbol_library(path)
        if lib is not None and lib_id.name not in lib.names():
            self.add(
                error(
                    Code.SYMBOL_NOT_IN_LIB,
                    f"symbol {lib_id} is not in {self.rel(path)}",
                    path=where,
                    ref=ref,
                )
            )

    def _footprint_ref(self, lib_id: LibId, ref: str, where: str) -> None:
        if lib_id.nickname not in self.libs.footprints:
            self.add(
                error(
                    Code.FOOTPRINT_LIB_UNKNOWN,
                    f"footprint {lib_id} comes from library {lib_id.nickname!r}, "
                    "which is not a project library",
                    path=where,
                    ref=ref,
                )
            )
            return
        pretty = self.libs.footprints[lib_id.nickname]
        if pretty is None or not pretty.is_dir():
            return
        if not (pretty / f"{lib_id.name}.kicad_mod").is_file():
            self.add(
                error(
                    Code.FOOTPRINT_NOT_IN_LIB,
                    f"footprint {lib_id} is not in {self.rel(pretty)}",
                    path=where,
                    ref=ref,
                )
            )

    # ------------------------------------------------------------ board

    def board(self, board: Board) -> None:
        pcb = self.rel(board.path)
        for fp in board.footprints:
            ref = fp.reference or None
            if fp.lib_id.nickname:
                self._footprint_ref(fp.lib_id, fp.reference, pcb)
            self._models(fp.footprint, pcb, ref)

    def _models(self, fp: Footprint, where: str, ref: str | None) -> None:
        for model in fp.models:
            rel = _project_path(model)
            if rel is None:
                self.add(
                    error(
                        Code.MODEL_OUTSIDE_PROJECT,
                        f"3D model outside the project: {model}",
                        path=where,
                        ref=ref,
                        hint=f"vendor the model and reference it as {PROJECT_VAR}/...",
                    )
                )
            elif not (self.project.directory / rel).is_file():
                self.add(
                    error(
                        Code.MODEL_MISSING,
                        f"3D model does not exist: {model}",
                        path=where,
                        ref=ref,
                    )
                )

    def footprint_libraries(self) -> None:
        """3D paths inside the project's own footprint libraries."""
        for pretty in sorted({p for p in self.libs.footprints.values() if p}):
            if not pretty.is_dir():
                continue
            for mod in sorted(pretty.glob("*.kicad_mod")):
                try:
                    fp = read_footprint_file(mod)
                except (OSError, sexpr.SexprError) as exc:
                    self.add(error(Code.PARSE_ERROR, str(exc), path=self.rel(mod)))
                    continue
                self._models(fp, self.rel(mod), None)

    def parity(self, sch: Schematic, board: Board) -> None:
        on_board = board.by_reference()
        parts = {p.reference: p for p in sch.parts() if p.on_board}
        pcb = self.rel(board.path)
        for ref, part in parts.items():
            fp = on_board.get(ref)
            if fp is None:
                self.add(
                    warning(
                        Code.NOT_ON_BOARD,
                        f"{ref} is in the schematic but not on the board",
                        path=pcb,
                        ref=ref,
                        hint="Update PCB from Schematic (F8) in KiCad",
                    )
                )
            elif part.footprint and str(fp.lib_id) != part.footprint:
                self.add(
                    warning(
                        Code.FOOTPRINT_MISMATCH,
                        f"{ref}: schematic footprint {part.footprint}, board has "
                        f"{fp.lib_id}",
                        path=pcb,
                        ref=ref,
                        hint="Update PCB from Schematic (F8) in KiCad",
                    )
                )
        for ref in sorted(set(on_board) - set(parts)):
            self.add(
                warning(
                    Code.NOT_IN_SCHEMATIC,
                    f"{ref} is on the board but not in the schematic",
                    path=pcb,
                    ref=ref,
                )
            )

    # ------------------------------------------------------------ lock

    def lock(self) -> None:
        project = self.project
        fab_dir = project.fab_library_dir()
        if fab_dir is None:
            return
        try:
            lock = load_lockfile(project.lock_path)
        except LockError as exc:
            self.add(error(Code.PARSE_ERROR, str(exc), path=LOCK_NAME))
            return
        if lock is None:
            self.add(
                error(
                    Code.LOCK_FILE_MISSING,
                    f"no {LOCK_NAME}: the vendored library cannot be verified",
                    path=LOCK_NAME,
                )
            )
            return
        directory = project.directory
        for locked in lock.files:
            path = directory / locked.path
            if not path.is_file():
                self.add(
                    error(
                        Code.LOCK_HASH_MISMATCH,
                        f"vendored file is missing: {locked.path}",
                        path=LOCK_NAME,
                    )
                )
            elif sha256_file(path) != locked.sha256:
                self.add(
                    error(
                        Code.LOCK_HASH_MISMATCH,
                        f"vendored file was changed: {locked.path}",
                        path=locked.path,
                        hint="re-vendor the part instead of editing fab files",
                    )
                )
        locked_paths = {f.path for f in lock.files}
        for path in sorted(fab_dir.rglob("*")):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            rel = self.rel(path)
            if rel.endswith(".kicad_sym") or rel in locked_paths:
                continue
            self.add(
                warning(
                    Code.UNLOCKED_FILE,
                    f"{rel} is not in {LOCK_NAME}",
                    path=rel,
                    hint="add parts with kifab so the lock knows them",
                )
            )
        locked_symbols = {(s.library, s.name): s.sha256 for s in lock.symbols}
        for sym_file in sorted(fab_dir.glob("*.kicad_sym")):
            lib_rel = self.rel(sym_file)
            lib = self.libs.symbol_library(sym_file)
            if lib is None:
                self.add(error(Code.PARSE_ERROR, "unreadable library", path=lib_rel))
                continue
            for sym in lib.symbols:
                digest = locked_symbols.get((lib_rel, sym.name))
                if digest is None:
                    self.add(
                        warning(
                            Code.UNLOCKED_SYMBOL,
                            f"symbol {sym.name} is not in {LOCK_NAME}",
                            path=lib_rel,
                        )
                    )
                elif digest != sym.digest():
                    self.add(
                        error(
                            Code.LOCK_HASH_MISMATCH,
                            f"vendored symbol was changed: {sym.name}",
                            path=lib_rel,
                        )
                    )
        for (lib_rel, name), _ in sorted(locked_symbols.items()):
            path = directory / lib_rel
            lib = self.libs.symbol_library(path) if path.is_file() else None
            if lib is None or name not in lib.names():
                self.add(
                    error(
                        Code.LOCK_SYMBOL_MISSING,
                        f"locked symbol {name} is missing from {lib_rel}",
                        path=LOCK_NAME,
                    )
                )

    # ------------------------------------------------------------ git

    def git_hygiene(self) -> None:
        directory = self.project.directory
        if self.git is None:
            return
        try:
            top = self.git.toplevel(directory)
        except GitError as exc:
            self.add(info(Code.NOT_A_GIT_REPO, str(exc)))
            return
        if top is None:
            self.add(
                warning(
                    Code.NOT_A_GIT_REPO,
                    "the project is not in a git repository",
                    hint="git init",
                )
            )
            return
        ignore = directory / ".gitignore"
        if not ignore.is_file() or not gitignore.is_current(
            ignore.read_text(encoding="utf-8")
        ):
            self.add(
                warning(
                    Code.GITIGNORE_OUTDATED,
                    ".gitignore lacks kifab's current managed block",
                    path=".gitignore",
                    hint="run `kifab check --fix-gitignore`",
                )
            )
        try:
            tracked = self.git.tracked(directory)
            untracked = self.git.untracked(directory)
        except GitError as exc:
            self.add(warning(Code.NOT_A_GIT_REPO, str(exc)))
            return
        for rel in sorted(tracked):
            if gitignore.is_ignored(rel):
                self.add(
                    warning(
                        Code.TRACKED_GENERATED,
                        f"{rel} is tracked but is generated or machine-local",
                        path=rel,
                        hint=f"git rm --cached {rel}",
                    )
                )
        for rel in sorted(untracked):
            if _is_source(rel) and not gitignore.is_ignored(rel):
                self.add(
                    warning(
                        Code.UNTRACKED_SOURCE,
                        f"{rel} should be checked in",
                        path=rel,
                        hint=f"git add {rel}",
                    )
                )


def _project_path(model: str) -> PurePosixPath | None:
    """The project-relative path of a ``${KIPRJMOD}/...`` model, else ``None``."""
    prefix = PROJECT_VAR + "/"
    if not model.startswith(prefix):
        return None
    rel = PurePosixPath(model[len(prefix) :])
    return None if ".." in rel.parts else rel


def _is_source(rel: str) -> bool:
    path = PurePosixPath(rel)
    if path.parts and path.parts[0] in (BACKUP_DIR, "build", "orders"):
        return False
    return any(path.match(p) for p in SOURCE_PATTERNS) or rel.startswith("lib/")


def run_check(
    project: Project,
    *,
    git: Git | None = None,
    reporter: Reporter | None = None,
) -> CheckResult:
    """Run every check. ``git=None`` skips the git hygiene part."""
    checker = Checker(project, git, reporter or NullReporter())
    result = CheckResult()

    def step(name: str, fn: Callable[[], None]) -> None:
        before = len(checker.issues)
        fn()
        checker.flush()
        found = len(checker.issues) - before
        result.checks.append(name)
        checker.reporter.step(name, f"{found} finding(s)" if found else "ok")

    kicad = project.kicad
    open_files = locked_files(project.directory)
    if open_files:
        checker.add(
            info(
                Code.KICAD_OPEN,
                "KiCad has " + ", ".join(open_files) + " open; unsaved changes "
                "are not checked",
            )
        )
    if project.config is None:
        checker.add(
            info(
                Code.NO_CONFIG,
                f"no {CONFIG_NAME}: design rules and the vendored-library lock "
                "are not checked",
            )
        )
    step("library tables", checker.lib_tables)

    schematic: Schematic | None = None
    board: Board | None = None
    try:
        schematic = read_schematic(kicad.sch, kicad.name)
    except (OSError, sexpr.SexprError) as exc:
        checker.add(error(Code.PARSE_ERROR, str(exc), path=kicad.rel(kicad.sch)))
    if kicad.pcb.is_file():
        try:
            board = read_board(kicad.pcb)
        except (OSError, sexpr.SexprError) as exc:
            checker.add(error(Code.PARSE_ERROR, str(exc), path=kicad.rel(kicad.pcb)))
    if schematic is not None:
        sch = schematic
        step("schematic symbols", lambda: checker.schematic(sch))
    if board is not None:
        brd = board
        step("board footprints", lambda: checker.board(brd))
    step("footprint libraries", checker.footprint_libraries)
    if schematic is not None and board is not None:
        sch2, brd2 = schematic, board
        step("schematic vs board", lambda: checker.parity(sch2, brd2))
    if project.config is not None:
        step("vendored library lock", checker.lock)
        step("design rules", lambda: _extend(checker, check_rules(project)))
    if git is not None:
        step("git hygiene", checker.git_hygiene)
    checker.flush()
    result.issues = checker.issues
    return result


def _extend(checker: Checker, issues: Iterable[Issue]) -> None:
    for issue in issues:
        checker.add(issue)
