"""Fetch a part's symbol, footprint and 3D model: easyeda2kicad behind an interface.

easyeda2kicad (https://github.com/uPesy/easyeda2kicad.py) downloads the EasyEDA
data JLCPCB assembles from and converts it. kifab runs it into a scratch
directory with ``--full --overwrite`` and does the vendoring itself, so none of
the tool's quirks (``--project-relative`` depending on the working directory,
"already exists" exits) reach the project.

``$KIFAB_EASYEDA2KICAD`` replaces the command (tests use a stand-in);
``$KIFAB_OFFLINE=<dir>`` copies captured output from
``<dir>/easyeda2kicad_output/<id>/`` instead of running anything.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from kifab.fabs.base import FetchedPart, FetchError

ENV_COMMAND = "KIFAB_EASYEDA2KICAD"
OUTPUT_NAME = "jlcpcb"  # easyeda2kicad's library name: footprints become jlcpcb:<name>
_VERSION = re.compile(r"easyeda2kicad(?:\.py)?\s+v?(\d+(?:\.\d+)+)", re.IGNORECASE)
_LCSC = re.compile(r"^C\d+$")


def _collect(part_id: str, out: Path, log: str) -> FetchedPart:
    pretty = out.with_suffix(".pretty")
    shapes = out.with_suffix(".3dshapes")
    sym = out.with_suffix(".kicad_sym")
    footprints = tuple(sorted(pretty.glob("*.kicad_mod"))) if pretty.is_dir() else ()
    models = (
        tuple(sorted(p for p in shapes.iterdir() if p.is_file()))
        if shapes.is_dir()
        else ()
    )
    if not footprints and not sym.is_file():
        tail = log.strip().splitlines()[-1:] or ["no output"]
        raise FetchError(f"{part_id}: easyeda2kicad produced nothing ({tail[0]})")
    m = _VERSION.search(log)
    return FetchedPart(
        part_id=part_id,
        symbol_library=sym if sym.is_file() else None,
        footprints=footprints,
        models=models,
        log=log,
        source=f"easyeda2kicad {m.group(1)}" if m else "easyeda2kicad",
    )


def _check_id(part_id: str) -> str:
    wanted = part_id.strip().upper()
    if not _LCSC.match(wanted):
        raise FetchError(f"{part_id!r} is not an LCSC part number (C123456)")
    return wanted


@dataclass
class Easyeda2kicad:
    """:class:`kifab.fabs.base.PartSource` running the easyeda2kicad tool."""

    command: tuple[str, ...] = ()
    timeout: float = 120.0

    def argv(self) -> list[str]:
        if self.command:
            return list(self.command)
        env = os.environ.get(ENV_COMMAND)
        return shlex.split(env) if env else [sys.executable, "-m", "easyeda2kicad"]

    def fetch(self, part_id: str, workdir: Path) -> FetchedPart:
        part_id = _check_id(part_id)
        out = Path(workdir) / OUTPUT_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            *self.argv(),
            f"--lcsc_id={part_id}",
            "--full",
            "--overwrite",
            "--output",
            str(out),
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=workdir,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"{part_id}: easyeda2kicad timed out") from exc
        except OSError as exc:
            raise FetchError(
                f"cannot run easyeda2kicad ({exc}); install it: pip install easyeda2kicad"
            ) from exc
        log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            last = log.strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
            raise FetchError(f"{part_id}: easyeda2kicad failed: {last[0]}")
        if "No module named easyeda2kicad" in log:
            raise FetchError(
                "easyeda2kicad is not installed: pip install easyeda2kicad"
            )
        return _collect(part_id, out, log)


@dataclass
class FixturePartSource:
    """Offline :class:`PartSource`: captured easyeda2kicad output per part."""

    root: Path

    def fetch(self, part_id: str, workdir: Path) -> FetchedPart:
        part_id = _check_id(part_id)
        captured = Path(self.root) / "easyeda2kicad_output" / part_id
        if not captured.is_dir():
            raise FetchError(f"{part_id}: no captured easyeda2kicad output (offline)")
        dest = Path(workdir) / "captured"
        shutil.copytree(captured, dest)
        log_file = dest / "easyeda2kicad.log"
        log = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
        return _collect(part_id, dest / OUTPUT_NAME, log)
