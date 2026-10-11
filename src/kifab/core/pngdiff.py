"""Compare two images, for telling real changes from render noise.

``kicad-cli pcb render --quality high`` is ray traced with random sampling, so
two renders of the same board differ slightly in every run.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import cast

from PIL import Image, ImageChops, UnidentifiedImageError

#: Side of the squares the images are averaged over before comparing.
BLOCK = 8
#: Largest block-averaged difference (0..255) that is still render noise.
#: Measured on ``kicad-cli pcb render --quality high``: two renders of the same
#: board peak at about 12; changing one character of a label gives about 48.
NOISE_FLOOR = 24


class ImageError(Exception):
    """Not an image Pillow can read."""


@dataclass(frozen=True)
class Difference:
    peak: int  # largest block-averaged difference anywhere, 0..255
    size_differs: bool = False

    def real(self, noise_floor: int = NOISE_FLOOR) -> bool:
        """Is this more than render noise?"""
        return self.size_differs or self.peak > noise_floor


def _load(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(str(exc)) from exc
    return image


def compare(a: bytes, b: bytes, block: int = BLOCK) -> Difference:
    """How much two images differ, as the peak over the image of the difference
    of block averages.

    Ray tracing jitters the anti-aliased edge pixels, by large amounts but
    each in a few pixels; averaging over blocks removes that while a changed
    label or part still shows. A peak anywhere counts, not an average over
    the whole image. Colour is weighted by alpha (invisible pixels carry
    random colour).
    """
    if a == b:
        return Difference(0)
    first, second = _load(a), _load(b)
    if first.size != second.size:
        return Difference(255, size_differs=True)
    blocks = [
        # "RGBa" is RGB premultiplied by alpha.
        im.convert("RGBa").reduce(block)
        for im in (first, second)
    ]
    difference = ImageChops.difference(*blocks)
    # Per band (min, max); a multi-band image would return a tuple of them.
    peaks = [cast("tuple[int, int]", b.getextrema())[1] for b in difference.split()]
    return Difference(max(peaks))
