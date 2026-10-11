"""``kifab init``: a new, self-contained, git-ready KiCad project."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from kifab import __version__
from kifab.core import gitignore
from kifab.core.basic_symbols import basic_library
from kifab.core.config import (
    SUPPORTED_LAYERS,
    BoardConfig,
    Config,
    ConfigError,
    RulesConfig,
)
from kifab.core.design_rules import render_dru
from kifab.core.git import Git
from kifab.core.lockfile import Lockfile
from kifab.core.project import BASIC_LIB, LIB_DIR, Project
from kifab.fabs.base import get_fab
from kifab.kicad import sexpr
from kifab.kicad.libtable import LibTable, LibTableEntry, TableKind
from kifab.kicad.project import KicadProject, ProjectFile
from kifab.kicad.symbol_lib import SymbolLibrary

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: KiCad 9 file versions; KiCad 10 opens them and upgrades on save.
SCH_VERSION = "20250114"
PCB_VERSION = "20241229"
KEEP = ".gitkeep"


class InitError(Exception):
    pass


@dataclass(frozen=True)
class InitResult:
    project: Project
    files: tuple[Path, ...]
    git_initialised: bool


def _sch(name: str) -> str:
    s = sexpr.Sym
    node: sexpr.Node = [
        s("kicad_sch"),
        [s("version"), s(SCH_VERSION)],
        [s("generator"), "kifab"],
        [s("generator_version"), __version__],
        [s("uuid"), str(uuid.uuid4())],
        [s("paper"), "A4"],
        [s("title_block"), [s("title"), name]],
        [s("lib_symbols")],
        [s("sheet_instances"), [s("path"), "/", [s("page"), "1"]]],
        [s("embedded_fonts"), s("no")],
    ]
    return sexpr.dumps(node)


def _copper_layers(count: int) -> list[tuple[int, str]]:
    inner = [(2 + 2 * i, f"In{i}.Cu") for i in range(1, count - 1)]
    return [(0, "F.Cu"), *inner, (2, "B.Cu")]


_TECH_LAYERS = (
    (9, "F.Adhes", "F.Adhesive"),
    (11, "B.Adhes", "B.Adhesive"),
    (13, "F.Paste", None),
    (15, "B.Paste", None),
    (5, "F.SilkS", "F.Silkscreen"),
    (7, "B.SilkS", "B.Silkscreen"),
    (1, "F.Mask", None),
    (3, "B.Mask", None),
    (17, "Dwgs.User", "User.Drawings"),
    (19, "Cmts.User", "User.Comments"),
    (21, "Eco1.User", "User.Eco1"),
    (23, "Eco2.User", "User.Eco2"),
    (25, "Edge.Cuts", None),
    (27, "Margin", None),
    (31, "F.CrtYd", "F.Courtyard"),
    (29, "B.CrtYd", "B.Courtyard"),
    (35, "F.Fab", None),
    (33, "B.Fab", None),
)


def _pcb(layers: int) -> str:
    s = sexpr.Sym
    layer_nodes: list[sexpr.Item] = [
        [s(str(n)), name, s("signal")] for n, name in _copper_layers(layers)
    ]
    for n, name, user in _TECH_LAYERS:
        item: sexpr.Node = [s(str(n)), name, s("user")]
        if user:
            item.append(user)
        layer_nodes.append(item)
    node: sexpr.Node = [
        s("kicad_pcb"),
        [s("version"), s(PCB_VERSION)],
        [s("generator"), "kifab"],
        [s("generator_version"), __version__],
        [s("general"), [s("thickness"), s("1.6")], [s("legacy_teardrops"), s("no")]],
        [s("paper"), "A4"],
        [s("layers"), *layer_nodes],
        [
            s("setup"),
            [s("pad_to_mask_clearance"), s("0")],
            [s("allow_soldermask_bridges_in_footprints"), s("no")],
        ],
        [s("net"), s("0"), ""],
        _outline(),
        [s("embedded_fonts"), s("no")],
    ]
    return sexpr.dumps(node)


#: Default board outline (mm), so a new project passes DRC and can be bundled.
OUTLINE = ((100, 100), (150, 150))


def _outline() -> sexpr.Node:
    s = sexpr.Sym
    (x0, y0), (x1, y1) = OUTLINE
    return [
        s("gr_rect"),
        [s("start"), sexpr.number(x0), sexpr.number(y0)],
        [s("end"), sexpr.number(x1), sexpr.number(y1)],
        [s("stroke"), [s("width"), s("0.05")], [s("type"), s("default")]],
        [s("fill"), s("no")],
        [s("layer"), "Edge.Cuts"],
        [s("uuid"), str(uuid.uuid4())],
    ]


def init_project(
    directory: Path,
    name: str,
    fab_name: str,
    *,
    layers: int = 2,
    git: Git | None = None,
) -> InitResult:
    """Create ``directory`` (which must not exist or be empty) as a kifab project.

    With ``git`` given, ``git init`` runs unless the directory is already inside
    a repository.
    """
    if not _NAME.fullmatch(name):
        raise InitError(
            f"invalid project name {name!r}: use letters, digits, '.', '_', '-'"
        )
    if layers not in SUPPORTED_LAYERS:
        raise InitError(f"layers must be an even number from 2 to 20, got {layers}")
    try:
        fab = get_fab(fab_name)
    except Exception as exc:  # UnknownFab, or a broken plugin
        raise InitError(str(exc)) from exc
    directory = Path(directory)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise InitError(f"{directory} exists and is not empty; refusing to overwrite")
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()

    config = Config(
        fab=fab.name,
        project=name,
        board=BoardConfig(layers=layers),
        rules=RulesConfig(profile=fab.profiles()[0]),
    )
    try:
        profile = fab.design_rules(config.rules.profile, config.rule_options())
    except (ValueError, ConfigError) as exc:
        raise InitError(str(exc)) from exc

    kicad = KicadProject(directory, name)
    lib = fab.library_name
    lib_rel = f"{LIB_DIR}/{lib}"
    pro = ProjectFile.new(name)
    pro.set_board_rules(profile.constraints.values())

    sym_table = LibTable(
        TableKind.SYMBOL,
        (
            LibTableEntry(
                BASIC_LIB,
                f"${{KIPRJMOD}}/{LIB_DIR}/{BASIC_LIB}/{BASIC_LIB}.kicad_sym",
                descr="Basic symbols: power, ground, flags (shipped by kifab)",
            ),
            LibTableEntry(
                lib,
                f"${{KIPRJMOD}}/{lib_rel}/{lib}.kicad_sym",
                descr=f"{fab.title} parts (vendored by kifab)",
            ),
        ),
    )
    fp_table = LibTable(
        TableKind.FOOTPRINT,
        (
            LibTableEntry(
                lib,
                f"${{KIPRJMOD}}/{lib_rel}/{lib}.pretty",
                descr=f"{fab.title} footprints (vendored by kifab)",
            ),
        ),
    )
    files: dict[str, str] = {
        "kifab.toml": config.dumps(),
        "kifab.lock": Lockfile().dumps(),
        f"{name}.kicad_pro": pro.dumps(),
        f"{name}.kicad_sch": _sch(name),
        f"{name}.kicad_pcb": _pcb(layers),
        f"{name}.kicad_dru": render_dru(profile, None),
        "sym-lib-table": sym_table.dumps(),
        "fp-lib-table": fp_table.dumps(),
        f"{LIB_DIR}/{BASIC_LIB}/{BASIC_LIB}.kicad_sym": basic_library().dumps(),
        f"{lib_rel}/{lib}.kicad_sym": SymbolLibrary(symbols=()).dumps(),
        f"{lib_rel}/{lib}.pretty/{KEEP}": "",
        f"{lib_rel}/{lib}.3dshapes/{KEEP}": "",
        ".gitignore": gitignore.update(""),
    }
    written: list[Path] = []
    for rel, text in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    initialised = False
    if git is not None and git.toplevel(directory) is None:
        git.init(directory)
        initialised = True
    return InitResult(Project(kicad, config), tuple(written), initialised)
