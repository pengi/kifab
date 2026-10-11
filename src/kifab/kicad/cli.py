"""Wrapper around ``kicad-cli``.

Only the sub-commands kifab needs, with typed results. The program is found via
``$KIFAB_KICAD_CLI`` (a command line, used by the tests to run a stand-in) or
``kicad-cli`` on ``PATH``. Commands are executed through a :class:`Runner`, so
unit tests can also replace the process layer entirely.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ENV_KICAD_CLI = "KIFAB_KICAD_CLI"


class ToolMissing(Exception):
    """The external program is not installed / not found."""


class ToolFailed(Exception):
    """The external program ran but reported failure."""

    def __init__(self, what: str, result: RunResult) -> None:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = f": {detail[-1]}" if detail else ""
        super().__init__(f"{what} failed (exit {result.returncode}){tail}")
        self.result = result


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: Sequence[str], cwd: Path | None = None) -> RunResult: ...


class SubprocessRunner:
    def run(self, argv: Sequence[str], cwd: Path | None = None) -> RunResult:
        try:
            proc = subprocess.run(
                list(argv), capture_output=True, text=True, cwd=cwd, check=False
            )
        except FileNotFoundError as exc:
            raise ToolMissing(f"{argv[0]} not found") from exc
        return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def kicad_cli_command() -> list[str]:
    override = os.environ.get(ENV_KICAD_CLI)
    if override:
        return shlex.split(override)
    exe = shutil.which("kicad-cli")
    if not exe:
        raise ToolMissing(
            "kicad-cli not found in PATH (it ships with KiCad; kifab needs KiCad 10)"
        )
    return [exe]


# ---------------------------------------------------------------- DRC report


@dataclass(frozen=True)
class DrcItem:
    description: str
    x: float | None
    y: float | None
    uuid: str | None


@dataclass(frozen=True)
class DrcViolation:
    type: str
    description: str
    severity: str
    excluded: bool
    items: tuple[DrcItem, ...]

    def location(self) -> str:
        """``"(x, y) mm"`` of the first item with a position, or ``""``."""
        for item in self.items:
            if item.x is not None and item.y is not None:
                return f"({item.x:g}, {item.y:g}) mm"
        return ""


@dataclass(frozen=True)
class DrcReport:
    violations: tuple[DrcViolation, ...]
    unconnected: tuple[DrcViolation, ...]
    parity: tuple[DrcViolation, ...]

    def active(self) -> list[DrcViolation]:
        """Everything not excluded by the user in KiCad."""
        return [
            v
            for v in (*self.violations, *self.unconnected, *self.parity)
            if not v.excluded
        ]


def _opt_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _violations(raw: Any) -> tuple[DrcViolation, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[DrcViolation] = []
    for v in raw:
        if not isinstance(v, dict):
            continue
        items: list[DrcItem] = []
        for item in v.get("items") or []:
            if isinstance(item, dict):
                raw_pos = item.get("pos")
                pos: dict[str, Any] = raw_pos if isinstance(raw_pos, dict) else {}
                items.append(
                    DrcItem(
                        description=str(item.get("description", "")),
                        x=_opt_float(pos.get("x")),
                        y=_opt_float(pos.get("y")),
                        uuid=str(item["uuid"]) if "uuid" in item else None,
                    )
                )
        out.append(
            DrcViolation(
                type=str(v.get("type", "")),
                description=str(v.get("description", "")),
                severity=str(v.get("severity", "error")),
                excluded=bool(v.get("excluded", False)),
                items=tuple(items),
            )
        )
    return tuple(out)


def parse_drc_report(text: str) -> DrcReport:
    """Parse ``kicad-cli pcb drc --format json`` output (tolerant of missing keys)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("DRC report is not a JSON object")
    return DrcReport(
        violations=_violations(data.get("violations")),
        unconnected=_violations(data.get("unconnected_items")),
        parity=_violations(data.get("schematic_parity")),
    )


# ---------------------------------------------------------------- the tool


@dataclass(frozen=True)
class BomColumns:
    """Which schematic fields go into the BOM, under which column labels."""

    fields: tuple[str, ...]
    labels: tuple[str, ...]
    group_by: tuple[str, ...]


