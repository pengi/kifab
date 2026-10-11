"""``kifab bundle``: validate and produce everything needed to order a board.

Port of jlcpcb-fab. Steps: refuse while KiCad has the project open, ``kifab
check``, DRC, gerbers, drill, the fab's upload files, BOM, placement, report.
All work happens in a staging directory; the output directory (``build/``) is
only changed at the end, and only files named ``<board>*`` are replaced, so a
failed run leaves the previous results untouched.
"""

from __future__ import annotations

import dataclasses

import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kifab import __version__
from kifab.core.check import run_check
from kifab.core.config import CONFIG_NAME
from kifab.core.git import Git, GitError
from kifab.core.issues import (
    Code,
    Issue,
    Severity,
    error,
    has_errors,
    info,
    sort_issues,
)
from kifab.core.orderability import AvailabilityReport, check_availability
from kifab.core.manufacturing import (
    BOM_FOOTPRINT,
    BOM_REFS,
    BOM_VALUE,
    Bom,
    BuildOutputs,
    ExtraOutput,
    FileArtifact,
    OrderFiles,
    ParseError,
    Placement,
    parse_kicad_bom,
    parse_kicad_pos,
)
from kifab.core.project import Project
from kifab.core.reporter import NullReporter, Reporter
from kifab.core.reproducible import normalize_timestamps, source_date
from kifab.core.partref import FieldSpec, field_specs, sku_key
from kifab.core.sourcing import Sourcing, sourcing_for
from kifab.fabs.base import Fab
from kifab.kicad import sexpr
from kifab.kicad.board import Board, read_board
from kifab.kicad.cli import BomColumns, KicadCli, ToolFailed, ToolMissing
from kifab.kicad.project import locked_files
from kifab.kicad.schematic import Schematic, read_schematic, sorted_refs

BUILD_DIR = "build"
TECH_LAYERS = (
    "F.Paste",
    "B.Paste",
    "F.Silkscreen",
    "B.Silkscreen",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
)
GERBER_SUFFIX = re.compile(r"\.(gtl|gbl|gts|gbs|gto|gbo|gtp|gbp|gm1|g\d+)$")
#: Gerbers that must exist after plotting; a missing one means a layer was dropped.
REQUIRED_SUFFIXES = (".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gm1")
TEXT_OUTPUTS = re.compile(r"\.(gtl|gbl|gts|gbs|gto|gbo|gtp|gbp|gm1|g\d+|drl|gbrjob)$")


class StepFailed(Exception):
    """A step failed; its issues are already recorded."""


@dataclass(frozen=True)
class BundleOptions:
    out_dir: Path | None = None  # default: <project>/build
    skip_check: bool = False
    skip_drc: bool = False
    force: bool = False  # run even if KiCad seems to have the project open
    assemble_through_hole: bool | None = None  # None: from kifab.toml
    artifacts_dir: Path | None = None  # also copy the upload set, versioned names
    stock: bool = False  # check stock and part types online (not reproducible)
    boards: int = 1  # quantity for the stock check


@dataclass(frozen=True)
class Artifact:
    """A release file: where it is and the name it is published under."""

    path: Path
    release_name: str


@dataclass
class BundleResult:
    project: str
    out_dir: Path
    version: str
    issues: list[Issue] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    bom: Bom | None = None
    placement: Placement | None = None
    left_out: list[str] = field(default_factory=list)
    dnp: list[str] = field(default_factory=list)
    availability: AvailabilityReport | None = None

    @property
    def ok(self) -> bool:
        return not has_errors(self.issues)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_files(project: Project, schematic: Schematic | None) -> list[Path]:
    """The source files a bundle is made from (for hashes in the manifest)."""
    k = project.kicad
    files = [k.pro, k.pcb, k.dru, project.directory / CONFIG_NAME, project.lock_path]
    files += [project.directory / n for n in ("sym-lib-table", "fp-lib-table")]
    files += list(schematic.sheets) if schematic else [k.sch]
    return sorted({f.resolve() for f in files if f.is_file()})


def input_hashes(project: Project, schematic: Schematic | None) -> dict[str, str]:
    return {
        project.rel(f): sha256_bytes(f.read_bytes())
        for f in input_files(project, schematic)
    }


