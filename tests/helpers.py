"""Builders for small KiCad files used by the tests."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from kifab.core.lockfile import (
    LockedFile,
    LockedPart,
    LockedSymbol,
    Lockfile,
    sha256_file,
)
from kifab.core.project import Project
from kifab.kicad.symbol_lib import SymbolLibrary, read_symbol_library

FIXTURES = Path(__file__).resolve().parent / "fixtures"
R0603 = FIXTURES / "easyeda2kicad_output/C900001"


def symbol(
    ref: str,
    lcsc: str,
    value: str = "v",
    footprint: str = "Lib:FP",
    dnp: str = "no",
    field: str = "LCSC",
    extra_refs: tuple[str, ...] = (),
    lib_id: str = "Device:X",
    project: str = "p",
    in_bom: str = "yes",
) -> str:
    refs = "".join(
        f'(path "/{r}" (reference "{r}") (unit 1))' for r in (ref, *extra_refs)
    )
    lcsc_prop = f'(property "{field}" "{lcsc}")' if lcsc else ""
    return (
        f'(symbol (lib_id "{lib_id}") (at 0 0 0) (in_bom {in_bom}) (dnp {dnp}) '
        f'(property "Reference" "{ref}") (property "Value" "{value}") '
        f'(property "Footprint" "{footprint}") {lcsc_prop} '
        f'(instances (project "{project}" {refs})))'
    )


def sheet_node(filename: str) -> str:
    return f'(sheet (at 0 0) (property "Sheetname" "S") (property "Sheetfile" "{filename}"))'


def schematic(*nodes: str) -> str:
    # lib_symbols definitions carry an LCSC property too and must never be counted
    libs = (
        '(lib_symbols (symbol "Device:X" (property "Reference" "X") '
        '(property "LCSC" "C999999")) (symbol "kifab:GND" (power global)))'
    )
    return "(kicad_sch (version 20250114) " + libs + " " + " ".join(nodes) + ")"


def footprint(
    ref: str,
    lib_id: str,
    *,
    attr: str = "smd",
    model: str | None = None,
    at: str = "10 20 0",
    layer: str = "F.Cu",
    lcsc: str = "",
) -> str:
    model_node = f'(model "{model}")' if model else ""
    lcsc_node = f'(property "LCSC" "{lcsc}")' if lcsc else ""
    return (
        f'(footprint "{lib_id}" (layer "{layer}") (at {at}) '
        f'(property "Reference" "{ref}") (property "Value" "v") {lcsc_node} '
        f'(attr {attr}) (pad "1" smd rect) {model_node})'
    )


def board(*footprints: str) -> str:
    layers = '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))'
    return "(kicad_pcb (version 20241229) " + layers + " " + " ".join(footprints) + ")"


def vendor_r0603(project: Project, project_relative: bool = True) -> None:
    """Copy one easyeda2kicad part into lib/jlcpcb (what `component add` will do).

    The fixture was made without --project-relative, so its 3D path is absolute;
    ``project_relative`` rewrites it the way a vendored part must look.
    """
    lib = project.directory / "lib" / "jlcpcb"
    mod = (R0603 / "jlcpcb.pretty" / "R0603.kicad_mod").read_text()
    if project_relative:
        mod = re.sub(
            r'\(model "[^"]*/', '(model "${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/', mod
        )
    (lib / "jlcpcb.pretty" / "R0603.kicad_mod").write_text(mod)
    shutil.copy(R0603 / "jlcpcb.3dshapes" / "R0603.wrl", lib / "jlcpcb.3dshapes")
    sym = read_symbol_library(R0603 / "jlcpcb.kicad_sym")
    (lib / "jlcpcb.kicad_sym").write_text(SymbolLibrary(sym.symbols).dumps())


def lock_everything(project: Project) -> None:
    d = project.directory
    lib = d / "lib" / "jlcpcb"
    files = [
        p
        for p in sorted(lib.rglob("*"))
        if p.is_file() and p.suffix in (".kicad_mod", ".wrl")
    ]
    sym_lib = read_symbol_library(lib / "jlcpcb.kicad_sym")
    lock = Lockfile(
        parts=(
            LockedPart(
                "C900001",
                "easyeda",
                "easyeda2kicad 1.0.1",
                tuple(s.name for s in sym_lib.symbols),
                tuple(project.rel(f) for f in files),
            ),
        ),
        files=tuple(LockedFile(project.rel(f), sha256_file(f)) for f in files),
        symbols=tuple(
            LockedSymbol("lib/jlcpcb/jlcpcb.kicad_sym", s.name, s.digest())
            for s in sym_lib.symbols
        ),
    )
    lock.save(project.lock_path)