class KicadCli:
    """The ``kicad-cli`` sub-commands kifab uses."""

    def __init__(
        self, command: Sequence[str] | None = None, runner: Runner | None = None
    ) -> None:
        self._command = list(command) if command is not None else None
        self.runner: Runner = runner or SubprocessRunner()

    @property
    def command(self) -> list[str]:
        if self._command is None:
            self._command = kicad_cli_command()
        return self._command

    def run(self, *args: str | Path) -> RunResult:
        return self.runner.run(self.command + [str(a) for a in args])

    def version(self) -> str:
        result = self.run("version")
        if result.returncode != 0:
            raise ToolFailed("kicad-cli version", result)
        return result.stdout.strip()

    def drc(self, pcb: Path, report: Path, *, parity: bool = True) -> DrcReport:
        """DRC with zones refilled in memory; the board file is not modified.

        Errors and warnings are reported; exclusions made in KiCad are kept.
        """
        args: list[str | Path] = [
            "pcb",
            "drc",
            "--format",
            "json",
            "--refill-zones",
            "--severity-error",
            "--severity-warning",
        ]
        if parity:
            args.append("--schematic-parity")
        result = self.run(*args, "-o", report, pcb)
        if not report.is_file():
            raise ToolFailed("DRC", result)
        return parse_drc_report(report.read_text(encoding="utf-8"))

    def export_gerbers(self, pcb: Path, out_dir: Path, layers: Sequence[str]) -> None:
        result = self.run(
            "pcb",
            "export",
            "gerbers",
            "-o",
            f"{out_dir}{os.sep}",
            "--check-zones",
            "--subtract-soldermask",
            "--layers",
            ",".join(layers),
            pcb,
        )
        if result.returncode != 0:
            raise ToolFailed("gerber export", result)

    def export_drill(self, pcb: Path, out_dir: Path) -> None:
        """Excellon, mm, PTH/NPTH separate. No drill maps: their PDFs embed the
        plot time, which would make the bundle irreproducible, and fabs do not
        need them."""
        result = self.run(
            "pcb",
            "export",
            "drill",
            "-o",
            f"{out_dir}{os.sep}",
            "--format",
            "excellon",
            "--excellon-units",
            "mm",
            "--excellon-zeros-format",
            "decimal",
            "--excellon-oval-format",
            "route",
            "--excellon-separate-th",
            "--drill-origin",
            "absolute",
            pcb,
        )
        if result.returncode != 0:
            raise ToolFailed("drill export", result)

    def export_ipcd356(self, pcb: Path, out: Path) -> None:
        """IPC-D-356 netlist (for the fab's electrical test)."""
        result = self.run("pcb", "export", "ipcd356", "-o", out, pcb)
        if result.returncode != 0 or not out.is_file():
            raise ToolFailed("IPC-D-356 export", result)

    def export_odb(self, pcb: Path, out_dir: Path) -> None:
        """ODB++ into ``out_dir`` (uncompressed, mm, 6 digits: what AISLER uses)."""
        result = self.run(
            "pcb",
            "export",
            "odb",
            "--units",
            "mm",
            "--precision",
            "6",
            "--compression",
            "none",
            "-o",
            out_dir,
            pcb,
        )
        if result.returncode != 0 or not out_dir.is_dir():
            raise ToolFailed("ODB++ export", result)

    def export_bom(self, sch: Path, out: Path, columns: BomColumns) -> None:
        result = self.run(
            "sch",
            "export",
            "bom",
            "--exclude-dnp",
            "--fields",
            ",".join(columns.fields),
            "--labels",
            ",".join(columns.labels),
            "--group-by",
            ",".join(columns.group_by),
            "--ref-delimiter",
            ",",
            "--ref-range-delimiter",
            "",
            "-o",
            out,
            sch,
        )
        if result.returncode != 0:
            raise ToolFailed("BOM export", result)

    def export_pos(self, pcb: Path, out: Path, *, smd_only: bool) -> None:
        extra = ["--smd-only"] if smd_only else []
        result = self.run(
            "pcb",
            "export",
            "pos",
            "--format",
            "csv",
            "--units",
            "mm",
            "--side",
            "both",
            *extra,
            "--exclude-dnp",
            "-o",
            out,
            pcb,
        )
        if result.returncode != 0:
            raise ToolFailed("placement export", result)
