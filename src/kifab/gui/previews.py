"""What the part detail panel shows: product images and footprint/symbol drawings.

Plain Python (no Qt), so it runs in jobs and tests. A part's footprint and
symbol come from the project library when it is vendored; otherwise they are
fetched with the fab's part source into kifab-gui's cache (never into a
project), so looking at a part does not change anything.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kifab.core.inventory import LibraryPart
from kifab.core.project import Project
from kifab.libraries.base import PartSource
from kifab.suppliers.base import Catalog
from kifab.kicad.drawing import (
    Drawing,
    library_symbol_drawing,
    read_footprint_drawing,
)
from kifab.kicad.symbol_lib import read_symbol_library

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
COMPLETE = ".complete"
SYMBOLS = "symbols.kicad_sym"


@dataclass(frozen=True)
class PreviewFiles:
    footprints: tuple[Path, ...] = ()
    symbol_library: Path | None = None
    symbol: str | None = None  # name in symbol_library; None: the first one


@dataclass(frozen=True)
class Preview:
    footprint: Drawing | None = None
    footprint_name: str = ""
    symbol: Drawing | None = None
    symbol_name: str = ""


def _part_dir(cache: Path, part_id: str) -> Path:
    if not _SAFE_ID.match(part_id):
        raise ValueError(f"unexpected part number {part_id!r}")
    return cache / "parts" / part_id.upper()


def cached_preview(cache: Path, part_id: str) -> PreviewFiles | None:
    """Files fetched earlier for ``part_id``, if the cache has them."""
    try:
        d = _part_dir(cache, part_id)
    except ValueError:
        return None
    if not (d / COMPLETE).is_file():
        return None
    sym = d / SYMBOLS
    return PreviewFiles(
        tuple(sorted(d.glob("*.kicad_mod"))), sym if sym.is_file() else None
    )


def fetch_preview(source: PartSource, cache: Path, part_id: str) -> PreviewFiles:
    """Fetch ``part_id`` with ``source`` into the cache (raises FetchError)."""
    d = _part_dir(cache, part_id)
    with tempfile.TemporaryDirectory(prefix="kifab-preview-") as tmp:
        fetched = source.fetch(part_id, Path(tmp))
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for fp in fetched.footprints:
            shutil.copyfile(fp, d / fp.name)
        if fetched.symbol_library is not None:
            shutil.copyfile(fetched.symbol_library, d / SYMBOLS)
        (d / COMPLETE).write_text(fetched.source + "\n", encoding="utf-8")
    files = cached_preview(cache, part_id)
    assert files is not None
    return files


def library_preview(project: Project, part: LibraryPart) -> PreviewFiles:
    """The vendored footprint and symbol of a part in the project library."""
    nickname = project.library_name
    footprints = tuple(
        project.directory / f for f in part.part.files if f.endswith(".kicad_mod")
    )
    sym: Path | None = None
    if nickname:
        sym = project.lib_dir / nickname / f"{nickname}.kicad_sym"
    return PreviewFiles(
        tuple(p for p in footprints if p.is_file()),
        sym if sym is not None and sym.is_file() else None,
        part.part.symbols[0] if part.part.symbols else None,
    )


def load_preview(files: PreviewFiles) -> Preview:
    """Drawings of the first footprint and the symbol; unreadable ones are left out."""
    footprint: Drawing | None = None
    footprint_name = ""
    for path in files.footprints[:1]:
        try:
            footprint = read_footprint_drawing(path)
            footprint_name = path.stem
        except (OSError, ValueError):
            pass
    symbol: Drawing | None = None
    symbol_name = ""
    if files.symbol_library is not None:
        try:
            library = read_symbol_library(files.symbol_library)
        except (OSError, ValueError):
            library = None
        if library is not None and library.symbols:
            symbol_name = files.symbol or library.symbols[0].name
            symbol = library_symbol_drawing(library, symbol_name)
    return Preview(footprint, footprint_name, symbol, symbol_name)


def _image_name(url: str, label: str) -> str:
    """``<sha256 of url>-<label>``: the hash keys the cache, the label (a part
    id) only tells a person listing the folder what is in there."""
    safe = "".join(c if c.isascii() and c.isalnum() else "_" for c in label)[:40]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"{digest}-{safe}" if safe else digest


def _image_suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    return ""


def load_image(catalog: Catalog, cache: Path, url: str, label: str = "") -> bytes:
    """A product image, downloaded once (raises CatalogError)."""
    folder = cache / "images"
    stem = _image_name(url, label)
    for path in sorted(folder.glob(f"{stem}*")) if folder.is_dir() else []:
        if path.is_file():
            return path.read_bytes()
    data = catalog.image(url)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}{_image_suffix(data)}").write_bytes(data)
    except OSError:
        pass  # the cache is only a convenience
    return data
