"""EasyEDA: symbols, footprints and 3D models by LCSC number (easyeda2kicad)."""

from __future__ import annotations

from kifab.core.cache import user_cache_dir
from kifab.core.net import offline_root
from kifab.libraries.base import LibrarySource, PartSource
from kifab.libraries.easyeda.fetch import (
    CachedPartSource,
    Easyeda2kicad,
    FixturePartSource,
)


class EasyedaLibrary(LibrarySource):
    name = "easyeda"
    title = "EasyEDA"
    sku_kind = "lcsc"

    def part_source(self) -> PartSource:
        root = offline_root()
        if root:
            return FixturePartSource(root)
        return CachedPartSource(Easyeda2kicad(), user_cache_dir() / "easyeda2kicad")
