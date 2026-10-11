"""The kifab icon and logo (SVG), shipped with the package."""

from __future__ import annotations

from importlib.resources import files


def read_asset(name: str) -> bytes:
    return files(__package__).joinpath(name).read_bytes()
