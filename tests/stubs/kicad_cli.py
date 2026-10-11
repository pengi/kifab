"""Stand-in for ``kicad-cli`` used by the tests (no KiCad needed).

Implements the sub-commands kifab calls and writes files in KiCad's formats.
BOM and placement are computed from the real input files with kifab's own
readers, the way kicad-cli would, so pipelines can be tested end to end on
``tests/fixtures/demo-board``.

Switches (environment variables):
  STUB_DRC_FAIL=1         ``pcb drc`` reports one error violation
  STUB_DRC_EXCLUDED=1     ``pcb drc`` reports one violation the user excluded
  STUB_FAIL=<subcommand>  that sub-command (e.g. "export drill") exits 1
  STUB_CLI_LOG=<file>     every invocation is appended to this file
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from pathlib import Path

from kifab.kicad.board import read_board
from kifab.kicad.schematic import read_schematic, ref_sort_key, sorted_refs

EXT = {
    "F.Cu": "gtl",
    "B.Cu": "gbl",
    "F.Paste": "gtp",
    "B.Paste": "gbp",
    "F.Silkscreen": "gto",
    "B.Silkscreen": "gbo",
    "F.Mask": "gts",
    "B.Mask": "gbs",
    "Edge.Cuts": "gm1",
}
HEADER = (
    "%TF.GenerationSoftware,KiCad,Pcbnew,10.0.6*%\n"
    "%TF.CreationDate,2026-10-10T19:55:02+02:00*%\n"
    "G04 Created by KiCad (PCBNEW 10.0.6) date 2026-10-10 19:55:02*\n"
)


def opt(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def ext_for(layer: str) -> str:
    if layer in EXT:
        return EXT[layer]
    if layer.startswith("In") and layer.endswith(".Cu"):
        return "g" + layer[2:-3]
    raise SystemExit(f"Unknown layer {layer}")


def gerbers(args: list[str], pcb: Path) -> None:
    out = Path(opt(args, "-o"))
    layers = opt(args, "--layers").split(",")
    for layer in layers:
        ext_for(layer)
    for layer in layers:
        name = f"{pcb.stem}-{layer.replace('.', '_')}.{ext_for(layer)}"
        (out / name).write_text(HEADER + f"G04 layer {layer}*\nM02*\n")
    job = {"Header": {"CreationDate": "2026-10-10T19:55:02+02:00"}}
    (out / f"{pcb.stem}-job.gbrjob").write_text(json.dumps(job, indent=2) + "\n")


def drill(args: list[str], pcb: Path) -> None:
    out = Path(opt(args, "-o"))
    for kind in ("PTH", "NPTH"):
        (out / f"{pcb.stem}-{kind}.drl").write_text(
            "M48\n; DRILL file KiCad 10.0.6 date 2026-10-10T19:55:02\n"
            "; #@! TF.CreationDate,2026-10-10T19:55:02+02:00\nM30\n"
        )
    if "--generate-map" in args:
        for kind in ("PTH", "NPTH"):
            (out / f"{pcb.stem}-{kind}-drl_map.pdf").write_text("%PDF\n")


def pos(args: list[str], pcb: Path) -> None:
    board = read_board(pcb)
    smd_only = "--smd-only" in args
    rows = []
    for fp in board.footprints:
        attrs = fp.footprint.attributes
        if "exclude_from_pos_files" in attrs or "board_only" in attrs:
            continue
        if "--exclude-dnp" in args and fp.dnp:
            continue
        if smd_only and not fp.footprint.smd:
            continue
        rows.append(fp)
    rows.sort(key=lambda fp: ref_sort_key(fp.reference))
    buf = io.StringIO()
    buf.write("Ref,Val,Package,PosX,PosY,Rot,Side\n")
    for fp in rows:
        buf.write(
            f'"{fp.reference}","{fp.value}","{fp.lib_id.name}",'
            f"{fp.x:.6f},{-fp.y:.6f},{fp.rotation:.6f},{fp.side.value}\n"
        )
    Path(opt(args, "-o")).write_text(buf.getvalue(), encoding="utf-8")


def bom(args: list[str], sch: Path) -> None:
    """Like kicad-cli: any fields (missing ones are empty), grouped by --group-by."""
    schematic = read_schematic(sch)
    fields = opt(args, "--fields").split(",")
    labels = opt(args, "--labels").split(",")
    group_by = opt(args, "--group-by").split(",")
    groups: dict[tuple[str, ...], list[str]] = {}
    values: dict[tuple[str, ...], dict[str, str]] = {}
    for part in schematic.parts():
        if not part.in_bom or ("--exclude-dnp" in args and part.dnp):
            continue
        props = {**part.properties, "Value": part.value, "Footprint": part.footprint}
        key = tuple(props.get(f, "") for f in group_by)
        groups.setdefault(key, []).append(part.reference)
        values.setdefault(key, props)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(labels)
    rows = []
    for key, refs in groups.items():
        refs = sorted_refs(refs)
        props = {**values[key], "Reference": ",".join(refs)}
        rows.append((refs[0], [props.get(f, "") for f in fields]))
    for _, row in sorted(rows, key=lambda r: ref_sort_key(r[0])):
        writer.writerow(row)
    Path(opt(args, "-o")).write_text(buf.getvalue(), encoding="utf-8")


def drc(args: list[str]) -> None:
    violations: list[dict[str, object]] = []
    if os.environ.get("STUB_DRC_FAIL"):
        violations.append(
            {
                "type": "silk_overlap",
                "description": "Silkscreen clearance",
                "severity": "error",
                "items": [{"description": "Pad 1 of R1", "pos": {"x": 1.5, "y": 2}}],
            }
        )
    if os.environ.get("STUB_DRC_EXCLUDED"):
        violations.append(
            {
                "type": "starved_thermal",
                "description": "Thermal relief connection to zone incomplete",
                "severity": "warning",
                "excluded": True,
                "items": [],
            }
        )
    report = {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "violations": violations,
        "unconnected_items": [],
        "schematic_parity": [],
    }
    Path(opt(args, "-o")).write_text(json.dumps(report, indent=2))


def main() -> int:
    args = sys.argv[1:]
    log = os.environ.get("STUB_CLI_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n")
    if args == ["version"]:
        print("10.0.6")
        return 0
    cmd = " ".join(args[:3] if args[1:2] == ["export"] else args[:2])
    if os.environ.get("STUB_FAIL") and os.environ["STUB_FAIL"] in cmd:
        print(f"stub: simulated failure of {cmd}", file=sys.stderr)
        return 1
    target = Path(args[-1])
    if cmd == "pcb drc":
        drc(args)
    elif cmd == "pcb export gerbers":
        gerbers(args, target)
    elif cmd == "pcb export drill":
        drill(args, target)
    elif cmd == "pcb export pos":
        pos(args, target)
    elif cmd == "sch export bom":
        bom(args, target)
    else:
        print(f"stub: unsupported command {args}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
