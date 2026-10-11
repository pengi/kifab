"""Order-file helpers shared by fab plugins: CSV bytes and the gerber zip."""

from __future__ import annotations

import csv
import io
import re

from kifab.core.manufacturing import BuildOutputs, FileArtifact
from kifab.core.reproducible import deterministic_zip

#: Files a fab manufactures the board from: gerbers (incl. inner layers .g1,
#: .g2 ...), job file, drill files.
_ZIP_MEMBER = re.compile(r".*\.(gtl|gbl|gts|gbs|gto|gbo|gtp|gbp|gm1|g\d+|gbrjob|drl)$")


def csv_bytes(rows: list[list[str]], quote_all: bool) -> bytes:
    """CSV with CRLF line ends, UTF-8 (what fab upload forms expect)."""
    buf = io.StringIO()
    writer = csv.writer(
        buf,
        quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def gerber_zip(
    built: BuildOutputs,
    extra: tuple[FileArtifact, ...] = (),
    name: str = "",
) -> FileArtifact:
    """``<board>_gerbers.zip`` (or ``name``): gerbers, job and drill files, plus
    ``extra`` files; reproducible."""
    members = [
        (p.name, p.read_bytes())
        for p in (*built.gerbers, *built.drills, *([built.job] if built.job else []))
        if _ZIP_MEMBER.match(p.name)
    ]
    members += [(a.name, a.data) for a in extra]
    return FileArtifact(
        name or f"{built.board_name}_gerbers.zip",
        deterministic_zip(members, built.stamp),
    )