def bom_columns(
    schematic: Schematic, sourcing: Sourcing
) -> tuple[BomColumns, tuple[FieldSpec, ...]]:
    """The kicad-cli BOM columns for the part fields the schematic uses.

    Each :class:`FieldSpec` (MPN, manufacturer, supplier numbers) becomes a
    column under its key, read from the field name the schematic uses most;
    the configured suppliers' number columns are always there.
    """
    always = {sku_key(s.sku_kind) for s in sourcing.suppliers}
    fields: list[str] = []
    specs: list[FieldSpec] = []
    for spec in field_specs():
        used: Counter[str] = Counter(
            name
            for part in schematic.parts()
            if (name := spec.field_name(part.properties))
        )
        if used:
            fields.append(used.most_common(1)[0][0])
        elif spec.key in always:
            fields.append(spec.names[0])
        else:
            continue
        specs.append(spec)
    return (
        BomColumns(
            fields=("Value", "Reference", "Footprint", *fields),
            labels=(BOM_VALUE, BOM_REFS, BOM_FOOTPRINT, *(s.key for s in specs)),
            group_by=("Value", "Footprint", *fields),
        ),
        tuple(specs),
    )


class _Run:
    def __init__(
        self,
        project: Project,
        fab: Fab,
        options: BundleOptions,
        cli: KicadCli,
        git: Git | None,
        reporter: Reporter,
    ) -> None:
        self.project = project
        self.fab = fab
        self.options = options
        self.cli = cli
        self.git = git
        self.reporter = reporter
        self.name = project.name
        out_dir = options.out_dir or project.directory / BUILD_DIR
        self.result = BundleResult(self.name, out_dir.resolve(), "unversioned")
        self.stamp: datetime | None = None
        self.commit: str | None = None
        self.kicad_version = ""
        self.schematic: Schematic | None = None
        self.board: Board | None = None

    # ------------------------------------------------------------ helpers

    def add(self, issue: Issue) -> None:
        self.result.issues.append(issue)
        self.reporter.issue(issue)

    def fail(self, issue: Issue) -> StepFailed:
        self.add(issue)
        return StepFailed(issue.message)

    def tool(self, what: str, exc: Exception) -> StepFailed:
        code = Code.TOOL_MISSING if isinstance(exc, ToolMissing) else Code.TOOL_FAILED
        return self.fail(error(code, f"{what}: {exc}"))

    def sourcing(self) -> Sourcing:
        return self.project.sourcing() or sourcing_for(self.fab)

    # ------------------------------------------------------------ steps

    def prepare(self) -> None:
        open_files = locked_files(self.project.directory)
        if open_files and not self.options.force:
            raise self.fail(
                error(
                    Code.KICAD_OPEN,
                    "KiCad has " + ", ".join(open_files) + " open; the saved files "
                    "may not be what you see",
                    hint="save and close KiCad, or use --force",
                )
            )
        if self.git is not None:
            try:
                self.result.version = self.git.describe(self.project.directory) or (
                    "unversioned"
                )
                self.commit = self.git.head(self.project.directory)
                self.stamp = source_date(self.git.head_time(self.project.directory))
            except GitError:
                self.stamp = source_date(None)
        else:
            self.stamp = source_date(None)
        kicad = self.project.kicad
        try:
            self.schematic = read_schematic(kicad.sch, kicad.name)
            self.board = read_board(kicad.pcb)
        except (OSError, sexpr.SexprError) as exc:
            raise self.fail(error(Code.PARSE_ERROR, str(exc)))
        self.result.dnp = sorted_refs(
            p.reference for p in self.schematic.parts() if p.dnp
        )

    def check(self) -> None:
        if self.options.skip_check:
            self.reporter.step("check", "skipped")
            return
        result = run_check(self.project)
        problems = [i for i in result.issues if i.severity is not Severity.INFO]
        for issue in problems:
            self.add(issue)
        if has_errors(problems):
            raise self.fail(
                error(
                    Code.CHECK_FAILED,
                    "kifab check found errors",
                    hint="fix them, or use --skip-check to bundle anyway",
                )
            )
        self.reporter.step("check", f"{len(problems)} warning(s)" if problems else "ok")

    def drc(self, tmp: Path) -> None:
        if self.options.skip_drc:
            self.reporter.step("DRC", "skipped")
            return
        try:
            report = self.cli.drc(self.project.kicad.pcb, tmp / "drc.json")
        except (ToolMissing, ToolFailed, ValueError) as exc:
            raise self.tool("DRC", exc)
        active = report.active()
        for v in active:
            code = {
                "unconnected_items": Code.DRC_UNCONNECTED,
            }.get(v.type, Code.DRC_VIOLATION)
            if v in report.parity:
                code = Code.DRC_PARITY
            where = v.location()
            items = "; ".join(i.description for i in v.items if i.description)
            self.add(
                error(
                    code,
                    f"[{v.type}] {v.description}"
                    + (f": {items}" if items else "")
                    + (f" at {where}" if where else "")
                    + f" ({v.severity})",
                    path=self.project.rel(self.project.kicad.pcb),
                )
            )
        if active:
            raise StepFailed("DRC")
        self.reporter.step("DRC", "no errors or warnings")

    def gerbers(self, stage: Path, plot: Path) -> list[Path]:
        assert self.board is not None
        layers = [*self.board.copper_layers, *TECH_LAYERS]
        try:
            self.cli.export_gerbers(self.project.kicad.pcb, plot, layers)
        except (ToolMissing, ToolFailed) as exc:
            raise self.tool("gerber export", exc)
        files = sorted(p for p in plot.iterdir() if p.is_file())
        gerbers = [p for p in files if GERBER_SUFFIX.search(p.name)]
        missing = [
            s for s in REQUIRED_SUFFIXES if not any(p.suffix == s for p in files)
        ]
        if missing or len(gerbers) != len(layers):
            raise self.fail(
                error(
                    Code.OUTPUT_MISSING,
                    f"gerber export made {len(gerbers)} of {len(layers)} layers"
                    + (f" (no {', '.join(missing)})" if missing else ""),
                )
            )
        moved = []
        for f in files:
            target = stage / f.name
            shutil.move(str(f), target)
            moved.append(target)
        self.reporter.step("gerbers", f"{len(gerbers)} layers")
        return moved

    def drill(self, stage: Path, plot: Path) -> list[Path]:
        try:
            self.cli.export_drill(self.project.kicad.pcb, plot)
        except (ToolMissing, ToolFailed) as exc:
            raise self.tool("drill export", exc)
        files = sorted(p for p in plot.iterdir() if p.is_file())
        if not any(p.suffix == ".drl" for p in files):
            raise self.fail(error(Code.OUTPUT_MISSING, "drill export made no files"))
        moved = []
        for f in files:
            target = stage / f.name
            shutil.move(str(f), target)
            moved.append(target)
        kinds = sorted((p.stem.rsplit("-", 1)[-1] for p in moved if p.suffix == ".drl"))
        self.reporter.step("drill", ", ".join(kinds))
        return moved

    def extra_outputs(self, tmp: Path) -> tuple[Path | None, Path | None]:
        """The IPC-D-356 netlist and ODB++ directory, if the fab wants them."""
        wanted = self.fab.extra_outputs
        pcb = self.project.kicad.pcb
        netlist = odb = None
        try:
            if ExtraOutput.IPC_D356 in wanted:
                netlist = tmp / "netlist.ipc"
                self.cli.export_ipcd356(pcb, netlist)
                self.reporter.step("netlist", "IPC-D-356")
            if ExtraOutput.ODB in wanted:
                odb = tmp / "odb"
                self.cli.export_odb(pcb, odb)
                # misc/info and steps/*/eda/data carry the export time
                stamped = [odb / "misc" / "info", *odb.glob("steps/*/eda/data")]
                self.normalize([p for p in stamped if p.is_file()], any_name=True)
                self.reporter.step("ODB++", "exported")
        except (ToolMissing, ToolFailed) as exc:
            raise self.tool("export for the fab", exc)
        return netlist, odb

    def normalize(self, files: list[Path], *, any_name: bool = False) -> None:
        """Set the plot time in text outputs to the build's stamp."""
        if self.stamp is None:
            return
        for f in files:
            if any_name or TEXT_OUTPUTS.search(f.name):
                text = f.read_text(encoding="utf-8")
                new = normalize_timestamps(text, self.stamp)
                if new != text:
                    f.write_text(new, encoding="utf-8")

    def bom_and_placement(self, tmp: Path) -> tuple[Bom, Placement]:
        assert self.schematic is not None
        kicad = self.project.kicad
        columns, specs = bom_columns(self.schematic, self.sourcing())
        raw_bom, pos_all, pos_smd = (tmp / n for n in ("bom.csv", "all.csv", "smd.csv"))
        try:
            self.cli.export_bom(kicad.sch, raw_bom, columns)
            self.cli.export_pos(kicad.pcb, pos_all, smd_only=False)
            self.cli.export_pos(kicad.pcb, pos_smd, smd_only=True)
            bom = parse_kicad_bom(raw_bom.read_text(encoding="utf-8"), specs)
            every = parse_kicad_pos(pos_all.read_text(encoding="utf-8"))
            smd = parse_kicad_pos(pos_smd.read_text(encoding="utf-8"))
        except (ToolMissing, ToolFailed, ParseError, OSError) as exc:
            raise self.tool("BOM/placement export", exc)

        through_hole = every.references() - smd.references()
        every = Placement(
            tuple(
                dataclasses.replace(p, through_hole=p.reference in through_hole)
                for p in every.parts
            )
        )
        assemble_tht = self.options.assemble_through_hole
        if assemble_tht is None:
            config = self.project.config
            assemble_tht = config.bundle.assemble_through_hole if config else False
        if assemble_tht:
            placement = every
        else:
            placement = smd
            bom = bom.without(through_hole)
            self.result.left_out = sorted_refs(through_hole)
            if through_hole:
                self.add(
                    info(
                        Code.THROUGH_HOLE_LEFT_OUT,
                        "through-hole parts are left out of BOM and placement "
                        "(order and solder them yourself)",
                        ref=",".join(self.result.left_out),
                    )
                )
        if self.result.dnp:
            self.add(
                info(
                    Code.DNP,
                    "do-not-populate parts are left out",
                    ref=",".join(self.result.dnp),
                )
            )
        for issue in self.fab.check_parts(bom):
            self.add(issue)
        if self.options.stock:
            report = check_availability(
                bom, self.sourcing().supplier_catalogs(), boards=self.options.boards
            )
            self.result.availability = report
            for issue in report.issues:
                self.add(issue)
            self.reporter.step(
                "stock",
                f"{len(report.lines)} parts checked for {report.boards} board(s), "
                f"{len(report.extended_types)} Extended, parts ~${report.parts_cost:.2f}",
            )
        self.reporter.step("BOM", f"{len(bom.lines)} lines, {bom.part_count} parts")
        self.reporter.step("placement", f"{len(placement.parts)} parts")
        return bom, placement

    # ------------------------------------------------------------ output

    def write(self, stage: Path, artifact: FileArtifact) -> Path:
        path = stage / artifact.name
        path.write_bytes(artifact.data)
        return path

    def manifest(self, uploads: list[Path], bom: Bom, placement: Placement) -> bytes:
        payload: dict[str, object] = {
            "kifab": __version__,
            "fab": self.fab.name,
            "project": self.name,
            "version": self.result.version,
            "commit": self.commit,
            "source_date": (
                self.stamp.astimezone(timezone.utc).isoformat() if self.stamp else None
            ),
            "kicad": self.kicad_version,
            "ok": self.result.ok,
            "inputs": input_hashes(self.project, self.schematic),
            "files": {p.name: sha256_bytes(p.read_bytes()) for p in sorted(uploads)},
            "bom_lines": len(bom.lines),
            "bom_parts": bom.part_count,
            "placed": len(placement.parts),
            "left_out": self.result.left_out,
            "dnp": self.result.dnp,
            "issues": [i.to_json() for i in sort_issues(self.result.issues)],
        }
        if self.result.availability is not None:
            payload["stock"] = self.result.availability.snapshot()
            payload["boards"] = self.result.availability.boards
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def report(self, uploads: list[Path], bom: Bom, placement: Placement) -> bytes:
        r = self.result
        lines = [
            f"# {self.name} {r.version}: manufacturing files for {self.fab.title}",
            "",
            f"- version: `{r.version}`"
            + (f", commit `{self.commit}`" if self.commit else ""),
            f"- kifab {__version__}"
            + (f", KiCad {self.kicad_version}" if self.kicad_version else ""),
        ]
        if self.stamp:
            stamp = self.stamp.astimezone(timezone.utc).isoformat()
            lines.append(f"- file time stamps: {stamp} (reproducible)")
        lines += [
            f"- BOM: {len(bom.lines)} lines, {bom.part_count} parts; "
            f"placement: {len(placement.parts)} parts",
            "",
            "## Files",
            "",
            "| file | sha256 |",
            "|------|--------|",
        ]
        lines += [
            f"| {p.name} | `{sha256_bytes(p.read_bytes())}` |" for p in sorted(uploads)
        ]
        if r.left_out:
            lines += [
                "",
                "## Left out (through-hole, solder yourself)",
                "",
                ", ".join(r.left_out),
            ]
        if r.dnp:
            lines += ["", "## Do not populate", "", ", ".join(r.dnp)]
        lines += ["", "## Findings", ""]
        findings = sort_issues(r.issues)
        if findings:
            for i in findings:
                where = f" ({i.ref})" if i.ref else ""
                lines.append(
                    f"- **{i.severity.value}** `{i.code.value}`: {i.message}{where}"
                )
        else:
            lines.append("None.")
        lines += [
            "",
            "## Before ordering",
            "",
            "Check polarized and rotated parts (modules, ICs, LEDs) in the fab's "
            "placement preview.",
            "",
        ]
        return "\n".join(lines).encode("utf-8")

    def publish(self, stage: Path) -> list[Path]:
        out = self.result.out_dir
        out.mkdir(parents=True, exist_ok=True)
        for old in out.iterdir():
            if old.is_file() and old.name.startswith(self.name):
                old.unlink()
        moved = []
        for f in sorted(stage.iterdir()):
            if f.is_file():
                target = out / f.name
                shutil.move(str(f), target)
                moved.append(target)
        return moved

    def copy_artifacts(self, uploads: list[str]) -> None:
        if self.options.artifacts_dir is None:
            return
        dest = self.options.artifacts_dir
        dest.mkdir(parents=True, exist_ok=True)
        prefix = f"{self.name}-{self.result.version}"
        for name in uploads:
            src = self.result.out_dir / name
            suffix = name[len(self.name) :].lstrip("_-")
            target = dest / f"{prefix}-{suffix}"
            shutil.copy2(src, target)
            self.result.artifacts.append(Artifact(target, target.name))

    # ------------------------------------------------------------ all

    def run(self) -> BundleResult:
        try:
            self.prepare()
            self.check()
            try:
                self.kicad_version = self.cli.version()
            except (ToolMissing, ToolFailed) as exc:
                raise self.tool("kicad-cli", exc)
            with tempfile.TemporaryDirectory(prefix="kifab-bundle-") as tmp_name:
                tmp = Path(tmp_name)
                stage, plot = tmp / "stage", tmp / "plot"
                stage.mkdir()
                plot.mkdir()
                self.drc(tmp)
                plotted = self.gerbers(stage, plot)
                plotted += self.drill(stage, plot)
                self.normalize(plotted)
                netlist, odb = self.extra_outputs(tmp)
                built = BuildOutputs(
                    board_name=self.name,
                    gerbers=tuple(p for p in plotted if GERBER_SUFFIX.search(p.name)),
                    drills=tuple(p for p in plotted if p.suffix == ".drl"),
                    job=next((p for p in plotted if p.suffix == ".gbrjob"), None),
                    others=tuple(p for p in plotted if p.suffix == ".pdf"),
                    stamp=self.stamp,
                    netlist=netlist,
                    odb=odb,
                )
                bom, placement = self.bom_and_placement(tmp)
                order_files = (
                    self.fab.export_bom(bom, self.name),
                    self.fab.export_placement(placement, self.name),
                )
                order = OrderFiles(bom, placement, order_files)
                uploads = [
                    self.write(stage, a) for a in self.fab.bundle_files(built, order)
                ]
                self.reporter.step("zip", ", ".join(p.name for p in uploads))
                uploads += [self.write(stage, a) for a in order_files]
                report = self.write(
                    stage,
                    FileArtifact(
                        f"{self.name}_report.md", self.report(uploads, bom, placement)
                    ),
                )
                uploads.append(report)
                self.write(
                    stage,
                    FileArtifact(
                        f"{self.name}_manifest.json",
                        self.manifest(uploads, bom, placement),
                    ),
                )
                self.reporter.step("report", report.name)
                upload_names = [p.name for p in uploads]
                self.result.files = self.publish(stage)
                self.result.bom, self.result.placement = bom, placement
            self.copy_artifacts(upload_names)
        except StepFailed:
            pass
        return self.result


def bundle(
    project: Project,
    options: BundleOptions | None = None,
    *,
    cli: KicadCli | None = None,
    git: Git | None = None,
    reporter: Reporter | None = None,
) -> BundleResult:
    """Build the manufacturing files of ``project``; never raises for bad input."""
    options = options or BundleOptions()
    fab = project.fab()
    run = _Run(
        project,
        fab if fab is not None else _default_fab(),
        options,
        cli or KicadCli(),
        git,
        reporter or NullReporter(),
    )
    if fab is None:
        run.add(
            info(
                Code.NO_CONFIG,
                f"no {CONFIG_NAME}; using {run.fab.title} as the fab",
            )
        )
    return run.run()


def _default_fab() -> Fab:
    from kifab.fabs.base import get_fab

    return get_fab("jlcpcb")
